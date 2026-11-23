"""Mission discipline for Raven: a live objectives checklist and a runtime-checked debrief.

The agent loop used to decide a mission was over from the model's own words (a completion
phrase in a text reply) or from a stall after a verified artifact. These two gateway-local
tools make the end of a mission explicit and checked:

* ``MissionObjectivesRequest`` sets the checklist (replacing it each time). The loop shows it
  back to the model as one compact line every turn.
* ``MissionCompleteRequest`` is the debrief. The runtime accepts it only when every objective
  is done or dropped with a reason, and every file the mission wrote has been verified by the
  loop's own lint/test tracking (the model cannot declare files verified). A refused debrief
  names what is missing; after ``MAX_REFUSALS`` it is accepted anyway, marked unverified, so a
  mission can never loop on its debrief.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

MAX_OBJECTIVES = 12
MAX_TEXT = 200
MAX_LINE = 600
MAX_REFUSALS = 3
STATUSES = ("pending", "in_progress", "done", "dropped")
_MARKS = {"done": "[x]", "dropped": "[~]", "in_progress": "[>]", "pending": "[ ]"}


@dataclass
class Discipline:
    objectives: list[dict[str, str]] = field(default_factory=list)
    refusals: int = 0

    # -- checkpoint -------------------------------------------------------------------
    def to_checkpoint(self) -> dict[str, Any]:
        return {"objectives": list(self.objectives), "debrief_refusal_count": self.refusals}

    def restore(self, cp: dict[str, Any] | None) -> None:
        if not isinstance(cp, dict):
            return
        objs = cp.get("objectives")
        if isinstance(objs, list):
            self.objectives = [o for o in objs if isinstance(o, dict)]
        refusals = cp.get("debrief_refusal_count")
        if isinstance(refusals, int):
            self.refusals = refusals

    # -- objectives -------------------------------------------------------------------
    def set_objectives(self, payload: Any) -> dict[str, str]:
        """Replace the checklist; returns the tool result (status SUCCESS or FAILURE)."""
        items = payload.get("objectives") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return {"status": "FAILURE", "message": "MissionObjectivesRequest needs a non-empty 'objectives' list."}
        if len(items) > MAX_OBJECTIVES:
            return {"status": "FAILURE", "message": f"At most {MAX_OBJECTIVES} objectives; merge some."}
        clean: list[dict[str, str]] = []
        for i, item in enumerate(items, 1):
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                return {"status": "FAILURE", "message": f"Objective {i} needs a 'text'."}
            status = str(item.get("status") or "pending").strip().lower()
            if status not in STATUSES:
                return {"status": "FAILURE",
                        "message": f"Objective {i} has status '{status}'; use one of {', '.join(STATUSES)}."}
            note = str(item.get("note") or "").strip()
            if status == "dropped" and not note:
                return {"status": "FAILURE",
                        "message": f"Objective {i} is dropped without a note; say why it is dropped."}
            clean.append({"id": str(item.get("id") or f"o{i}")[:20],
                          "text": str(item["text"]).strip()[:MAX_TEXT], "status": status, "note": note[:MAX_TEXT]})
        self.objectives = clean
        done = sum(1 for o in clean if o["status"] in ("done", "dropped"))
        return {"status": "SUCCESS", "message": f"Objectives set: {done}/{len(clean)} done."}

    def open_objectives(self) -> list[str]:
        return [o["id"] for o in self.objectives if o.get("status") not in ("done", "dropped")]

    def line(self) -> str:
        """One compact line for the model's context, or '' with no objectives."""
        if not self.objectives:
            return ""
        done = len(self.objectives) - len(self.open_objectives())
        parts = [f"{_MARKS.get(o.get('status', ''), '[ ]')} {o.get('id')} {o.get('text', '')}" for o in self.objectives]
        text = f"OBJECTIVES {done}/{len(self.objectives)} done: " + ", ".join(parts)
        return text if len(text) <= MAX_LINE else text[: MAX_LINE - 3] + "..."

    def recap(self) -> str:
        if not self.objectives:
            return ""
        rows = []
        for o in self.objectives:
            row = f"{_MARKS.get(o.get('status', ''), '[ ]')} {o.get('id')}: {o.get('text', '')}"
            if o.get("status") == "dropped" and o.get("note"):
                row += f" (dropped: {o['note']})"
            rows.append(row)
        return "Objectives:\n" + "\n".join(rows)

    # -- debrief ----------------------------------------------------------------------
    def review_debrief(self, payload: Any, unverified: Iterable[str]) -> tuple[dict[str, str], str | None]:
        """Check a MissionCompleteRequest.

        ``unverified`` comes from the loop's own write/verify tracking, never from the
        payload. Returns (tool result, final answer). The answer is None while the
        debrief is refused; otherwise the mission ends with it.
        """
        summary = str((payload or {}).get("summary") or "").strip() if isinstance(payload, dict) else ""
        verification = str((payload or {}).get("verification") or "").strip() if isinstance(payload, dict) else ""
        missing: list[str] = []
        if not summary:
            missing.append("a summary of what was done")
        open_ids = self.open_objectives()
        if open_ids:
            missing.append("objectives not done or dropped: " + ", ".join(open_ids))
        pending = sorted(set(unverified))
        if pending:
            missing.append("files written but not linted or tested: " + ", ".join(pending))

        body = summary or "(no summary)"
        if verification:
            body += f"\n\nVerification: {verification}"
        if self.objectives:
            body += "\n\n" + self.recap()

        if not missing:
            return {"status": "SUCCESS", "message": "Debrief accepted; mission complete."}, body
        self.refusals += 1
        if self.refusals >= MAX_REFUSALS:
            answer = "Debrief not verified: " + "; ".join(missing) + ".\n\n" + body
            return {"status": "SUCCESS", "message": "Debrief accepted unverified after repeated refusals."}, answer
        return {"status": "FAILURE",
                "message": "MissionCompleteRequest refused. Missing: " + "; ".join(missing)
                + ". Fix these, update the objectives, then call MissionCompleteRequest again."}, None
