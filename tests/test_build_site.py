import importlib.util
import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_site.py"
SPEC = importlib.util.spec_from_file_location("build_site", MODULE_PATH)
build_site = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(build_site)


class BuildSiteTests(unittest.TestCase):
    def test_duration_and_ingredient_formatting(self):
        recipe = {
            "steps": [
                {"duration": "20 minutes"},
                {"duration": "1 hour"},
                {"duration": "5-8 minutes"},
            ]
        }
        self.assertEqual(build_site.duration_minutes(recipe), 88)
        self.assertEqual(build_site.display_duration(88), "1 hr 28 min")
        ingredient = {"name": "sweet potatoes", "quantity": {"raw": "2", "unit": "large", "note": "diced"}}
        self.assertEqual(build_site.ingredient_line(ingredient), "2 large sweet potatoes — diced")

    def test_description_is_recipe_specific(self):
        recipe = {"title": "Masa Biscuits", "ingredients": [{}, {}], "steps": [{"detail": "Mix."}]}
        self.assertEqual(
            build_site.recipe_description(recipe),
            "Make Masa Biscuits from its visual recipe card: 2 ingredients and 1 practical step.",
        )

    def test_pdf_table_becomes_social_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            pdf_path = target / "recipe.pdf"
            png_path = target / "social.png"

            document = fitz.open()
            page = document.new_page(width=612, height=792)
            page.draw_rect(fitz.Rect(40, 40, 572, 350), width=2)
            for y in (100, 170, 240, 310):
                page.draw_line(fitz.Point(40, y), fitz.Point(572, y), width=1)
            for x in (180, 340, 470):
                page.draw_line(fitz.Point(x, 40), fitz.Point(x, 350), width=1)
            page.insert_text(fitz.Point(50, 70), "MASA BISCUITS", fontsize=18)
            page.insert_text(fitz.Point(50, 500), "This explanatory text should not be in the social image.", fontsize=11)
            document.save(pdf_path)
            document.close()

            build_site.render_social_card(pdf_path, png_path)
            with Image.open(png_path) as image:
                self.assertEqual(image.size, (1200, 630))
                self.assertEqual(image.format, "PNG")
            self.assertGreater(png_path.stat().st_size, 5_000)

    def test_page_contains_social_and_recipe_metadata(self):
        recipe = {
            "title": "Masa Biscuits",
            "servings": "12 biscuits",
            "ingredients": [{"name": "masa harina", "quantity": {"raw": "1", "unit": "cup", "note": ""}}],
            "steps": [{"detail": "Mix and bake until golden.", "duration": "15 minutes"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            page = Path(temporary) / "index.html"
            build_site.build_recipe_page(recipe, "masa-biscuits", "https://example.com/recipes", page)
            markup = page.read_text(encoding="utf-8")
            self.assertIn('content="summary_large_image"', markup)
            self.assertIn("https://example.com/recipes/assets/cards/masa-biscuits.png", markup)
            self.assertIn('"@type": "Recipe"', markup)


if __name__ == "__main__":
    unittest.main()
