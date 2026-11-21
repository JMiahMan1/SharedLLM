"""Derive the evaluation and training curriculum from the features Jarvis has.

Three sources feed one list of :class:`Case` items:

* **Seeds** -- the household's real work, hand-written and objectively checked:
  fixing its own code, writing a sermon from a passage, turning a photo request
  into a valid image-edit call, citing a lesson, staying inside a small context
  window. These are the use cases the trainer is responsible for teaching.
* **Tool cases** -- one per tool in ``tool_registry._RAVEN_TOOL_TABLE``: given
  the tool's own summary, the model must name the tool. Adding a tool adds a
  case. The answer is checkable with no model in the loop.
* **Route cases** -- the Workspace API recipes (read, write, test, commit,
  pull-on-push, terminal) and one service-ownership case per service, all read
  from :mod:`services.gateway.trainer.catalog`.

Every case is scored by string/regex checks only, so a run is deterministic and
comparable over time; the model's latency and length are recorded separately.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from services.gateway.trainer import catalog as catalog_mod

#: Stable bucket names, narrowest first. The UI and the backlog use these.
BUCKETS = (
    "repair",
    "writing",
    "vision",
    "workspace",
    "memory",
    "protocol",
    "tools",
    "knowledge",
    "teacher",
    "raven_failures",
)

DEFAULT_SYSTEM = (
    "You are Jarvis, the household's own assistant, running on a small local model. "
    "Answer plainly. When a tool or endpoint is needed, name it exactly as it is "
    "written in the documentation. Respect any length limit you are given."
)


@dataclass(frozen=True)
class Case:
    """One prompt, with checks that decide whether an answer counts as correct."""

    id: str
    bucket: str
    title: str
    prompt: str
    checks: dict[str, Any]
    system: str = DEFAULT_SYSTEM
    source: str = "seed"
    difficulty: int = 1
    max_tokens: int = 320
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        return data


def _case_id(prefix: str, text: str) -> str:
    """Stable id: the same case keeps its id across runs and machines."""
    return f"{prefix}-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"


def _seed_cases() -> list[Case]:
    """The household's real use cases, with checks a script can trust."""
    return [
        Case(
            id="seed-repair-python-bug",
            bucket="repair",
            title="Fix an off-by-one bug from the code alone",
            difficulty=1,
            max_tokens=420,
            tags=("repair", "python"),
            prompt=(
                "This function is meant to return the mean of a list of numbers, but it is wrong:\n\n"
                "```python\n"
                "def mean(values):\n"
                "    return sum(values) / len(values) + 1\n"
                "```\n\n"
                "Reply with only the corrected function inside one ```python block. No explanation."
            ),
            checks={
                "must_include": ["```python", "def mean"],
                "must_not_match": [r"len\(values\)\s*\+\s*1"],
            },
        ),
        Case(
            id="seed-repair-traceback-cause",
            bucket="repair",
            title="Name the root cause of a real traceback",
            difficulty=2,
            max_tokens=260,
            tags=("repair", "diagnosis"),
            prompt=(
                "A mission failed with this traceback. Name the root cause and the exact fix, "
                "in at most three sentences.\n\n"
                "Traceback (most recent call last):\n"
                "  File \"agent.py\", line 42, in run_tool\n"
                "    result = await client.post(url, json=payload, headers=headers, timeout=30)\n"
                "TypeError: ClientTimeout() takes no arguments"
            ),
            checks={
                "must_match": [r"(?i)ClientTimeout", r"(?i)total\s*="],
                "max_chars": 900,
            },
        ),
        Case(
            id="seed-writing-sermon-outline",
            bucket="writing",
            title="Outline a sermon for a Sunday congregation",
            difficulty=1,
            max_tokens=520,
            tags=("writing", "sermon"),
            prompt=(
                "Write a sermon outline on Romans 8:28 for a Sunday congregation. "
                "Include, on labelled lines: a title, the big idea, three points, and one "
                "application. No preamble, no closing remarks."
            ),
            checks={
                "must_match": [
                    r"(?i)title",
                    r"(?i)big idea",
                    r"(?i)point",
                    r"(?i)application",
                    r"Romans 8:28",
                ],
                "max_chars": 2600,
            },
        ),
        Case(
            id="seed-writing-scripture-recall",
            bucket="writing",
            title="Quote the verse a sermon is built on",
            difficulty=2,
            max_tokens=200,
            tags=("writing", "sermon", "scripture"),
            prompt=(
                "Quote John 3:16 from memory, then say in one sentence why it anchors a sermon "
                "on God's love. Plain text."
            ),
            checks={
                "must_match": [r"(?i)John 3:16", r"(?i)so loved the world"],
                "max_chars": 1200,
            },
        ),
        Case(
            id="seed-vision-image-edit",
            bucket="vision",
            title="Turn a photo request into a valid image-edit call",
            difficulty=2,
            max_tokens=300,
            tags=("vision", "images", "workspace"),
            prompt=(
                "Turn this request into one JSON tool call for the image edit tool, using only "
                "fields that tool accepts: \"make the photo of the church look like a watercolour "
                "painting, keeping the faces the same\".\n\n"
                "The tool is ImageEditRequest. Reply with JSON only."
            ),
            checks={
                "must_include": ["image_path", "prompt", "watercolour"],
                "must_not_match": [r'"image"\s*:', r'"face_image_path"'],
                "max_chars": 700,
            },
        ),
        Case(
            id="seed-workspace-write-tool",
            bucket="workspace",
            title="Save a sermon into the workspace with the right tool",
            difficulty=2,
            max_tokens=420,
            tags=("workspace", "writing"),
            prompt=(
                "You must save a finished sermon as sermons/sunday.md inside workspace "
                "\"sermons\". Name the single tool call you use and the fields it needs, in a "
                "short JSON object. Do not describe the sermon."
            ),
            checks={
                "must_include": ["WorkspaceFileWriteRequest", "path", "content"],
                "max_chars": 900,
            },
        ),
        Case(
            id="seed-memory-apply-citation",
            bucket="memory",
            title="Cite an applied lesson exactly as the protocol requires",
            difficulty=1,
            max_tokens=120,
            tags=("memory", "protocol"),
            prompt=(
                "You applied the stored lesson with id lesson-abc123. Write the exact citation "
                "line the protocol requires, and nothing else."
            ),
            checks={"must_include": ["Apply: [lesson-abc123]"], "max_chars": 200},
        ),
        Case(
            id="seed-memory-dream-passes",
            bucket="memory",
            title="Explain what dreaming does to lessons",
            difficulty=2,
            max_tokens=320,
            tags=("memory", "rag"),
            prompt=(
                "In three short sentences, explain what a dreaming pass does to Raven's stored "
                "lessons. Name each pass."
            ),
            checks={
                "must_match": [r"(?i)compact", r"(?i)merge", r"(?i)prune"],
                "max_chars": 1400,
            },
        ),
        Case(
            id="seed-protocol-workspace-first",
            bucket="protocol",
            title="Start every mission in its own workspace",
            difficulty=1,
            max_tokens=300,
            tags=("protocol", "workspace"),
            prompt=(
                "You are asked to build a small command line tool from scratch. What is your "
                "very first tool call, and which workspace do you work in? Two sentences."
            ),
            checks={
                "must_include": ["WorkspaceCreateRequest"],
                "must_match": [r"(?i)dedicated|its own|own workspace"],
                "max_chars": 700,
            },
        ),
        Case(
            id="seed-hardware-budget",
            bucket="knowledge",
            title="Explain a service inside a small token budget",
            difficulty=1,
            max_tokens=260,
            tags=("knowledge", "hardware"),
            prompt=(
                "Explain what the Workspace Runtime service does, in plain English for a family "
                "member, under 90 words."
            ),
            checks={"must_match": [r"(?i)workspace"], "max_chars": 700},
        ),
        Case(
            id="seed-shell-home-eacces",
            bucket="workspace",
            title="Diagnose a shell that cannot write its own cache",
            difficulty=2,
            max_tokens=300,
            tags=("workspace", "shell", "diagnosis"),
            prompt=(
                "In a workspace, `npm test` fails with:\n\n"
                "npm error code EACCES\n"
                "npm error Path: /home/sharedllm/.npm\n\n"
                "The workspace shell itself runs other commands fine. "
                "Name the cause and the fix in at most three sentences."
            ),
            checks={
                # The model has to reach for the diagnosis tool (StudioDiagnoseRequest,
                # which it can call), not guess a chmod.
                "must_include": ["StudioDiagnoseRequest"],
                "must_match": [r"(?i)HOME"],
                "must_not_match": [r"(?i)chmod\s+777"],
                "max_chars": 900,
            },
        ),
        Case(
            id="seed-stl-resize-checked",
            bucket="workspace",
            title="Resize a print and prove it with both measurements",
            difficulty=3,
            max_tokens=320,
            tags=("workspace", "3d", "stl"),
            prompt=(
                "A workspace has `parts/bracket.stl` (80 mm long) and the printer needs it 120 mm "
                "long, centred on the origin. Which single tool call does it, and what must you "
                "report afterwards? Name the tool and the fields. Three sentences at most."
            ),
            checks={
                "must_include": ["StlTransformRequest"],
                "must_match": [r"(?i)fit", r"(?i)center", r"(?i)before", r"(?i)after"],
                "max_chars": 900,
            },
        ),
    ]


