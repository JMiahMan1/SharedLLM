"""An image CI just pushed is "deploying", not "update available", for a while."""
import ast
import os


def _settle():
    with open(os.path.join(os.path.dirname(__file__), "..", "main.py")) as f:
        src = f.read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "settle_update")
    ns: dict = {"UPDATE_SETTLE_SECONDS": 600.0}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "main.py", "exec"), ns)
    return ns["settle_update"]


def test_new_image_waits_before_it_is_reported():
    settle, seen = _settle(), {}
    assert settle("gateway", "sha:new", 1000.0, seen) == (False, 0.0)
    assert settle("gateway", "sha:new", 1300.0, seen)[0] is False
    assert settle("gateway", "sha:new", 1600.0, seen) == (True, 600.0)


def test_a_newer_push_restarts_the_wait():
    settle, seen = _settle(), {}
    settle("ui", "sha:a", 0.0, seen)
    assert settle("ui", "sha:b", 700.0, seen) == (False, 0.0)
    assert ("ui", "sha:a") not in seen
