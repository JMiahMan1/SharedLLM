"""Every env var services/config.py reads must have a deterministic test default.

services/config.py derives each of its ~50 constants ONCE at import, into module
attributes that roughly 30 other modules capture at their own import time. A var
with no test default therefore has two different values depending on where the
machine's .env happens to point, and a var whose value changes between two
modules' imports is a var that two modules disagree about.

That is not hypothetical. conftest.py's _load_env_files() reads the developer's
real .env, and EXECUTION_EXTERNAL_HOST was only ever set there
(=192.168.2.205). Two test modules clobbered it to "localhost" at import, and two
others reload services.config mid-session — so
services/execution/abs_client.py and services/config.py ended up holding
different snapshots of the same name, and test_abs_stream_url.py compared one
against the other. It failed on a developer machine and passed in CI, which told
us nothing about the code it was meant to cover.

These tests fail the moment a new os.environ read lands in services/config.py
without a matching pin in conftest.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PY = ROOT / "services" / "config.py"
CONFTEST = ROOT / "conftest.py"


_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def _config_env_reads() -> set[str]:
    """Every env var name services/config.py reads, by AST (not a bare regex)."""
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        # os.environ.get(...) / os.getenv(...)
        if node.func.attr in {"get", "getenv"} and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
        # os.environ["NAME"]
        if node.func.attr == "getitem" and len(node.args) == 1:
            sub = node.func.value
            if isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Constant):
                val = sub.slice.value
                if isinstance(val, str):
                    found.add(val)
    return found


def _root_conftest() -> object:
    """The ROOT conftest, by path.

    `import conftest` is not safe: pytest imports every conftest.py it finds as a
    top-level module named "conftest", so services/gateway/tests/conftest.py
    shadows this one. Load it explicitly by file location instead.
    """
    import importlib.util
    import sys

    if "alpaca_root_conftest" in sys.modules:
        return sys.modules["alpaca_root_conftest"]
    spec = importlib.util.spec_from_file_location("alpaca_root_conftest", CONFTEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["alpaca_root_conftest"] = module
    spec.loader.exec_module(module)
    return module


def _test_env_defaults() -> list[str]:
    """conftest's _TEST_ENV_DEFAULTS keys, read by AST so a syntax error cannot
    hide them. Values are not always literals (some are tempfile paths), so only
    the keys matter here — the applied values are asserted against os.environ."""
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        if not any(getattr(t, "id", "") == "_TEST_ENV_DEFAULTS" for t in targets):
            continue
        assert isinstance(node.value, ast.Dict), "_TEST_ENV_DEFAULTS must be a literal dict"
        keys: list[str] = []
        for key in node.value.keys:
            assert isinstance(key, ast.Constant) and isinstance(key.value, str), ast.dump(key)
            keys.append(key.value)
        return keys
    pytest.fail("conftest.py no longer defines _TEST_ENV_DEFAULTS")


def _conftest_statement_pins() -> set[str]:
    """Keys conftest pins with its own os.environ calls rather than the dict.

    The service URLs, the two database URLs and the embedding model predate
    _TEST_ENV_DEFAULTS; they count as pinned, they just are not in it.
    """
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"))
    pins: set[str] = set()
    for node in ast.walk(tree):
        # os.environ.setdefault("KEY", ...)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setdefault"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            pins.add(node.args[0].value)
        # os.environ["KEY"] = ...
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].value, ast.Attribute)
            and node.targets[0].value.attr == "environ"
        ):
            sl = node.targets[0].slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                pins.add(sl.value)
    return pins


def _all_pins() -> set[str]:
    return set(_test_env_defaults()) | _conftest_statement_pins()


def _services_tree_env_reads() -> set[str]:
    """Every env var name read anywhere in the repo, by AST.

    Deliberately wider than services/config.py, and wider than services/ alone,
    for two reasons that both showed up as false "stale pin" reports:

      * DEVICE_REGISTRY_PATH is read through a module constant
        (services/execution/device_registry.py: DB_PATH_ENV = "DEVICE_REGISTRY_PATH",
        then os.environ.get(DB_PATH_ENV, ...)), so a reader that only looks at
        literal call arguments cannot see it. Pinning it is still correct — it
        points at the production device list otherwise.
      * Some pins are consumed by tests rather than by service code. A pin that
        only a test reads is live, not dead.
    """
    roots = [ROOT / "services", ROOT / "tests", ROOT / "test"]
    found: set[str] = set()
    for base in roots:
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr in {"get", "getenv"} and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        found.add(first.value)
            # An env var name can also reach os.getenv indirectly. seed.py builds
            # "MAIL_URL"/"MAIL_ADMIN"/... by os.getenv(f"MAIL_{key}") over a dict
            # literal, so the name never appears as a call argument. Any
            # SHOUTING_SNAKE string constant in a service module is treated as a
            # read, which errs towards keeping a legitimate pin: the cost of a
            # false negative here is a useless pin, the cost of a false positive
            # is a warning nobody can act on.
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and _ENV_NAME_RE.match(node.value)
                ):
                    found.add(node.value)
            # Reads can be indirect: services/execution/device_registry.py does
            # DB_PATH_ENV = "DEVICE_REGISTRY_PATH" and then os.environ.get(DB_PATH_ENV, ...).
            # Resolving every such alias needs dataflow analysis; for a staleness
            # check it is enough to count a *_ENV constant's value as "read", which
            # errs towards keeping a legitimate pin rather than demanding its removal.
            for node in ast.walk(tree):
                targets: list[ast.expr] = []
                value: ast.expr | None = None
                if isinstance(node, ast.Assign):
                    targets, value = list(node.targets), node.value
                elif isinstance(node, ast.AnnAssign):
                    targets, value = [node.target], node.value
                if value is None or not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    continue
                for target in targets:
                    name = getattr(target, "id", "") or getattr(target, "attr", "")
                    if name.endswith("_ENV"):
                        found.add(value.value)
    return found


def test_config_env_reads_are_all_pinned():
    """A new env read in services/config.py must come with a test default."""
    reads = _config_env_reads()
    unpinned = sorted(reads - _all_pins())
    assert not unpinned, (
        f"services/config.py reads {len(reads)} env vars but conftest does not pin: {unpinned}. "
        f"Add them to _TEST_ENV_DEFAULTS, or the value silently depends on the developer's .env "
        f"and on module import order."
    )


def test_defaults_are_not_stale():
    """A key in _TEST_ENV_DEFAULTS that nothing under services/ reads is dead
    weight that implies coverage it does not provide.

    Scoped to _TEST_ENV_DEFAULTS on purpose: conftest's other pins (the service
    URLs, INIT_DB, TEST_MODE) are consumed by other layers, not by config, so
    they are not stale.
    """
    reads = _services_tree_env_reads()
    stale = sorted(set(_test_env_defaults()) - reads)
    assert not stale, f"_TEST_ENV_DEFAULTS pins {stale}, which nothing in the repo reads"


def test_the_pins_really_reach_the_environment():
    """A pinned key that never landed in os.environ protects nothing."""
    assert _root_conftest() is not None
    for key in _test_env_defaults():
        assert os.environ.get(key) is not None, f"{key} is pinned in conftest but absent from the environment"
    for key in ("INTERNAL_SECRET", "FERNET_KEY", "IDENTITY_DATABASE_URL", "WORKSPACE_DATABASE_URL"):
        assert os.environ.get(key), f"conftest no longer sets {key}"


def test_config_sees_the_pinned_values_not_the_dotenv_ones():
    """The end-to-end guarantee: the value services/config.py derived is the test
    value, whatever the machine's .env says."""
    from services import config

    for key in ("EXECUTION_EXTERNAL_HOST", "ABS_URL", "HA_URL", "GATEWAY_INTERNAL_URL", "TIMEZONE"):
        assert getattr(config, key) == os.environ[key], f"services.config.{key} came from .env, not the pin"


