"""Keeping prompt settings in step with prompts/*.md.

Each prompts/<key>.md is the shipped text of the GlobalSetting <key>. Identity
remembers the hash of the repo text it last wrote into each one (PromptSeed), so
at startup it can tell an untouched prompt, which follows the repo, from one an
admin edited in settings, which is left alone and reported instead:

- missing setting: seeded from the file;
- live value is what was seeded and the file changed: replaced with the file;
- live value differs from what was seeded: kept, and "the repo has a newer
  version" is logged once per repo version and reported by GET /api/admin/prompts;
- no record (rows from before PromptSeed): the file's hash is recorded only when
  the live value equals it, otherwise the value counts as edited.

Whitespace at line ends and at either end of the text is not a difference.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from services.identity.models import GlobalSetting, PromptSeed

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def normalize(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def prompt_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def repo_prompts(prompts_dir: Path | None = None) -> dict[str, str]:
    """Setting key -> shipped text, for every non-empty prompts/*.md but the README."""
    directory = Path(prompts_dir or PROMPTS_DIR)
    if not directory.is_dir():
        return {}
    prompts: dict[str, str] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        content = normalize(path.read_text(encoding="utf-8"))
        if content:
            prompts[path.stem] = content
    return prompts


def _record(session: Session, key: str) -> PromptSeed:
    record = session.get(PromptSeed, key)
    if record is None:
        record = PromptSeed(key=key)
    return record


def _mark_seeded(session: Session, record: PromptSeed, repo_hash: str) -> None:
    record.seeded_hash = repo_hash
    record.announced_hash = None
    record.updated_at = datetime.now(UTC).isoformat()
    session.add(record)


def status_of(live: str | None, repo_text: str, record: PromptSeed | None) -> dict:
    """How a prompt setting stands against its repo file, without changing anything."""
    repo_hash = prompt_hash(repo_text)
    live_hash = prompt_hash(live) if live is not None else None
    seeded_hash = record.seeded_hash if record else None
    if live_hash is None:
        state = "missing"
    elif live_hash == repo_hash:
        state = "current"
    elif seeded_hash is not None and live_hash == seeded_hash:
        state = "outdated"  # untouched, and the repo moved on: the next sync replaces it
    else:
        state = "edited"
    return {
        "state": state,
        # An edited value whose repo file is not the one it was seeded from.
        "repo_has_newer_version": state == "edited" and repo_hash != seeded_hash,
        "live_hash": live_hash,
        "repo_hash": repo_hash,
        "seeded_hash": seeded_hash,
    }


def sync_prompts(session: Session, force: bool = False, prompts_dir: Path | None = None) -> dict[str, str]:
    """Bring prompt settings in step with the repo. Returns key -> what was done.

    ``force`` writes every file over its setting, edited or not (FORCE_RESEED).
    The caller commits.
    """
    actions: dict[str, str] = {}
    for key, content in repo_prompts(prompts_dir).items():
        repo_hash = prompt_hash(content)
        setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        record = _record(session, key)

        if setting is None:
            session.add(GlobalSetting(key=key, value=content, description=f"Prompt seeded from {key}.md."))
            _mark_seeded(session, record, repo_hash)
            log.info("[seed] Prompt seeded: %s from %s.md", key, key)
            actions[key] = "seeded"
            continue

        if force:
            setting.value = content
            session.add(setting)
            _mark_seeded(session, record, repo_hash)
            log.info("[seed] Force-updated prompt: %s", key)
            actions[key] = "forced"
            continue

        state = status_of(setting.value, content, record)
        if state["state"] == "current":
            if record.seeded_hash != repo_hash:
                # First sight of a pre-PromptSeed row, or an edit that now matches the repo.
                _mark_seeded(session, record, repo_hash)
            actions[key] = "current"
        elif state["state"] == "outdated":
            setting.value = content
            session.add(setting)
            _mark_seeded(session, record, repo_hash)
            log.info("[seed] updated prompt %s from the repo", key)
            actions[key] = "updated"
        else:
            if state["repo_has_newer_version"] and record.announced_hash != repo_hash:
                log.warning("[seed] prompt %s was edited in settings; the repo has a newer version", key)
                record.announced_hash = repo_hash
                session.add(record)
            actions[key] = "edited"
    return actions


def prompt_statuses(session: Session, prompts_dir: Path | None = None) -> list[dict]:
    """Every repo prompt's standing, for the admin API."""
    statuses = []
    for key, content in repo_prompts(prompts_dir).items():
        setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        status = status_of(setting.value if setting else None, content, session.get(PromptSeed, key))
        statuses.append({"key": key, **status})
    return statuses


def replace_with_repo(session: Session, key: str, prompts_dir: Path | None = None) -> dict | None:
    """Write the repo's text over a prompt setting (an admin's "replace"). None when no such file."""
    content = repo_prompts(prompts_dir).get(key)
    if content is None:
        return None
    setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
    if setting is None:
        setting = GlobalSetting(key=key, value=content, description=f"Prompt seeded from {key}.md.")
    else:
        setting.value = content
    session.add(setting)
    record = _record(session, key)
    _mark_seeded(session, record, prompt_hash(content))
    session.commit()
    log.info("[seed] prompt %s replaced with the repo version by an admin", key)
    return {"key": key, **status_of(content, content, record)}

