#!/usr/bin/env python3
"""Build the family recipe box site and X preview images from recipe JSON and PDF cards."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import fitz
from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
RECIPES_DIR = ROOT / "recipes"
OUTPUT_DIR = ROOT / "site"
SITE_NAME = "Our Family Recipe Box"
KITCHEN_OF = "the Waller family"
TAGLINE_PRINTED = "These are my personal recipes"
TAGLINE_HANDWRITTEN = "from my family to yours."
SOCIAL_HANDLE = "@RegularJoe_Ceo"
DEFAULT_BASE_URL = "https://regularjoe-ceo.github.io/recipe-cards"


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def duration_minutes(recipe: dict[str, Any]) -> int | None:
    total = 0
    found = False
    for step in recipe.get("steps", []):
        raw = clean_text(step.get("duration", "")).lower()
        numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", raw)]
        if not numbers:
            continue
        value = max(numbers)
        if "hour" in raw:
            value *= 60
        total += round(value)
        found = True
    return total if found else None


def display_duration(minutes: int | None) -> str:
    if minutes is None:
        return "Step-by-step"
    if minutes >= 60:
        hours, remainder = divmod(minutes, 60)
        return f"{hours} hr {remainder} min" if remainder else f"{hours} hr"
    return f"{minutes} min"


def iso_duration(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    hours, remainder = divmod(minutes, 60)
    return f"PT{hours}H{remainder}M" if hours else f"PT{remainder}M"


def recipe_description(recipe: dict[str, Any]) -> str:
    title = clean_text(recipe.get("title", "Recipe"))
    ingredients = recipe.get("ingredients", [])
    steps = [step for step in recipe.get("steps", []) if clean_text(step.get("detail"))]
    ingredient_label = "ingredient" if len(ingredients) == 1 else "ingredients"
    step_label = "step" if len(steps) == 1 else "steps"
    return f"{title}, straight from our family recipe box: {len(ingredients)} {ingredient_label} and {len(steps)} simple {step_label}."


def servings_label(recipe: dict[str, Any]) -> str:
    servings = clean_text(recipe.get("servings"))
    if not servings:
        return ""
    return f"Serves {servings}" if re.fullmatch(r"\d+(?:\s*-\s*\d+)?", servings) else servings.capitalize()


def ingredient_line(item: dict[str, Any]) -> str:
    quantity = item.get("quantity") or {}
    parts = [clean_text(quantity.get("raw")), clean_text(quantity.get("unit")), clean_text(item.get("name"))]
    line = " ".join(part for part in parts if part)
    note = clean_text(quantity.get("note"))
    return f"{line} — {note}" if note else line


def render_pdf_page(pdf_path: Path, scale: float = 3.0) -> Image.Image:
    with fitz.open(pdf_path) as document:
        if document.page_count < 1:
            raise ValueError(f"PDF has no pages: {pdf_path}")
        pixmap = document[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def crop_to_table(page: Image.Image) -> Image.Image:
    """Detect the broad horizontal rules of the first table and exclude prose below."""
    gray = page.convert("L")
    binary = gray.point([255 if value < 225 else 0 for value in range(256)])
    width, height = binary.size
    scan_step = max(1, height // 1500)
    wide_rows: list[int] = []
    continuous_rule = bytes([255]) * max(24, round(width * 0.14))

    for y in range(0, height, scan_step):
        row = binary.crop((0, y, width, min(y + scan_step, height)))
        # Table rules contain a long uninterrupted run; prose contains separated glyphs.
        if continuous_rule in row.tobytes():
            wide_rows.append(y)

    groups: list[list[int]] = []
    max_rule_gap = max(30, round(height * 0.075))
    for row in wide_rows:
        if not groups or row - groups[-1][-1] > max_rule_gap:
            groups.append([row])
        else:
            groups[-1].append(row)
    table_groups = [group for group in groups if len(group) >= 2]

    if table_groups:
        # Grid rules form a dense cluster; a later isolated rule is usually the footer.
        table_rows = max(table_groups, key=lambda group: (len(group), group[-1] - group[0]))
        top = max(0, min(table_rows) - 18)
        bottom = min(height, max(table_rows) + 18)
        if bottom - top < height * 0.12:
            top, bottom = 0, round(height * 0.62)
    else:
        top, bottom = 0, round(height * 0.62)

    table_mask = binary.crop((0, top, width, bottom))
    content_box = table_mask.getbbox()
    if content_box:
        left = max(0, content_box[0] - 18)
        right = min(width, content_box[2] + 18)
    else:
        left, right = 0, width
    return page.crop((left, top, right, bottom))


def render_social_card(pdf_path: Path, destination: Path) -> None:
    """Render the PDF's table—not a replacement design—inside X's 1200×630 frame."""
    page = render_pdf_page(pdf_path)
    table = crop_to_table(page)
    table.thumbnail((1140, 570), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (1200, 630), (255, 255, 255))
    x = (canvas.width - table.width) // 2
    y = (canvas.height - table.height) // 2

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((x - 8, y + 5, x + table.width + 8, y + table.height + 18), radius=10, fill=(29, 42, 68, 60))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB")
    canvas.paste(table, (x, y))

    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "PNG", optimize=True)


def page_head(
    title: str,
    description: str,
    page_url: str,
    image_url: str,
    base_url: str,
    *,
    json_ld: dict[str, Any] | None = None,
) -> str:
    structured = ""
    if json_ld:
        safe_json = json.dumps(json_ld, ensure_ascii=False).replace("</", "<\\/")
        structured = f'<script type="application/ld+json">{safe_json}</script>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <meta name="description" content="{esc(description)}">
  <link rel="canonical" href="{esc(page_url)}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="{SITE_NAME}">
  <meta property="og:title" content="{esc(title)}">
  <meta property="og:description" content="{esc(description)}">
  <meta property="og:url" content="{esc(page_url)}">
  <meta property="og:image" content="{esc(image_url)}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:type" content="image/png">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:creator" content="{SOCIAL_HANDLE}">
  <meta name="twitter:title" content="{esc(title)}">
  <meta name="twitter:description" content="{esc(description)}">
  <meta name="twitter:image" content="{esc(image_url)}">
  <meta name="twitter:image:alt" content="The visual recipe table for {esc(title)}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Alegreya:ital,wght@0,400;0,500;0,700;0,800;1,400&family=Caveat:wght@500;700&family=Courier+Prime:wght@400;700&display=swap">
  <link rel="stylesheet" href="{esc(base_url)}/assets/site.css?v={CSS_VERSION}">
  {structured}
</head>"""