@pytest.mark.parametrize("secret", ["ABS_API_KEY", "HA_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN", "NEXTCLOUD_PASS"])
def test_no_real_deployment_secret_reaches_the_test_environment(secret: str):
    """The real .env carries live credentials; _load_env_files() must not inject
    them into a test run now that every config var is pinned."""
    assert _root_conftest() is not None
    real = _real_env_value(secret)
    if real is None:
        pytest.skip(f"{secret} is not set in this checkout's .env")
    assert os.environ[secret] != real, f"{secret} leaked a real .env value into the test environment"


def test_the_environ_isolation_fixture_really_restores():
    """The autouse fixture is what stops one test's env edit from becoming the
    next test's baseline — the mechanism that made the ABS test order-dependent.
    Drive the underlying generator directly so a no-op body cannot pass."""
    conftest = _root_conftest()
    fixture_fn = conftest._isolate_environ.__wrapped__

    before = dict(os.environ)
    gen = fixture_fn()
    next(gen)  # entering the fixture body
    os.environ["A_VAR_ONLY_THIS_TEST_SET"] = "leaked"
    os.environ["EXECUTION_EXTERNAL_HOST"] = "hijacked"
    with pytest.raises(StopIteration):
        next(gen)  # drives the finally block
    assert dict(os.environ) == before
    assert "A_VAR_ONLY_THIS_TEST_SET" not in os.environ
    assert os.environ["EXECUTION_EXTERNAL_HOST"] == before["EXECUTION_EXTERNAL_HOST"]


