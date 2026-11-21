"""One local completion, inside a hardware budget.

The household's model is small and often CPU-bound, so every call here is
shaped for that: a trimmed prompt (head and tail kept, middle dropped), a hard
wall-clock limit, and a token ceiling the caller chooses. There is no retry and
no streaming: a trainer run is a sequence of short, comparable questions, and a
hung call must surface as a failure rather than stall the whole run.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("gateway.trainer.llm")

#: One short answer, a context the loaded model can hold, and no creativity:
#: the curriculum's checks are exact strings, so sampling noise only hurts.
DEFAULT_MAX_TOKENS = 320
DEFAULT_CONTEXT_CHARS = 6000
DEFAULT_TIMEOUT_S = 180.0
DEFAULT_TEMPERATURE = 0.2

#: Share of the context budget kept from the start of a prompt. The end matters
#: too (the instruction usually lives there), so both ends are kept.
_HEAD_SHARE = 0.7
_TRIM_MARKER = "\n\n[... middle of the request trimmed to fit the local model ...]\n\n"


class LocalModelUnavailable(RuntimeError):
    """The model is not configured, not reachable, or did not answer in time."""


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    elapsed_s: float
    prompt_chars: int
    truncated: bool = False


def trim_prompt(prompt: str, limit: int = DEFAULT_CONTEXT_CHARS) -> tuple[str, bool]:
    """Keep the head and the tail of a prompt inside ``limit`` characters.

    A long file plus a traceback easily exceeds a small model's window. Dropping
    the middle keeps the task statement (top) and the instruction/error (bottom),
    which is where the useful signal is.
    """
    if limit <= 0 or len(prompt) <= limit:
        return prompt, False
    head = int(limit * _HEAD_SHARE)
    tail = max(0, limit - head - len(_TRIM_MARKER))
    return prompt[:head] + _TRIM_MARKER + prompt[len(prompt) - tail:], True


class LocalModelBusy(LocalModelUnavailable):
    """The host is up but busy (no free slot in time). Not a fault of the model."""


def model_from_settings(settings: dict[str, Any]) -> str:
    """The configured preference: the trainer's own, then coding, then assistant."""
    for key in ("trainer_model", "coding_model", "assistant_model"):
        value = str(settings.get(key) or "").strip()
        if value:
            return value
    return ""


async def resolve_model(settings: dict[str, Any], requested: str | None = None) -> str:
    """The one model a run will use, checked against what the host really has.

    Order: an explicit ``requested`` model (the eval request's ``model``), then
    :func:`alpaca.choose_model` over the host's inventory (``trainer_model``,
    ``coding_model``, ``assistant_model``, then a loaded text model, then any text
    model). Every failure is loud:

    * a requested model, or a ``trainer_model`` setting, that names a picture
      model or is not installed on the host;
    * a host with no text model at all;
    * an inventory that could not be read for any reason other than the host
      having no ``/admin/models`` endpoint (404/405: a plain llama.cpp server,
      where the configured name is used as given, but never an image model).
    """
    from services.gateway.trainer import alpaca

    requested = (requested or "").strip()
    preferred = str(settings.get("trainer_model") or "").strip()
    for label, name in (("The requested model", requested), ("trainer_model", preferred)):
        if name and alpaca.is_image_model(name):
            raise LocalModelUnavailable(
                f"{label} ({name}) is a picture model; the trainer needs a text model."
            )

    base = alpaca.proxy_base(settings)
    if not base:
        raise LocalModelUnavailable(
            "llm_local_url is not set, so there is no model host to train on."
        )
    try:
        payload = await alpaca.get_json(f"{base}/admin/models")
    except Exception as exc:
        if not alpaca.is_missing_endpoint(exc):
            raise LocalModelUnavailable(
                f"Could not list the models on {base}: {exc}"
            ) from exc
        # A host without an inventory: the configured name is all there is.
        chosen = requested or model_from_settings(settings)
        if not chosen:
            raise LocalModelUnavailable(
                "No local model is configured for training. Set trainer_model (or "
                "coding_model) in Settings > AI & Compute."
            ) from exc
        if alpaca.is_image_model(chosen):
            raise LocalModelUnavailable(
                f"The configured model ({chosen}) is a picture model; set trainer_model to a text model."
            ) from exc
        return chosen

    models = [item for item in (payload.get("models") or []) if isinstance(item, dict) and item.get("name")]
    names = [str(item["name"]) for item in models]
    if requested:
        for name in names:
            if alpaca.matches_model(name, requested):
                return name
        raise LocalModelUnavailable(f"The requested model ({requested}) is not installed on {base}.")
    if preferred and not any(alpaca.matches_model(name, preferred) for name in names):
        raise LocalModelUnavailable(
            f"trainer_model ({preferred}) is not installed on {base}. Install it or change the setting."
        )
    chosen = alpaca.choose_model(settings, models)
    if not chosen:
        raise LocalModelUnavailable(f"{base} has no text model the trainer can use.")
    return chosen