def site_header(base_url: str) -> str:
    return f'<header class="site-header"><a class="brand" href="{esc(base_url)}/">{SITE_NAME}</a><span class="handle">{SOCIAL_HANDLE}</span></header>'


def site_footer(base_url: str, *, link_home: bool) -> str:
    home = f'<a href="{esc(base_url)}/">Back to the recipe box</a>' if link_home else ""
    return f'<footer><p class="hand">{TAGLINE_HANDWRITTEN.capitalize()}</p><p>Print any card and keep it in your own recipe box.</p>{home}</footer>'


def build_recipe_page(recipe: dict[str, Any], slug: str, base_url: str, output: Path) -> None:
    title = clean_text(recipe.get("title", "Untitled Recipe"))
    description = recipe_description(recipe)
    page_url = f"{base_url}/recipes/{slug}/"
    image_url = f"{base_url}/assets/cards/{slug}.png"
    minutes = duration_minutes(recipe)
    ingredients = [ingredient_line(item) for item in recipe.get("ingredients", [])]
    steps = [clean_text(step.get("detail")) for step in recipe.get("steps", []) if clean_text(step.get("detail"))]
    servings = clean_text(recipe.get("servings"))

    json_ld: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": title,
        "description": description,
        "image": [image_url],
        "author": {"@type": "Person", "name": "Eric Waller"},
        "recipeIngredient": ingredients,
        "recipeInstructions": [{"@type": "HowToStep", "position": index + 1, "text": step} for index, step in enumerate(steps)],
        "url": page_url,
    }
    if servings:
        json_ld["recipeYield"] = servings
    if iso_duration(minutes):
        json_ld["totalTime"] = iso_duration(minutes)

    ingredient_html = "".join(f"<li>{esc(item)}</li>" for item in ingredients)
    step_html = "".join(
        f'<li><span class="step-number" aria-hidden="true">{index}</span><p>{esc(step)}</p></li>'
        for index, step in enumerate(steps, 1)
    )
    facts = [servings_label(recipe), f"{len(ingredients)} ingredients", f"{len(steps)} steps"]
    if minutes is not None:
        facts.append(f"About {display_duration(minutes)}")
    facts_html = "".join(f"<li>{esc(fact)}</li>" for fact in facts if fact)
    source = clean_text(recipe.get("source"))
    source_html = ""
    if source and urlparse(source).scheme in {"http", "https"}:
        source_html = f'<p class="source">Adapted from <a href="{esc(source)}" rel="nofollow">the original recipe</a>.</p>'

    head = page_head(f"{title} | {SITE_NAME}", description, page_url, image_url, base_url, json_ld=json_ld)
    body = f"""
<body>
  {site_header(base_url)}
  <main class="recipe-page">
    <a class="back" href="{esc(base_url)}/"><span aria-hidden="true">&larr;</span> Back to the recipe box</a>
    <article class="index-card recipe">
      <header class="card-top">
        <p class="printed">From the kitchen of <span class="hand">{esc(KITCHEN_OF)}</span></p>
        <h1>{esc(title)}</h1>
        <ul class="facts">{facts_html}</ul>
      </header>
      <div class="recipe-grid">
        <section class="ingredients-panel"><h2 class="hand">You'll need</h2><ul class="ingredients">{ingredient_html}</ul></section>
        <section><h2 class="hand">How to make it</h2><ol class="steps">{step_html}</ol></section>
      </div>
      {source_html}
    </article>
    <section class="glance">
      <h2 class="hand">The whole recipe on one card</h2>
      <p>Every ingredient and step laid out in a single table. Print it and stick it on the fridge.</p>
      <img class="snapshot" src="{esc(image_url)}" width="1200" height="630" alt="Recipe table for {esc(title)}">
      <div class="actions"><a class="button" href="recipe.pdf" download>Print the recipe card (PDF)</a><a class="button secondary" href="{esc(image_url)}" download>Save the sharing image</a></div>
    </section>
  </main>
  {site_footer(base_url, link_home=True)}
</body>
</html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(head + body, encoding="utf-8")


def build_index(recipes: list[tuple[str, dict[str, Any]]], base_url: str) -> None:
    description = f"{TAGLINE_PRINTED} {TAGLINE_HANDWRITTEN} Printable recipe cards from our kitchen to yours."
    default_image = f"{base_url}/assets/cards/{recipes[0][0]}.png"
    cards = []
    for slug, recipe in recipes:
        title = clean_text(recipe.get("title", "Untitled Recipe"))
        minutes = duration_minutes(recipe)
        meta = [f"{len(recipe.get('ingredients', []))} ingredients"]
        if minutes is not None:
            meta.append(display_duration(minutes))
        if servings_label(recipe):
            meta.append(servings_label(recipe))
        cards.append(f"""
        <li><a class="index-card library-card" href="{esc(base_url)}/recipes/{esc(slug)}/">
          <h2>{esc(title)}</h2>
          <p class="meta">{esc(' · '.join(meta))}</p>
          <img class="snapshot" src="{esc(base_url)}/assets/cards/{esc(slug)}.png" width="1200" height="630" loading="lazy" alt="Recipe table for {esc(title)}">
        </a></li>""")
    head = page_head(SITE_NAME, description, f"{base_url}/", default_image, base_url)
    body = f"""
