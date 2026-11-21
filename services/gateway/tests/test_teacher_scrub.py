"""No secret in the shared fixture survives the teacher-trace scrubber.

Every value here is synthetic, built at run time from the fixture the opencode
plugin's tests also read. The tests pin the three layers: a step that touched
secrets is dropped whole, a task about secrets (or that lost too many steps)
leaves only a content-free stub, and everything that remains is masked, with a
final leak check that would drop the trace rather than let anything through.
"""
from __future__ import annotations

import json
import pathlib

import pytest
import teacher_fixture

from services.gateway.trainer import teacher_scrub as scrub
from services.gateway.trainer import teacher_traces as traces

FIX = teacher_fixture.load()
SECRETS = FIX["secrets"]


def _config(tmp_path: pathlib.Path) -> scrub.ScrubConfig:
    env = tmp_path / ".env"
    env.write_text("\n".join(FIX["env_lines"]) + "\nBRANCH=microservices\n", encoding="utf-8")
    names_file = tmp_path / "names.txt"
    names_file.write_text("# household\n" + "\n".join(FIX["names"]) + "\n", encoding="utf-8")
    return scrub.ScrubConfig(
        names=tuple(scrub.load_names(names_file)),
        literals=tuple(scrub.load_env_literals([env])),
        names_loaded=True,
    )


def _assert_clean(blob: str) -> None:
    for kind, item in SECRETS.items():
        assert item["value"] not in blob, f"{kind} survived the scrubber"
    for name in FIX["names"]:
        assert name.lower() not in blob.lower(), "a household name survived"


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_each_secret_is_masked_in_free_text(kind, tmp_path):
    config = _config(tmp_path)
    text = f"While fixing the build I saw {SECRETS[kind]['text']} in the log."

    out = scrub.scrub_text(text, config)

    assert SECRETS[kind]["value"] not in out
    assert scrub.leak_check(out, config) == []


def test_every_secret_together_leaves_nothing(tmp_path):
    config = _config(tmp_path)
    blob = "\n".join(item["text"] for item in SECRETS.values())

    out = scrub.scrub_text(blob, config)

    _assert_clean(out)
    assert scrub.leak_check(out, config) == []


def test_ordinary_code_is_left_readable(tmp_path):
    config = _config(tmp_path)
    code = (
        "def check(password: str, token=None):\n"
        "    password = form.password\n"
        "    return process.env.API_URL or os.environ['HOME']\n"
        "git log 0123456789abcdef0123456789abcdef01234567\n"
        "services/gateway/trainer/teacher_scrub.py test_a_room_with_every_new_rule_in_play 2026-11-21\n"
        "BRANCH is microservices\n"
    )

    out = scrub.scrub_text(code, config)

    assert out == code


def test_a_missing_names_file_stops_the_run(tmp_path):
    with pytest.raises(FileNotFoundError, match="names file"):
        scrub.load_names(tmp_path / "nope.txt")


def test_an_empty_names_file_is_an_error(tmp_path):
    empty = tmp_path / "names.txt"
    empty.write_text("# nobody yet\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lists no names"):
        scrub.load_names(empty)


def test_only_secret_looking_env_values_become_literals(tmp_path):
    env = tmp_path / ".env"
    env.write_text("BRANCH=microservices\nTZ=America/Phoenix\n" + "\n".join(FIX["env_lines"]), encoding="utf-8")

    literals = scrub.load_env_literals([env])

    assert "microservices" not in literals and "America/Phoenix" not in literals
    assert SECRETS["env_literal"]["value"] in literals


def _session_trace() -> dict:
    session = FIX["session"]
    steps = [
        traces.make_step(
            step["tool"],
            json.loads(teacher_fixture.fill(json.dumps(step["input"]), SECRETS)),
            teacher_fixture.fill(step["output"], SECRETS),
            exit_code=step.get("exit"),
        )
        for step in session["steps"]
    ]
    return traces.build_trace(
        source="opencode", session_id="ses_synthetic", task_index=0, started_at=None,
        model="openrouter/teacher", location="cloud", project="SharedLLM", goal=session["goal"],
        steps=steps, final_answer=teacher_fixture.fill(session["final_answer"], SECRETS), next_user_text=None,
    )


def test_a_session_that_decrypted_keys_is_dropped_to_a_stub(tmp_path):
    """The shape of a real session that printed decrypted keys: nothing of it is kept."""
    config = _config(tmp_path)

    out = scrub.scrub_trace(_session_trace(), config)

    assert out["scrub"]["status"] == "dropped"
    assert out["scrub"]["drop_reason"] == "too_many_secret_steps"
    assert "steps" not in out and "goal" not in out and "final_answer" not in out
    assert out["scrub"]["dropped_steps"] >= 4
    _assert_clean(json.dumps(out))