def test_ast_reader_finds_every_env_read():
    """Guard the AST reader itself: a silent under-count would make
    test_config_env_reads_are_all_pinned vacuous."""
    reads = _config_env_reads()
    # spot-check names that appear in each of the three call styles
    for known in ("HA_URL", "ABS_API_KEY", "INTERNAL_SECRET", "FERNET_KEY", "TIMEZONE"):
        assert known in reads, f"the AST reader missed {known}; the coverage test is not trustworthy"
    assert len(reads) > 40, f"only found {len(reads)} env reads; the reader is probably broken"


def _real_env_value(key: str) -> str | None:
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


_SUBPROCESS_PROBE = r"""
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("root_conftest_probe", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# Report what the pins ACTUALLY produced, from the dict conftest itself applied,
# rather than from a re-parse of its source: that makes a value the tests cannot
# evaluate (a tempfile path, the generated Fernet key) comparable too.
pins = mod._TEST_ENV_DEFAULTS
print(json.dumps({"applied": {k: os.environ.get(k) for k in pins},
                  "mismatched": {k: [v, os.environ.get(k)] for k, v in pins.items()
                                 if os.environ.get(k) != v},
                  "queried": {k: os.environ.get(k) for k in json.loads(sys.argv[2])}}))
"""


def _conftest_in_subprocess(queried: list[str], ambient: dict[str, str], scrub_pins: bool = False) -> dict:
    """Import the root conftest in a fresh interpreter and report what it produced.

    The in-process tests cannot see the defect this guards: they run *after*
    conftest has already applied its pins, so an ordering bug is invisible to
    them. Only a fresh interpreter can prove a pin actually wins against a value
    the runner exported — which is exactly how five test_music_proxy tests came
    to return 401 against a real INTERNAL_SECRET while every one of them passed
    in isolation.

    `scrub_pins` removes every pinned key from the child's environment first, so
    os.environ is genuinely empty and the only possible source of a value is the
    .env file. That is what the passthrough test needs; leaving them in place
    would let this process's own pins be inherited and prove nothing.
    """
    import json
    import subprocess
    import sys

    env = dict(os.environ)
    if scrub_pins:
        for key in _test_env_defaults():
            env.pop(key, None)
    env.update(ambient)
    out = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_PROBE, str(CONFTEST), json.dumps(queried)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "key,exported",
    [
        # The five test_music_proxy 401s, verbatim.
        ("INTERNAL_SECRET", "a-real-deployment-secret"),
        # Bug #15's original symptom, as a CI runner would supply it.
        ("EXECUTION_EXTERNAL_HOST", "192.168.2.205"),
        # A var a developer's own shell usually exports.
        ("HA_URL", "https://homeassistant.internal:8123"),
        ("GITHUB_TOKEN", "ghp_real_token"),
    ],
)
def test_a_pinned_key_beats_a_value_the_runner_exported(key: str, exported: str):
    """Pins are applied by ASSIGNMENT, after the .env load, so nothing outside
    this file can change them.

    setdefault was wrong here and failed silently: it defers to whatever is
    already in the environment, so a CI `env:` block or an exported shell
    variable won every time while the suite still looked correctly configured.
    """
    result = _conftest_in_subprocess([key], {key: exported}, scrub_pins=True)
    assert result["queried"][key] != exported, (
        f"{key} kept the runner-exported value {exported!r}; the pin must be an assignment, not a setdefault"
    )