<body>
  {site_header(base_url)}
  <main class="library">
    <section class="index-card hero">
      <p class="printed">From the kitchen of <span class="hand">{esc(KITCHEN_OF)}</span></p>
      <h1><span class="h1-printed">{esc(TAGLINE_PRINTED)}</span> <span class="hand">{esc(TAGLINE_HANDWRITTEN)}</span></h1>
      <p class="hero-note">Pull out a card, cook it, and make it yours.</p>
    </section>
    <h2 class="box-label hand">What's in the box</h2>
    <ul class="library-grid">{''.join(cards)}</ul>
  </main>
  {site_footer(base_url, link_home=False)}
</body>
</html>"""
    (OUTPUT_DIR / "index.html").write_text(head + body, encoding="utf-8")


CSS = r"""
:root{--ink:#1d2a44;--typed:#2e3442;--muted:#5a6478;--red:#e0382c;--red-dark:#b8281e;--sun:#ffc83d;--leaf:#2fa84f;--sky:#2b8be0;--rule:#bfdaf3;--redline:#f08a82;--card:#fff;--gap:36px;--hand:"Caveat","Bradley Hand","Segoe Print",cursive;--serif:"Alegreya",Georgia,serif;--typewriter:"Courier Prime","Courier New",monospace}
*{box-sizing:border-box}
html{background-color:#fff;background-image:linear-gradient(90deg,rgba(224,56,44,.13) 50%,transparent 50%),linear-gradient(rgba(224,56,44,.13) 50%,transparent 50%);background-size:64px 64px;color:var(--ink);font-family:var(--serif);font-size:19px;line-height:1.55}
body{margin:0}
a{color:inherit}
:focus-visible{border-radius:3px;outline:3px solid var(--sky);outline-offset:4px}
.hand{font-family:var(--hand);font-weight:700}
.site-header{align-items:baseline;background:var(--red);box-shadow:0 3px 0 var(--sun);display:flex;justify-content:space-between;padding:16px max(24px,calc((100vw - 1120px)/2));position:relative;z-index:1}
.brand{color:#fff;font-size:25px;font-weight:800;letter-spacing:-.01em;text-decoration:none}
.handle{color:#fff;font-family:var(--typewriter);font-size:14px;opacity:.9}
main{margin:auto;max-width:1120px;padding:64px 24px 40px}
.index-card{background-color:var(--card);border-radius:6px;box-shadow:0 1px 2px rgba(29,42,68,.12),0 20px 40px -18px rgba(29,42,68,.35);color:var(--ink);display:block;position:relative}
.printed{border-bottom:2px solid var(--redline);color:var(--muted);font-family:var(--typewriter);font-size:13px;letter-spacing:.08em;margin:0;padding-bottom:10px;text-transform:uppercase}
.printed .hand{color:var(--sky);font-size:30px;letter-spacing:0;line-height:1;margin-left:6px;text-transform:none}
/* hero: the note on the top card, taped down */
.hero{background-image:repeating-linear-gradient(to bottom,transparent 0 calc(var(--gap) - 1px),var(--rule) calc(var(--gap) - 1px) var(--gap));background-position:0 6px;margin:8px auto 0;max-width:980px;padding:30px 56px 40px;transform:rotate(-1.2deg)}
.hero::before,.hero::after{background:rgba(255,200,61,.85);content:"";height:34px;position:absolute;top:-14px;width:130px}
.hero::before{left:-22px;transform:rotate(-28deg)}
.hero::after{right:-22px;transform:rotate(26deg)}
.hero .printed{background:var(--card);border-radius:6px 6px 0 0;margin:-30px -56px 0;padding:30px 56px 12px}
.hero h1{font-weight:400;margin:18px 0 0}
.h1-printed{display:block;font-family:var(--serif);font-size:clamp(32px,4.4vw,52px);font-weight:800;letter-spacing:-.02em;line-height:1.15}
.hero h1 .hand{color:var(--red);display:block;font-size:clamp(44px,5.6vw,70px);line-height:1.05;margin-top:18px;transform:rotate(-2deg);transform-origin:left center}
.hero-note{color:var(--typed);font-family:var(--typewriter);font-size:16px;line-height:var(--gap);margin:22px 0 0}
.box-label{background:var(--card);border-radius:999px;box-shadow:0 6px 18px -8px rgba(29,42,68,.3);color:var(--ink);font-size:42px;font-weight:700;line-height:1;margin:96px auto 44px;padding:12px 36px 16px;text-align:center;width:max-content;max-width:100%}
/* recipe cards in the box, each with a colored divider tab */
.library-grid{display:grid;gap:60px 40px;grid-template-columns:repeat(2,minmax(0,1fr));list-style:none;margin:0;padding:0}
.library-grid li{--tab:var(--red)}
.library-grid li:nth-child(4n+2){--tab:var(--sun)}
.library-grid li:nth-child(4n+3){--tab:var(--leaf)}
.library-grid li:nth-child(4n){--tab:var(--sky)}
.library-card{border-top:6px solid var(--tab);padding:22px 26px 26px;text-decoration:none;transition:transform .25s ease,box-shadow .25s ease}
.library-card::before{background:var(--tab);border-radius:8px 8px 0 0;content:"";height:18px;left:24px;position:absolute;top:-22px;width:110px}
.library-grid li:nth-child(4n+1) .library-card{transform:rotate(-.9deg)}
.library-grid li:nth-child(4n+2) .library-card{transform:rotate(.7deg)}
.library-grid li:nth-child(4n+2) .library-card::before{left:auto;right:24px}
.library-grid li:nth-child(4n+3) .library-card{transform:rotate(.5deg)}
.library-grid li:nth-child(4n+3) .library-card::before{left:calc(50% - 55px)}
.library-grid li:nth-child(4n) .library-card{transform:rotate(-.6deg)}
.library-grid .library-card:hover,.library-grid .library-card:focus-visible{box-shadow:0 2px 3px rgba(29,42,68,.12),0 32px 52px -20px rgba(29,42,68,.45);transform:rotate(0) translateY(-6px)}
.library-card h2{border-bottom:2px solid var(--redline);font-size:32px;font-weight:800;letter-spacing:-.015em;line-height:1.15;margin:0;padding-bottom:10px}
.library-card .meta{border-bottom:1px solid var(--rule);color:var(--typed);font-family:var(--typewriter);font-size:15px;line-height:var(--gap);margin:0 0 18px}
.snapshot{border:1px solid #e6ebf2;border-radius:4px;display:block;height:auto;width:100%}
/* single recipe */
.back{background:var(--card);border-radius:999px;box-shadow:0 4px 12px -6px rgba(29,42,68,.35);color:var(--ink);display:inline-block;font-family:var(--typewriter);font-size:15px;margin-bottom:36px;padding:8px 18px;text-decoration:none}
.back:hover{color:var(--red)}
.recipe{border-top:6px solid var(--red);padding:30px 56px 40px}
.recipe::before{background:var(--red);border-radius:8px 8px 0 0;content:"";height:18px;left:48px;position:absolute;top:-22px;width:130px}
.recipe .card-top h1{font-size:clamp(44px,6.5vw,76px);font-weight:800;letter-spacing:-.025em;line-height:1.02;margin:26px 0 16px}
.facts{display:flex;flex-wrap:wrap;gap:8px;list-style:none;margin:0;padding:0}
.facts li{background:#fff4d1;border-radius:999px;color:var(--typed);font-family:var(--typewriter);font-size:15px;padding:4px 14px}
.recipe-grid{display:grid;gap:56px;grid-template-columns:minmax(240px,.85fr) minmax(320px,1.4fr);margin-top:40px}
.recipe-grid h2{color:var(--red);font-size:40px;line-height:1;margin:0 0 6px}
.recipe-grid .ingredients-panel h2{color:var(--leaf)}
.ingredients,.steps{list-style:none;margin:0;padding:0}
.ingredients li{border-bottom:1px solid var(--rule);color:var(--typed);font-family:var(--typewriter);font-size:16px;line-height:1.45;padding:9px 0}
.steps li{border-bottom:1px solid var(--rule);display:grid;gap:14px;grid-template-columns:34px 1fr;padding:12px 0}
.steps p{line-height:1.55;margin:0}
.step-number{color:var(--red);font-family:var(--hand);font-size:30px;font-weight:700;line-height:1}
.source{color:var(--muted);font-size:16px;font-style:italic;margin:34px 0 0}
.glance{background:var(--card);border-radius:6px;box-shadow:0 20px 40px -18px rgba(29,42,68,.35);margin:72px auto 0;max-width:940px;padding:36px 40px 40px;text-align:center}
.glance h2{color:var(--sky);font-size:44px;line-height:1;margin:0 0 8px}
.glance>p{color:var(--muted);margin:0 0 28px}
.actions{display:flex;flex-wrap:wrap;gap:12px;justify-content:center;margin-top:30px}
.button{background:var(--red);border:2px solid var(--red);border-radius:999px;color:#fff;font-size:17px;font-weight:700;padding:11px 22px;text-decoration:none}
.button:hover{background:var(--red-dark);border-color:var(--red-dark)}
.button.secondary{background:#fff;color:var(--red)}
.button.secondary:hover{background:#fff0ee}
footer{background:var(--red);box-shadow:0 -3px 0 var(--sun);color:#fff;font-size:16px;margin-top:72px;padding:40px 24px 48px;text-align:center}
footer p{margin:0 0 6px}
footer .hand{color:#fff;font-size:38px;line-height:1.1}
footer a{color:#fff;font-family:var(--typewriter);font-size:15px}
@media(max-width:760px){html{background-size:44px 44px;font-size:18px}main{padding-top:44px}.handle{display:none}.hero,.recipe{padding-left:24px;padding-right:24px}.hero .printed{margin-left:-24px;margin-right:-24px;padding-left:24px;padding-right:24px}.hero{transform:rotate(-.6deg)}.hero::before,.hero::after{width:90px}.library-grid,.recipe-grid{grid-template-columns:1fr}.recipe-grid{gap:32px}.box-label{font-size:34px;margin-top:72px}.library-card h2{font-size:28px}.glance{padding:28px 20px 32px}}
@media(prefers-reduced-motion:reduce){.library-card{transition:none}.library-grid .library-card:hover{transform:none}}
"""


CSS_VERSION = hashlib.sha256(CSS.strip().encode("utf-8")).hexdigest()[:10]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    (OUTPUT_DIR / "assets" / "cards").mkdir(parents=True)
    (OUTPUT_DIR / "assets" / "site.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    (OUTPUT_DIR / ".nojekyll").write_text("", encoding="utf-8")

    recipes: list[tuple[str, dict[str, Any]]] = []
    for json_path in sorted(RECIPES_DIR.glob("*/recipe.json")):
        recipe = json.loads(json_path.read_text(encoding="utf-8"))
        recipe_dir = json_path.parent
        pdf_path = recipe_dir / "recipe.pdf"
        if not pdf_path.exists():
            raise FileNotFoundError(f"Missing recipe PDF for {recipe_dir.name}: {pdf_path}")

        slug = recipe_dir.name
        recipes.append((slug, recipe))
        render_social_card(pdf_path, OUTPUT_DIR / "assets" / "cards" / f"{slug}.png")
        destination = OUTPUT_DIR / "recipes" / slug
        build_recipe_page(recipe, slug, base_url, destination / "index.html")
        for filename in ("recipe.pdf", "recipe.md", "recipe.json"):
            source = recipe_dir / filename
            if source.exists():
                shutil.copy2(source, destination / filename)

    recipes.sort(key=lambda item: clean_text(item[1].get("created_at")), reverse=True)
    if not recipes:
        raise RuntimeError("No recipes found under recipes/*/recipe.json")
    build_index(recipes, base_url)

    urls = [f"{base_url}/"] + [f"{base_url}/recipes/{slug}/" for slug, _ in recipes]
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(f"  <url><loc>{esc(url)}</loc></url>\n" for url in urls) + "</urlset>\n"
    (OUTPUT_DIR / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    (OUTPUT_DIR / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n", encoding="utf-8")
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "recipes": len(recipes)}
    (OUTPUT_DIR / "build.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(recipes)} recipe pages and PDF-derived social cards in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
