#!/usr/bin/env python3
"""Build recipe pages and X preview images from recipe JSON and PDF cards."""

from __future__ import annotations

import argparse
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
SITE_NAME = "Recipe Cards"
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
    step_label = "step" if len(steps) == 1 else "steps"
    return f"Make {title} from its visual recipe card: {len(ingredients)} ingredients and {len(steps)} practical {step_label}."


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

    canvas = Image.new("RGB", (1200, 630), (247, 243, 236))
    x = (canvas.width - table.width) // 2
    y = (canvas.height - table.height) // 2

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((x - 8, y + 5, x + table.width + 8, y + table.height + 18), radius=10, fill=(35, 28, 22, 72))
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
  <link rel="stylesheet" href="{esc(base_url)}/assets/site.css">
  {structured}
</head>"""


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
        f'<li><span class="step-number">{index}</span><p>{esc(step)}</p></li>'
        for index, step in enumerate(steps, 1)
    )
    pdf_link = '<a class="button secondary" href="recipe.pdf" download>Download original PDF</a>'
    source = clean_text(recipe.get("source"))
    source_html = ""
    if source and urlparse(source).scheme in {"http", "https"}:
        source_html = f'<p class="source">Adapted from <a href="{esc(source)}" rel="nofollow">the original source</a>.</p>'

    head = page_head(title, description, page_url, image_url, base_url, json_ld=json_ld)
    body = f"""
<body>
  <header class="site-header"><a href="{esc(base_url)}/">{SITE_NAME}</a><span>{SOCIAL_HANDLE}</span></header>
  <main>
    <article class="recipe">
      <div class="hero-copy">
        <p class="eyebrow">VISUAL RECIPE CARD</p>
        <h1>{esc(title)}</h1>
        <p class="lede">{esc(description)}</p>
        <div class="stats"><span>{len(ingredients)} ingredients</span><span>{len(steps)} steps</span><span>{esc(display_duration(minutes))}</span>{f'<span>{esc(servings)}</span>' if servings else ''}</div>
        <div class="actions">{pdf_link}<a class="button" href="{esc(image_url)}" download>Download sharing image</a></div>
      </div>
      <img class="hero-card" src="{esc(image_url)}" width="1200" height="630" alt="Visual recipe table for {esc(title)}">
      <div class="recipe-grid">
        <section><h2>Ingredients</h2><ul class="ingredients">{ingredient_html}</ul></section>
        <section><h2>Method</h2><ol class="steps">{step_html}</ol></section>
      </div>
      {source_html}
    </article>
  </main>
  <footer><a href="{esc(base_url)}/">Browse every recipe</a></footer>
</body>
</html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(head + body, encoding="utf-8")


def build_index(recipes: list[tuple[str, dict[str, Any]]], base_url: str) -> None:
    description = "A growing library of visual recipe tables, preserved exactly from their printable cards."
    default_image = f"{base_url}/assets/cards/{recipes[0][0]}.png"
    cards = []
    for slug, recipe in recipes:
        title = clean_text(recipe.get("title", "Untitled Recipe"))
        cards.append(f"""
        <a class="library-card" href="{esc(base_url)}/recipes/{esc(slug)}/">
          <img src="{esc(base_url)}/assets/cards/{esc(slug)}.png" width="1200" height="630" alt="Visual recipe table for {esc(title)}">
          <div><h2>{esc(title)}</h2><p>{len(recipe.get('ingredients', []))} ingredients · {esc(display_duration(duration_minutes(recipe)))}</p></div>
        </a>""")
    head = page_head(SITE_NAME, description, f"{base_url}/", default_image, base_url)
    body = f"""
<body>
  <header class="site-header"><a href="{esc(base_url)}/">{SITE_NAME}</a><span>{SOCIAL_HANDLE}</span></header>
  <main class="library">
    <div class="library-intro"><p class="eyebrow">THE RECIPE LIBRARY</p><h1>Cook from the card.</h1><p>{esc(description)}</p></div>
    <div class="library-grid">{''.join(cards)}</div>
  </main>
  <footer>New pages and X preview images are generated automatically from each recipe PDF.</footer>
</body>
</html>"""
    (OUTPUT_DIR / "index.html").write_text(head + body, encoding="utf-8")


