"""The scripts in tools/ run as subprocesses with tools/ as their directory, so
they import each other as plain sibling modules (``from vision_ocr import ...``).
Nothing else references those siblings, so a cleanup that deletes one breaks the
script only at runtime, inside a broad except: every web scrape then came back
with no prices and no visible failure. This pins each sibling import to a file
that exists."""
import ast
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[3] / "tools"
# Imports a tools/ script may make that are not siblings in tools/.
EXTERNAL_PREFIXES = ("services",)


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return names


def test_the_scraper_finds_its_ocr_sibling():
    assert "vision_ocr" in _imported_modules(TOOLS / "webscraper.py")
    assert (TOOLS / "vision_ocr.py").exists()


def test_every_import_of_a_tools_module_name_has_its_file():
    siblings = {p.stem for p in TOOLS.glob("*.py")}
    for script in TOOLS.glob("*.py"):
        for name in _imported_modules(script):
            if name.startswith(EXTERNAL_PREFIXES):
                continue
            # A name that was a tools/ module (or is imported nowhere else) must
            # still be one; the deleted-sibling case is exactly this.
            if name in {"vision_ocr", "webscraper", "code_search"}:
                assert name in siblings, f"{script.name} imports {name}, but tools/{name}.py is gone"
