"""Every `/execute/…` endpoint must say what its body is.

FastAPI is quiet when a handler parameter has no type: it treats the name as a
required **query** parameter. That is how "Put me to bed" answered with

    422 {"loc": ["query", "req"], "msg": "Field required"}

- the night-mode composite received its JSON, looked for a `?req=` in the query
string, and rejected the request before the handler ever ran. The endpoint had
been broken since it was written, and nothing failed because no test posts to it.

This test is the cheap, permanent version of that lesson: read the module and
refuse any `/execute/…` route whose parameters are not all annotated.
"""
from __future__ import annotations

import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parents[2] / "services/execution/main.py"

# Parameters FastAPI injects by type rather than by body, so they are still
# expected to be annotated - but a bare `Request` is the only thing it can fill
# from the ASGI scope, and even that has to be declared.
FRAMEWORK_OK = {"request", "background_tasks", "response"}


def _routes(tree: ast.Module) -> list[tuple[str, ast.AsyncFunctionDef | ast.FunctionDef]]:
    found: list[tuple[str, ast.AsyncFunctionDef | ast.FunctionDef]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) or not node.decorator_list:
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute) or func.attr not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            path = decorator.args[0].value
            if isinstance(path, str) and path.startswith("/execute"):
                found.append((path, node))
    return found


def test_every_execute_route_declares_its_body() -> None:
    tree = ast.parse(MAIN.read_text())
    routes = _routes(tree)
    # A floor, so a refactor that stops matching decorators cannot silently pass.
    assert len(routes) > 80, f"only found {len(routes)} /execute routes in {MAIN}"

    untyped: list[str] = []
    for path, node in routes:
        args = node.args.args[1:] if node.args.args and node.args.args[0].arg in {"self", "cls"} else node.args.args
        for arg in args:
            if arg.arg in {"self", "cls"}:
                continue
            if arg.annotation is None:
                untyped.append(f"{path} -> {node.name}({arg.arg})")

    assert not untyped, "these /execute routes would read their body as a query parameter:\n  " + "\n  ".join(untyped)


def test_the_composite_handlers_name_their_schemas() -> None:
    """The two that were actually broken, pinned by name."""
    source = MAIN.read_text()
    assert "async def execute_composite_night_mode(req: NightModeRequest)" in source
    assert "async def execute_composite_broadcast(req: DocumentBroadcastRequest)" in source