CSS = r"""
:root{--ink:#181512;--paper:#f7f1e8;--accent:#d95b34;--line:#d8cbbb;--muted:#675f56}*{box-sizing:border-box}html{background:var(--paper);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}body{margin:0}.site-header{align-items:center;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;padding:20px max(24px,calc((100vw - 1180px)/2));position:relative}.site-header a{color:var(--ink);font-size:21px;font-weight:850;letter-spacing:-.02em;text-decoration:none}.site-header span,footer{color:var(--muted);font-size:14px}main{margin:auto;max-width:1180px;padding:72px 24px}.recipe{display:grid;gap:42px}.hero-copy{max-width:880px}.eyebrow{color:var(--accent);font-size:13px;font-weight:850;letter-spacing:.16em;margin:0 0 14px}.hero-copy h1,.library-intro h1{font-family:Georgia,serif;font-size:clamp(52px,8vw,96px);letter-spacing:-.055em;line-height:.98;margin:0;max-width:950px}.lede,.library-intro>p:last-child{color:var(--muted);font-family:Georgia,serif;font-size:22px;line-height:1.5;max-width:740px}.stats{display:flex;flex-wrap:wrap;gap:10px;margin-top:25px}.stats span{border:1px solid var(--line);border-radius:100px;font-size:13px;font-weight:750;padding:10px 15px}.actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:26px}.button{background:var(--ink);border:1px solid var(--ink);border-radius:8px;color:#fff;font-size:14px;font-weight:750;padding:12px 17px;text-decoration:none}.button.secondary{background:transparent;color:var(--ink)}.hero-card{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:0 30px 70px #3b2e2026;display:block;height:auto;width:100%}.recipe-grid{display:grid;gap:70px;grid-template-columns:minmax(260px,.8fr) minmax(360px,1.4fr);margin-top:22px}.recipe-grid h2{font-family:Georgia,serif;font-size:38px;letter-spacing:-.03em}.ingredients{list-style:none;margin:0;padding:0}.ingredients li{border-top:1px solid var(--line);line-height:1.45;padding:14px 0}.steps{list-style:none;margin:0;padding:0}.steps li{border-top:1px solid var(--line);display:grid;gap:16px;grid-template-columns:42px 1fr;padding:18px 0}.steps p{line-height:1.6;margin:3px 0}.step-number{align-items:center;background:var(--ink);border-radius:50%;color:white;display:flex;font-size:13px;font-weight:800;height:32px;justify-content:center;width:32px}.source{border-top:1px solid var(--line);color:var(--muted);font-size:13px;padding-top:22px}.source a,footer a{color:inherit}.library{padding-top:90px}.library-intro{margin-bottom:55px;max-width:840px}.library-grid{display:grid;gap:28px;grid-template-columns:repeat(2,minmax(0,1fr))}.library-card{background:#fff;border:1px solid var(--line);border-radius:18px;color:var(--ink);overflow:hidden;text-decoration:none;transition:transform .18s ease,box-shadow .18s ease}.library-card:hover{box-shadow:0 22px 48px #3b2e201c;transform:translateY(-4px)}.library-card img{display:block;height:auto;width:100%}.library-card div{padding:20px 22px 24px}.library-card h2{font-family:Georgia,serif;font-size:28px;letter-spacing:-.025em;margin:0 0 8px}.library-card p{color:var(--muted);margin:0}footer{border-top:1px solid var(--line);margin:auto;max-width:1180px;padding:28px 24px 48px}@media(max-width:760px){main{padding-top:48px}.recipe-grid,.library-grid{grid-template-columns:1fr}.recipe-grid{gap:25px}.hero-copy h1,.library-intro h1{font-size:52px}.site-header span{display:none}}
"""


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