async def _resolve(model: str | None) -> tuple[Any, str, dict[str, Any]]:
    """The provider, model and settings to call. Split out so tests never touch a model.

    ``model`` is the name a caller already resolved (a run resolves once and
    passes it to every call); without one, it is resolved here.
    """
    from services.gateway.llm_providers import get_provider
    from services.gateway.orchestrator import get_llm_settings

    settings = await get_llm_settings()
    chosen = (model or "").strip() or await resolve_model(settings)
    provider = await get_provider(settings)
    return provider, chosen, settings


#: Defaults for the polite-wait: the trainer will queue behind the household for
#: this long, then give up rather than burn its budget on a busy GPU.
DEFAULT_SLOT_WAIT_S = 45.0


def _wait_for_slot_enabled(settings: dict[str, Any]) -> bool:
    return str(settings.get("trainer_wait_for_slot", "true")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


async def _admit(settings: dict[str, Any]) -> None:
    """Ask Alpaca for a free llama-server slot before spending the model.

    ``ready`` proceeds; ``no_queue`` (the host answered 404/405: a plain
    llama.cpp server) proceeds as before. A timeout means the household is using
    the GPU (:class:`LocalModelBusy`), and anything else (the host down, a 5xx,
    no URL) is :class:`LocalModelUnavailable`: an unanswered question about the
    GPU is not permission to use it.
    """
    if not _wait_for_slot_enabled(settings):
        return
    from services.gateway.trainer import alpaca

    raw = settings.get("trainer_slot_wait_seconds")
    try:
        wait_s = float(raw) if raw not in (None, "") else DEFAULT_SLOT_WAIT_S
    except (TypeError, ValueError) as exc:
        raise LocalModelUnavailable(
            f"trainer_slot_wait_seconds must be a number of seconds, not {raw!r}."
        ) from exc
    answer = await alpaca.wait_for_slot(settings, timeout=wait_s)
    status = str(answer.get("status") or "")
    if status == "timeout":
        raise LocalModelBusy(
            "The model host is busy with the household right now "
            f"(no free slot after {wait_s:.0f}s). The trainer will try again later."
        )
    if status == "ready":
        log.debug("[trainer] slot opened after %ss", answer.get("wait_time"))
        return
    if status == "no_queue":
        return
    raise LocalModelUnavailable(
        f"Could not ask the model host for a free slot: {answer.get('reason') or status or 'no answer'}"
    )


async def complete(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Completion:
    """Ask the local model once, bounded in prompt size, tokens and wall clock."""
    provider, chosen, settings = await _resolve(model)
    await _admit(settings)
    user_text, truncated = trim_prompt(prompt, context_chars)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_text})
    # No num_ctx: the household's model is already loaded at its own context,
    # and asking for a different one makes the host reload it (at 4K, which then
    # starves every household request until the next reload).
    options = {
        "temperature": temperature,
        "num_predict": max(16, int(max_tokens)),
    }

    started = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            provider.generate(chosen, messages, options=options), timeout=timeout
        )
    except TimeoutError as exc:
        raise LocalModelUnavailable(
            f"The local model ({chosen}) did not answer within {timeout:.0f}s."
        ) from exc
    except Exception as exc:  # a trainer run must report this, not crash
        raise LocalModelUnavailable(f"The local model ({chosen}) failed: {exc}") from exc

    text = (raw or "").strip()
    if not text:
        raise LocalModelUnavailable(f"The local model ({chosen}) returned an empty answer.")
    return Completion(
        text=text,
        model=chosen,
        elapsed_s=round(time.monotonic() - started, 2),
        prompt_chars=len(user_text),
        truncated=truncated,
    )
