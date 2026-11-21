"""Workspace Studio: a workspace that builds, runs, previews and makes things.

The pieces are deliberately small and separately testable:

* :mod:`profiles`   -- what kind of project is here, and the commands it takes
                      (pure; no container needed).
* :mod:`runtime`    -- run a step in the workspace's sandbox, start a preview and
                      publish its port, stop it again.
* :mod:`media`      -- generated art and narration, written into ``assets/`` with
                      a manifest that records the prompt and model.
* :mod:`deck`       -- Markdown to one self-contained HTML deck.
* :mod:`templates`  -- the deck and game documents (no CDN, no build step).
* :mod:`works`      -- writing decks and games into the workspace, with a README.
* :mod:`routes`     -- ``/api/studio`` for the Tools panel and the workspace chat.
"""

from __future__ import annotations

__all__ = ["deck", "media", "profiles", "routes", "runtime", "templates", "works"]