#: Workspace API recipes Raven must know by heart. Each is only emitted when the
#: route still exists in the catalogue, so a renamed endpoint drops its case.
_WORKSPACE_RECIPES: tuple[tuple[str, str, str], ...] = (
    ("/files/read", "read a file inside a workspace", "read"),
    ("/files/write", "write a file inside a workspace", "write"),
    ("/tests/pytest", "run the workspace's test gate", "test"),
    ("/git/commit", "commit the workspace's changes", "commit"),
    ("/webhook/git-pull/{workspace_id}", "pull the workspace when its remote gets a push", "pull"),
    ("/ws/workspace/{workspace_id}/terminal", "open a live shell inside a workspace", "terminal"),
)


def workspace_cases(cat: dict[str, Any]) -> list[Case]:
    """Which Workspace API endpoint performs each job the model must do."""
    paths = {f.get("path") for f in cat.get("features", []) if f.get("service") == "workspace_runtime"}
    cases: list[Case] = []
    for path, job, tag in _WORKSPACE_RECIPES:
        if path not in paths:
            continue
        cases.append(
            Case(
                id=_case_id("route", path),
                bucket="workspace",
                title=f"Name the Workspace API call that will {job}",
                difficulty=2,
                max_tokens=120,
                tags=("workspace", tag),
                source="catalog:workspace_runtime",
                prompt=(
                    f"To {job} through the Workspace API, which endpoint do you call? "
                    "Answer with the path only."
                ),
                checks={"must_include": [path], "max_chars": 200},
            )
        )
    return cases