def test_every_pin_lands_with_exactly_the_value_listed():
    """Compare os.environ against the dict conftest itself applied, so a value
    the test cannot evaluate as a literal (a tempfile path, the generated
    Fernet key) is still checked."""
    result = _conftest_in_subprocess([], {}, scrub_pins=True)
    assert result["mismatched"] == {}, f"conftest did not apply these pins as listed: {result['mismatched']}"
    assert set(result["applied"]) == set(_test_env_defaults())


def test_the_passthrough_opt_out_still_yields_to_the_dotenv():
    """SHAREDLLM_TEST_PASSTHROUGH_ENV exists for `local_only` runs that really
    point at live infrastructure, and is never set in CI. It must reach the
    developer's own .env value — otherwise it is a switch that does nothing."""
    real = _real_env_value("HA_URL")
    if real is None:
        pytest.skip("HA_URL is not set in this checkout's .env")
    result = _conftest_in_subprocess(["HA_URL"], {"SHAREDLLM_TEST_PASSTHROUGH_ENV": "1"}, scrub_pins=True)
    assert result["queried"]["HA_URL"] == real


def test_the_passthrough_opt_out_is_off_by_default():
    """A stray export in a shell profile must not silently re-enable it."""
    result = _conftest_in_subprocess(["HA_URL"], {"SHAREDLLM_TEST_PASSTHROUGH_ENV": ""}, scrub_pins=True)
    assert result["queried"]["HA_URL"] == "http://localhost:8123"


# ---------------------------------------------------------------------------
# Module-scope env assignment is a load-order lottery
# ---------------------------------------------------------------------------
#
# A test module that does `os.environ.setdefault("X", ...)` at module scope runs
# at COLLECTION. Every services/*/config.py derives its constants once, at
# IMPORT. Whichever happens first wins, and a single-file run is not a
# representative sample:
#
#   pytest services/gateway/tests/test_music_proxy.py    -> 5 passed
#   pytest -m "not local_only and not server_only"       -> 4 failed
#
# test_music_proxy.py set ALPACA_AUDIO_URL at module scope. Run alone, that
# landed before services.gateway.main imported services/gateway/config.py, so
# the route saw the URL. In the full suite an earlier module had already
# imported main, ALPACA_AUDIO_URL was captured as "", and four tests failed
# with a 503 that read like a missing-deployment error. Same file, same code,
# opposite outcome — a failure that says nothing about the thing it appears to
# be about.
#
# Conftest is imported before any test module and pins by direct assignment, so
# it is the only place a value can be guaranteed to land early enough. The rule
# this file enforces: if a test module sets an env var at module scope, conftest
# pins it too, and the module's own line is redundant noise.

_MODULE_SCOPE_ASSIGNMENT = (
    ast.Assign,
    ast.AnnAssign,
)


