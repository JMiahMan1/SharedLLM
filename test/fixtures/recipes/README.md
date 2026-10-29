# Recipe import test photos

Real pictures used by the recipe / food-label import tests. They are kept here
rather than generated so the tests exercise the actual bytes a person uploads.

| File | What it is | Used by |
| --- | --- | --- |
| `sugar-cookies-card.png` | A handwritten recipe card ("Sugar Cookies  Ann.", blue ink on lined paper, 993×600). | `services/gateway/tests/test_recipe_gateway.py`, `services/execution/tests/test_ocr_image_edit_handlers.py`, `test/local/test_recipe_ocr_live.py` |
| `food-label.webp` | A photo of a yogurt Nutrition Facts panel (1920×1440). | `services/gateway/tests/test_recipe_gateway.py`, `test/local/test_recipe_ocr_live.py` |

The unit tests stub the vision model and assert the parsing and the upload path
(the real bytes travel, base64-encoded, to `/execute/ocr`). The `local_only`
live tests in `test/local/test_recipe_ocr_live.py` run the configured model
against these same files and skip when it is not available.

Source: the household's own photos (handed over on the Desktop as
`Sugar-Cookies.png` and `FoodLabel.webp`). Keep them small and few.
