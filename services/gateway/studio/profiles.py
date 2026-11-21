"""What kind of project is in this workspace, and how do you build and test it?

A workspace is a folder, not a language: it may hold a Python package, a Vite
app, a Go service, a Rust crate, a Maven module, a .NET solution, or just a
folder of Markdown and pictures. The studio has to know which, because "run the
tests" means something different in each and guessing wastes a sandbox run.

Detection reads a flat list of file names (what ``/files/list`` returns) and
nothing else. It is pure, so the interesting cases -- two stacks in one folder, a
half-installed project, an empty workspace -- are testable without a container.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Step:
    """A step the studio can run on request.

    ``command`` is one shell line that already runs inside the workspace: the
    sandbox executes it with the workspace as its working directory.
    """

    name: str  # install | test | build | lint | serve
    command: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Profile:
    """One detected stack, with the steps that make sense for it."""

    stack: str
    label: str
    language: str
    steps: dict[str, Step] = field(default_factory=dict)
    preview_port: int | None = None
    entry: str = ""  # the file that made it this stack
    notes: list[str] = field(default_factory=list)

    def command_for(self, action: str) -> Step | None:
        return self.steps.get(action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stack": self.stack,
            "label": self.label,
            "language": self.language,
            "entry": self.entry,
            "preview_port": self.preview_port,
            "notes": self.notes,
            "steps": {name: step.to_dict() for name, step in self.steps.items()},
        }


def _names(entries: list[dict[str, Any]] | list[str]) -> set[str]:
    """The workspace's file names (flat)."""
    names: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            names.add(entry)
            continue
        for key in ("name", "path", "relative_path"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                names.add(value)
                break
    return names


def _has(names: set[str], *candidates: str) -> str:
    """The first candidate present, or ``""``."""
    lowered = {name.lower(): name for name in names}
    for candidate in candidates:
        found = lowered.get(candidate.lower())
        if found:
            return found
    return ""


def _suffix(names: set[str], suffix: str) -> str:
    for name in sorted(names):
        if name.lower().endswith(suffix):
            return name


def _python(names: set[str]) -> Profile | None:
    entry = _has(names, "pyproject.toml", "requirements.txt", "setup.py", "Pipfile", "poetry.lock")
    if not entry:
        return None
    steps = {
        "test": Step("test", "python -m pytest -q", "the project's own pytest suite"),
        "build": Step("build", "python -m compileall -q .", "compile every module (a fast syntax gate)"),
    }
    if _has(names, "requirements.txt"):
        steps["install"] = Step("install", "pip install -q -r requirements.txt", "install the declared packages")
    elif _has(names, "pyproject.toml"):
        steps["install"] = Step(
            "install",
            "pip install -q -e . || pip install -q -r requirements.txt || true",
            "editable install when the project supports it",
        )
    return Profile("python", "Python", "python", steps, entry=entry)


def _node(names: set[str]) -> Profile | None:
    entry = _has(names, "package.json")
    if not entry:
        return None
    lock = _has(names, "pnpm-lock.yaml", "yarn.lock", "package-lock.json")
    runner = "pnpm" if lock == "pnpm-lock.yaml" else "yarn" if lock == "yarn.lock" else "npm"
    install = (
        "pnpm install"
        if runner == "pnpm"
        else "yarn install"
        if runner == "yarn"
        else "npm install --no-audit --no-fund"
    )
    steps = {
        "install": Step("install", install, f"install dependencies with {runner}"),
        "test": Step("test", f"{runner} test", "the project's test script"),
        "build": Step("build", f"{runner} run build", "the project's build script"),
        "lint": Step("lint", f"{runner} run lint", "the project's lint script"),
    }
    if _has(names, "vite.config.ts", "vite.config.js", "vite.config.mjs"):
        steps["serve"] = Step("serve", f"{runner} run dev -- --host 0.0.0.0", "Vite dev server on 0.0.0.0")
        return Profile("node-vite", "Node (Vite)", "javascript", steps, preview_port=5173, entry=entry)
    if _has(names, "next.config.js", "next.config.mjs", "next.config.ts"):
        steps["serve"] = Step("serve", f"{runner} run dev", "Next.js dev server")
        return Profile("node-next", "Node (Next.js)", "javascript", steps, preview_port=3000, entry=entry)
    steps["serve"] = Step("serve", f"{runner} start", "the project's start script")
    return Profile("node", "Node", "javascript", steps, preview_port=3000, entry=entry)


def _go(names: set[str]) -> Profile | None:
    entry = _has(names, "go.mod")
    if not entry:
        return None
    return Profile(
        "go",
        "Go",
        "go",
        {
            "install": Step("install", "go mod download", "download modules"),
            "test": Step("test", "go test ./...", "the module's tests"),
            "build": Step("build", "go build ./...", "compile every package"),
            "serve": Step("serve", "go run .", "run the main package"),
        },
        preview_port=8080,
        entry=entry,
    )


def _rust(names: set[str]) -> Profile | None:
    entry = _has(names, "cargo.toml")
    if not entry:
        return None
    return Profile(
        "rust",
        "Rust",
        "rust",
        {
            "test": Step("test", "cargo test", "the crate's tests"),
            "build": Step("build", "cargo build", "a debug build"),
            "lint": Step("lint", "cargo clippy -- -D warnings", "clippy"),
            "serve": Step("serve", "cargo run", "run the binary"),
        },
        preview_port=8080,
        entry=entry,
    )


def _java(names: set[str]) -> Profile | None:
    entry = _has(names, "pom.xml", "build.gradle", "build.gradle.kts")
    if not entry:
        return None
    if entry.lower() == "pom.xml":
        return Profile(
            "java-maven",
            "Java (Maven)",
            "java",
            {
                "test": Step("test", "mvn -q test", "Maven tests"),
                "build": Step("build", "mvn -q package -DskipTests", "package the artefact"),
            },
            entry=entry,
        )
    gradle = "./gradlew" if _has(names, "gradlew") else "gradle"
    return Profile(
        "java-gradle",
        "Java (Gradle)",
        "java",
        {
            "test": Step("test", f"{gradle} test", "Gradle tests"),
            "build": Step("build", f"{gradle} build -x test", "build without tests"),
        },
        entry=entry,
    )


def _dotnet(names: set[str]) -> Profile | None:
    entry = _suffix(names, ".sln") or _suffix(names, ".csproj")
    if not entry:
        return None
    return Profile(
        "dotnet",
        ".NET",
        "csharp",
        {
            "install": Step("install", "dotnet restore", "restore packages"),
            "test": Step("test", "dotnet test", "the solution's tests"),
            "build": Step("build", "dotnet build -c Release", "a release build"),
        },
        entry=entry,
    )


def _static(names: set[str]) -> Profile | None:
    entry = _has(names, "index.html")
    if not entry:
        return None
    return Profile(
        "static",
        "Static site",
        "html",
        {
            "serve": Step("serve", "python3 -m http.server 8080 --bind 0.0.0.0", "a plain file server"),
        },
        preview_port=8080,
        entry=entry,
        notes=["no build step: index.html is served as-is"],
    )


_DETECTORS = (_node, _python, _go, _rust, _java, _dotnet, _static)

#: What to say when nothing matches: a folder of text and pictures is valid work.
UNKNOWN = Profile(
    "unknown",
    "A workspace of files",
    "",
    {},
    notes=[
        "no project file found (package.json, pyproject.toml, go.mod, Cargo.toml, "
        "pom.xml, *.csproj, index.html)",
        "a deck, a game or a plain folder of documents still works",
    ],
)


def detect(entries: list[dict[str, Any]] | list[str]) -> Profile:
    """The workspace's stack.

    Node is checked before Python on purpose: a Vite app often carries a stray
    ``requirements.txt`` for a helper script and never the other way round.
    """
    names = _names(entries)
    for detector in _DETECTORS:
        profile = detector(names)
        if profile is not None:
            return profile
    return UNKNOWN


def all_stacks(entries: list[dict[str, Any]] | list[str]) -> list[str]:
    """Every stack present (a repo with a Python service and a Vite app has two)."""
    names = _names(entries)
    found: list[str] = []
    for detector in _DETECTORS:
        profile = detector(names)
        if profile is not None and profile.stack not in found:
            found.append(profile.stack)
    return found


def slug(text: str, *, fallback: str = "untitled") -> str:
    """A file-safe name for a generated folder."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return cleaned[:60].strip("-") or fallback