def test_dropping_steps_without_dropping_the_task_still_masks_the_rest(tmp_path):
    config = _config(tmp_path)
    trace = _session_trace()
    # Keep one secret step among many ordinary ones: below the drop thresholds.
    secret_step = trace["steps"][1]
    ordinary = [traces.make_step("read", {"path": f"services/gateway/m{i}.py"}, "x = 1") for i in range(8)]
    trace["steps"] = [*ordinary, secret_step, trace["steps"][-1]]

    out = scrub.scrub_trace(trace, config)

    assert out["scrub"]["status"] == "masked"
    assert out["scrub"]["dropped_steps"] == 1
    assert out["scrub"]["dropped_reasons"] == {"secret_access": 1}
    assert len(out["steps"]) == 9
    _assert_clean(json.dumps(out))  # the final answer quoted a key: masked


def test_a_goal_about_secrets_drops_the_task(tmp_path):
    config = _config(tmp_path)
    trace = _session_trace()
    trace["steps"] = [trace["steps"][-1]]
    trace["goal"] = "Rotate the leaked API keys and decrypt the stored tokens"

    out = scrub.scrub_trace(trace, config)

    assert out["scrub"]["status"] == "dropped"
    assert out["scrub"]["drop_reason"] == "sensitive_goal"


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "grep KEY services/identity/.env.production",
        "printenv | sort",
        "docker inspect sharedllm-gateway-1",
        "cat ~/.ssh/id_ed25519",
        "sqlite3 identity.db 'select * from users'",
        "python -c 'print(fernet.decrypt(x))'",
    ],
)
def test_commands_that_touch_secrets_drop_their_step(command, tmp_path):
    step = traces.make_step("shell", {"command": command}, "output")

    assert scrub.step_drop_reason(step, _config(tmp_path)) is not None


@pytest.mark.parametrize(
    "command",
    ["cat .env.example", "pytest services/gateway -q", "grep -rn process.env src", "ls -la"],
)
def test_ordinary_commands_keep_their_step(command, tmp_path):
    step = traces.make_step("shell", {"command": command}, "fine")

    assert scrub.step_drop_reason(step, _config(tmp_path)) is None


def test_a_step_whose_output_carries_a_key_is_dropped(tmp_path):
    step = traces.make_step("shell", {"command": "curl -s localhost:8000/debug"}, SECRETS["github_token"]["text"])

    assert scrub.step_drop_reason(step, _config(tmp_path)) == "credential_in_output"


def test_residual_secrets_drop_the_trace_instead_of_leaking(tmp_path, monkeypatch):
    """If masking ever misses something the leak check catches, the trace is a stub."""
    config = _config(tmp_path)
    trace = _session_trace()
    trace["steps"] = [trace["steps"][-1]]
    monkeypatch.setattr(scrub, "scrub_text", lambda text, cfg, stats=None: text)

    out = scrub.scrub_trace(trace, config)

    assert out["scrub"]["status"] == "dropped"
    assert out["scrub"]["drop_reason"].startswith("residual:")
    _assert_clean(json.dumps(out))


def test_the_scrub_record_says_how_it_was_done(tmp_path):
    config = _config(tmp_path)
    trace = _session_trace()
    trace["steps"] = [trace["steps"][-1]]

    out = scrub.scrub_trace(trace, config)

    assert out["scrub"]["version"] == scrub.SCRUB_VERSION
    assert out["scrub"]["names_loaded"] is True
    assert out["scrub"]["ips_masked"] is True


def _two_task_session(location: str, tmp_path) -> list[dict]:
    config = _config(tmp_path)
    secret_task = _session_trace()
    secret_task["goal"] = "Decrypt the stored provider keys"
    ordinary = _session_trace()
    ordinary["steps"] = [ordinary["steps"][-1]]
    ordinary["final_answer"] = "Tests pass."
    ordinary["id"], ordinary["task_index"] = "tt-" + "c" * 12, 1
    for trace in (secret_task, ordinary):
        trace["model_location"] = location
    return [scrub.scrub_trace(secret_task, config), scrub.scrub_trace(ordinary, config)]


def test_a_cloud_session_that_touched_secrets_is_dropped_whole(tmp_path):
    out = scrub.apply_session_policy(_two_task_session("cloud", tmp_path))

    assert [t["scrub"]["status"] for t in out] == ["dropped", "dropped"]
    assert out[1]["scrub"]["drop_reason"] == "session_touched_secrets"
    assert "steps" not in out[1] and "goal" not in out[1]


def test_a_local_session_keeps_its_other_tasks(tmp_path):
    out = scrub.apply_session_policy(_two_task_session("local", tmp_path))

    assert [t["scrub"]["status"] for t in out] == ["dropped", "clean"]