def _module_scope_env_writes() -> dict[str, list[str]]:
    """Map env var name -> test files that assign it at module scope.

    Only top-level statements count. An assignment nested inside a function is
    a per-test monkeypatch and is both safe and intended.
    """
    found: dict[str, list[str]] = {}
    for base in (ROOT / "services", ROOT / "tests", ROOT / "test"):
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue
            # A conditional write is the *worst* form, not an exemption: it runs
            # at collection, it silently no-ops once the value already exists, and
            # the outcome depends on what another module happened to set first.
            # test_identity_resolution.py had three of them guarding the very vars
            # conftest now pins, and the guard silently decided the test's fate.
            top_level: list[ast.stmt] = []
            for node in tree.body:
                if isinstance(node, ast.If):
                    top_level.extend(node.body)
                    top_level.extend(node.orelse)
                else:
                    top_level.append(node)
            for node in top_level:
                targets: list[ast.expr] = []
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                else:
                    # os.environ.setdefault("X", ...) is a statement, not an
                    # assignment, so it needs its own arm. It is also the *most*
                    # misleading of the three: it reads like a defensive default
                    # but at collection time it is a silent no-op whenever any
                    # other module set the var first, which is how
                    # ALPACA_AUDIO_URL froze as "" and turned four passing
                    # music-proxy tests into "not configured on this deployment".
                    if (
                        isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Attribute)
                        and node.value.func.attr in {"setdefault", "pop"}
                        and isinstance(node.value.func.value, ast.Attribute)
                        and node.value.func.value.attr == "environ"
                        and node.value.args
                    ):
                        arg = node.value.args[0]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            found.setdefault(arg.value, []).append(str(py.relative_to(ROOT)))
                    continue
                for target in targets:
                    if not (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "environ"
                    ):
                        continue
                    key = target.slice
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.setdefault(key.value, []).append(str(py.relative_to(ROOT)))
    return found


# Two categories are exempt, deliberately and for different reasons.
#
#   * computed names. services/config.py and services/gateway/config.py build
#     "{NETWORK_MODE}_EXECUTION_SVC_URL" and friends with an f-string, so a
#     static reader cannot tie a pin to its read.
#   * per-file isolation. The workspace_runtime tests pin their own directory
#     and database here; a single shared default would let two files in one run
#     share a workspace database.
_EXEMPT = {"WORKSPACE_RUNTIME_ROOT", "WORKSPACE_DATABASE_URL"}

# The shortest f-string fragment that counts as evidence of a computed name.
# Below this, a fragment is more likely to be a shared word than a name suffix.
_MIN_COMPUTED_FRAGMENT = 12


def _computed_name_fragments() -> set[str]:
    """String fragments from every f-string in services/, e.g. the "_EXECUTION_SVC_URL".

    Only long fragments are returned. A short one would be useless as evidence:
    the fragment "DEFAULT_" is contained in "DEFAULT_ADMIN_PASSWORD", so keeping
    it would silently exempt every var that happens to share a prefix with some
    f-string literal. Requiring the fragment to be a substantial part of the
    name keeps the exemption for the real cases (a "_EXECUTION_SVC_URL" suffix
    appended to a network mode) while still flagging ordinary names.
    """
    fragments: set[str] = set()
    for py in (ROOT / "services").rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for part in node.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str) and len(part.value) >= _MIN_COMPUTED_FRAGMENT:
                        fragments.add(part.value)
    return fragments


def test_no_test_module_sets_an_env_var_at_module_scope():
    """Module scope is collection time, which is too late to be reliable.

    A var assigned here must be pinned in conftest instead; conftest is imported
    before anything else, so its value is the one every service module sees.
    Deleting these lines is safe precisely because the pin already exists.
    """
    computed = _computed_name_fragments()
    offenders = {
        name: files
        for name, files in _module_scope_env_writes().items()
        if name not in _EXEMPT
        and not any(frag in name for frag in computed)
        and any("/tests/" in f or f.startswith("tests/") or f.startswith("test/") for f in files)
    }
    assert not offenders, (
        "these env vars are assigned at test-module scope, where they race the "
        "config modules that read them: "
        + "; ".join(f"{name} in {', '.join(files)}" for name, files in sorted(offenders.items()))
    )


