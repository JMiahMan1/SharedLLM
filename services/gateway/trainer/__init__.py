"""Jarvis Trainer: the facilitator that keeps teaching the household's local model.

The trainer has one job: turn everything Jarvis can do into things the local
model can be tested and trained on, and turn its failures back into lessons,
workspaces and UI improvements. The pieces are deliberately small:

* :mod:`catalog`      -- derive the feature catalogue (routes, tools, UI pages)
                         from the source, so nothing goes stale.
* :mod:`curriculum`   -- derive evaluation and training cases from that
                         catalogue plus the household's own use cases (repairing
                         its errors, sermons, image edits, memory hygiene).
* :mod:`llm`          -- one local completion, inside a hardware budget.
* :mod:`runner`       -- run cases sequentially, score them, keep reports and
                         export the accepted rows as a fine-tuning dataset.
* :mod:`selffix`      -- the self-healing loop: read a workspace file, ask the
                         local model for the fix, write it back through the
                         Workspace API, and prove it with the workspace's tests.
* :mod:`sermon`       -- a written-work pipeline (sermons) that stays inside a
                         small context window, grounded on scripture.
* :mod:`dream`        -- RAG dreaming plus the improvement proposals it implies.
* :mod:`facilitator`  -- the cycle that ties dreaming, evaluation, lessons and
                         the improvement backlog together.
* :mod:`routes`       -- ``/api/trainer`` for the Lab's Training tab.
"""

from __future__ import annotations

__all__ = [
    "catalog",
    "curriculum",
    "dream",
    "facilitator",
    "llm",
    "runner",
    "selffix",
    "sermon",
]