def service_cases(cat: dict[str, Any]) -> list[Case]:
    """One ownership question per service, using a real route from that service.

    Raven has to pick the right service URL before it picks a tool; a wrong
    service is the most common kind of "the tool ran but nothing happened".
    """
    best: dict[str, dict[str, Any]] = {}
    for feature in cat.get("features", []):
        if feature.get("kind") != "route" or not feature.get("summary"):
            continue
        path = str(feature.get("path") or "")
        if not path.startswith("/") or path.count("/") > 3 or "{" in path:
            continue
        service = str(feature.get("service") or "")
        if not service or service in {"ui", "gateway", "workspace_runtime"}:
            continue
        current = best.get(service)
        if current is None or len(path) < len(str(current.get("path"))):
            best[service] = feature

    cases: list[Case] = []
    for service, feature in sorted(best.items()):
        path = str(feature["path"])
        cases.append(
            Case(
                id=_case_id("service", f"{service}{path}"),
                bucket="knowledge",
                title=f"Know which service owns {path}",
                difficulty=2,
                max_tokens=60,
                tags=("knowledge", "routing"),
                source=f"catalog:{service}",
                prompt=(
                    f"Which SharedLLM service owns the endpoint {path}? "
                    "Answer with the service name only."
                ),
                checks={"must_include": [service], "max_chars": 120},
            )
        )
    return cases


def tool_cases(cat: dict[str, Any]) -> list[Case]:
    """One case per tool: its own summary is the question, its name is the answer."""
    cases: list[Case] = []
    for feature in cat.get("features", []):
        if feature.get("kind") != "tool":
            continue
        name = str(feature.get("name") or "")
        summary = str(feature.get("summary") or "")
        if not name or not summary:
            continue
        cases.append(
            Case(
                id=_case_id("tool", name),
                bucket="tools",
                title=f"Choose {name} for the job it is for",
                difficulty=1,
                max_tokens=60,
                tags=tuple(dict.fromkeys(("tools", *(feature.get("tags") or ())))),
                source="catalog:tool_registry",
                prompt=(
                    "Which tool should Jarvis call for this job?\n\n"
                    f"{summary}\n\n"
                    "Answer with the tool name only."
                ),
                checks={"must_include": [name], "max_chars": 120},
            )
        )
    return cases


def derive_curriculum(
    cat: dict[str, Any] | None = None,
    *,
    buckets: list[str] | tuple[str, ...] | None = None,
    limit: int | None = None,
    include_teacher: bool = True,
) -> list[Case]:
    """Every case the trainer can run, filtered and capped.

    Order is deliberate: the household's own work first (so a short run always
    covers sermons, repairs and image edits), then the Workspace recipes, then
    the tasks strong teacher models solved on this repository (imported traces,
    see :mod:`services.gateway.trainer.teacher`), then the tool and service
    questions.
    """
    catalog = cat if cat is not None else catalog_mod.build_catalog()
    teacher: list[Case] = []
    if include_teacher and (not buckets or "teacher" in buckets):
        from services.gateway.trainer import teacher as teacher_mod

        teacher = teacher_mod.teacher_cases()
    failures: list[Case] = []
    if include_teacher and (not buckets or "raven_failures" in buckets):
        from services.gateway.trainer import raven_failures

        failures = raven_failures.failure_cases()
    cases = [
        *_seed_cases(),
        *workspace_cases(catalog),
        *teacher,
        *failures,
        *tool_cases(catalog),
        *service_cases(catalog),
    ]

    seen: set[str] = set()
    unique: list[Case] = []
    for case in cases:
        if case.id in seen:
            continue
        seen.add(case.id)
        if buckets and case.bucket not in buckets:
            continue
        unique.append(case)

    if limit is not None:
        unique = unique[: max(0, limit)]
    return unique


def summarise(cases: list[Case]) -> dict[str, Any]:
    """Counts by bucket and difficulty, for the Lab's Training tab."""
    by_bucket: dict[str, int] = {}
    by_difficulty: dict[str, int] = {}
    for case in cases:
        by_bucket[case.bucket] = by_bucket.get(case.bucket, 0) + 1
        key = str(case.difficulty)
        by_difficulty[key] = by_difficulty.get(key, 0) + 1
    return {
        "total": len(cases),
        "by_bucket": dict(sorted(by_bucket.items())),
        "by_difficulty": dict(sorted(by_difficulty.items())),
    }


