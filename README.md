# Recipe Cards

The recipes in this repository are automatically published as a visual recipe library at:

**https://regularjoe-ceo.github.io/recipe-cards/**

## Add a recipe

Add a folder under `recipes/` containing `recipe.json` and `recipe.pdf`; `recipe.md` is optional. When the change reaches `main`, GitHub Actions automatically:

1. generates a responsive recipe page;
2. renders the table portion of `recipe.pdf` into a 1200×630 PNG sharing card, preserving the recipe card's original design and excluding the prose below the table;
3. adds Open Graph, X Card, canonical and Recipe structured-data metadata;
4. rebuilds the library, sitemap and downloadable files; and
5. publishes the result with GitHub Pages.

The public URL follows this pattern:

`https://regularjoe-ceo.github.io/recipe-cards/recipes/<recipe-folder>/`

Share that `github.io` URL on X—not the `github.com` source-code URL—to receive the large preview card.

## Build locally

```bash
python -m pip install -r requirements.txt
python scripts/build_site.py
```

The generated site is written to `site/` and is intentionally not committed. A recipe must include both `recipe.json` and `recipe.pdf`; the build fails rather than publishing a generic or incorrect sharing image when the PDF is missing.
