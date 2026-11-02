"""The family cookbook: one shelf of recipes the whole house reads.

Recipes live in the household (Admin) account's ``Recipes/`` folder, so every
member reads the same shelf. Each recipe names who added it on an
``Added by:`` line, and only they change or delete it. A recipe the Admin
account added (or one from before the line existed) is changed by any admin.
Anyone else can suggest a change: it waits in ``Recipes/Suggestions/`` until
the recipe's owner or an admin approves it.

The gateway is the only way in. Members never hold the Admin account's
credentials, and the owner line is written here, never taken from the client.
"""
from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

COOKBOOK_DIR = "Recipes"
SUGGESTIONS_DIR = "Recipes/Suggestions"
#: Each member's week plan and shopping list sit in their own Recipes/ folder
#: (the Admin account's too); they are not recipes.
NOT_RECIPES = frozenset({
    "Recipes/Meal plan.md",
    "Recipes/Meal-plan.md",
    "Recipes/Shopping list.md",
    "Recipes/Shopping-list.md",
})
MAX_MESSAGE = 500

_OWNER_LINE = re.compile(r"^Added by:[ \t]*(.*?)[ \t]*$", re.IGNORECASE)
_HEADER_LINE = re.compile(r"^(Tags|Times|Source):", re.IGNORECASE)
#: The header the note API writes on every note: "# <title>", "Category: <dir>", blank.
_STORED_HEADER = re.compile(r"\A[ \t]*\n*# [^\n]*\nCategory: [^\n]*\n(?:[ \t]*\n)?")
_ATTACHMENT_LINK = re.compile(r"\]\(<?(\.attachments\.[0-9]{1,20}/[^)\s>]+)>?\)")
_SUGGESTION_KEYS = {
    "recipe": "Recipe",
    "recipe_title": "Recipe title",
    "by": "Suggested by",
    "at": "Suggested at",
    "message": "Message",
}
_SEPARATOR = "\n---\n"

#: Runs one note action against execution with the given credentials and
#: returns its result (``status``, ``message``, ``detail``).
Runner = Callable[[dict, dict], Awaitable[dict]]


@dataclass
class Caller:
    """Who is asking, and the household account the cookbook lives in."""

    user: str
    is_admin: bool
    house: str
    house_creds: dict
    own_creds: dict
    #: An admin adding a recipe as the Admin account (the "Send as" switch).
    as_house: bool = False

    @property
    def author(self) -> str:
        return self.house if self.as_house else self.user


def clean_path(path: Any) -> str:
    return str(path or "").strip().lstrip("/")


def _safe(path: str) -> bool:
    return path.endswith(".md") and ".." not in path.split("/") and "\\" not in path


def is_recipe_path(path: Any) -> bool:
    """A recipe in the cookbook: under Recipes/, not a plan, list or suggestion."""
    p = clean_path(path)
    return (
        _safe(p)
        and p.startswith(COOKBOOK_DIR + "/")
        and not p.startswith(SUGGESTIONS_DIR + "/")
        and p not in NOT_RECIPES
    )


def is_suggestion_path(path: Any) -> bool:
    p = clean_path(path)
    return _safe(p) and p.startswith(SUGGESTIONS_DIR + "/") and p.count("/") == 2


def strip_stored_header(text: str | None) -> str:
    """A note's text without the header(s) the note API stores on it."""
    out = text or ""
    while (m := _STORED_HEADER.match(out)):
        out = out[m.end():]
    return out


def stored_title(text: str | None) -> str:
    """The title in a stored note's "# <title>" header, or ""."""
    m = _STORED_HEADER.match(text or "")
    return m.group(0).lstrip().split("\n", 1)[0][2:].strip() if m else ""


def recipe_owner(body: str | None, default: str) -> str:
    """Who added the recipe, from its "Added by:" line (``default`` if none)."""
    for line in (body or "").splitlines():
        if line.startswith("## "):
            break
        m = _OWNER_LINE.match(line)
        if m and m.group(1):
            return m.group(1).strip().lower()
    return default


def without_owner(body: str | None) -> str:
    """The recipe without its "Added by:" line (the editor never shows it)."""
    lines = [line for line in (body or "").split("\n") if not _OWNER_LINE.match(line)]
    return "\n".join(lines)


def with_owner(body: str | None, owner: str) -> str:
    """The recipe with one "Added by:" line, after its Tags/Times/Source lines."""
    lines = without_owner(body).split("\n")
    at = 0
    while at < len(lines) and _HEADER_LINE.match(lines[at]):
        at += 1
    entry = [f"Added by: {owner}"]
    if at == 0 and lines and lines[0].strip():
        entry.append("")
    return "\n".join(lines[:at] + entry + lines[at:])


def can_change(owner: str, caller: Caller) -> bool:
    """Only whoever added a recipe changes it; the Admin account's, any admin."""
    if owner == caller.house:
        return caller.is_admin
    return owner == caller.user


def can_review(owner: str, caller: Caller) -> bool:
    """A suggested change is approved by the recipe's owner or by an admin."""
    return can_change(owner, caller) or caller.is_admin


def owner_label(owner: str, caller: Caller) -> str:
    if owner == caller.house:
        return "an admin"
    return owner[:1].upper() + owner[1:]


def suggestion_text(recipe_path: str, recipe_title: str, by: str, message: str, body: str) -> str:
    one_line = " ".join((message or "").split())[:MAX_MESSAGE]
    meta = [
        f"Recipe: {recipe_path}",
        f"Recipe title: {recipe_title}",
        f"Suggested by: {by}",
        f"Suggested at: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
    ]
    if one_line:
        meta.append(f"Message: {one_line}")
    return "\n".join(meta) + _SEPARATOR + body


def parse_suggestion(text: str | None) -> dict | None:
    """A stored suggestion's fields and proposed body, or None if malformed."""
    raw = strip_stored_header(text)
    head, sep, body = raw.partition(_SEPARATOR)
    if not sep:
        return None
    fields: dict[str, str] = {}
    labels = {label.lower(): key for key, label in _SUGGESTION_KEYS.items()}
    for line in head.splitlines():
        label, colon, value = line.partition(":")
        key = labels.get(label.strip().lower())
        if colon and key:
            fields[key] = value.strip()
    if not is_recipe_path(fields.get("recipe")) or not fields.get("by"):
        return None
    return {
        "recipe": clean_path(fields["recipe"]),
        "recipe_title": fields.get("recipe_title") or "",
        "by": fields["by"].lower(),
        "at": fields.get("at") or "",
        "message": fields.get("message") or "",
        "content": body,
    }


def attachment_links(text: str | None) -> list[str]:
    """The relative attachment links in a note's markdown, in order."""
    return list(dict.fromkeys(_ATTACHMENT_LINK.findall(text or "")))


def _ok(message: str, **detail: Any) -> dict:
    return {"status": "SUCCESS", "message": message, "service": "cookbook", "detail": detail}


def _fail(message: str, **detail: Any) -> dict:
    return {"status": "FAILURE", "message": message, "service": "cookbook", "detail": detail}


def _succeeded(result: dict) -> bool:
    return isinstance(result, dict) and result.get("status") == "SUCCESS"


def _note(action: str, **fields: Any) -> dict:
    return {"action": action, "storage": "nextcloud", **fields}


async def _listing(run: Runner, creds: dict, directory: str) -> tuple[list[dict] | None, dict]:
    res = await run(creds, _note("list", directories=[directory]))
    if not _succeeded(res):
        return None, res
    return list((res.get("detail") or {}).get("notes") or []), res


async def _read(run: Runner, creds: dict, path: str) -> tuple[str | None, str | None, dict]:
    res = await run(creds, _note("read", path=path, title=path.rsplit("/", 1)[-1][:-3]))
    if not _succeeded(res):
        return None, None, res
    return str(res.get("message") or ""), (res.get("detail") or {}).get("etag"), res


async def list_recipes(run: Runner, caller: Caller) -> dict:
    notes, res = await _listing(run, caller.house_creds, COOKBOOK_DIR)
    if notes is None:
        return res
    shelf = [n for n in notes if is_recipe_path(n.get("path"))]
    return _ok("Recipes listed", notes=shelf, me=caller.user, is_admin=caller.is_admin)


async def _find_by_title(run: Runner, caller: Caller, title: str) -> str | None:
    notes, _ = await _listing(run, caller.house_creds, COOKBOOK_DIR)
    wanted = title.strip().lower()
    for note in notes or []:
        if is_recipe_path(note.get("path")) and str(note.get("title") or "").strip().lower() == wanted:
            return clean_path(note.get("path"))
    return None


async def read_recipe(run: Runner, caller: Caller, path: Any = None, title: Any = None) -> dict:
    """One recipe: its text without the owner line, and who may change it."""
    target = clean_path(path)
    if not target and str(title or "").strip():
        target = await _find_by_title(run, caller, str(title)) or ""
        if not target:
            return _fail(f"“{title}” is not in the cookbook.")
    if not is_recipe_path(target):
        return _fail("That is not a recipe in the cookbook.")
    text, etag, res = await _read(run, caller.house_creds, target)
    if text is None:
        return res
    body = strip_stored_header(text)
    owner = recipe_owner(body, caller.house)
    return {
        "status": "SUCCESS",
        "message": without_owner(body),
        "service": "cookbook",
        "detail": {
            "path": target,
            "title": stored_title(text) or target.rsplit("/", 1)[-1][:-3],
            "etag": etag,
            "owner": owner,
            "owner_label": owner_label(owner, caller),
            "can_change": can_change(owner, caller),
            "can_review": can_review(owner, caller),
        },
    }


async def _create(run: Runner, caller: Caller, title: str, body: str) -> dict:
    """Write a new recipe, never over one already there."""
    res = await run(caller.house_creds, _note(
        "write", title=title, category=COOKBOOK_DIR, content=body, if_none_match=True,
    ))
    if (res.get("detail") or {}).get("conflict"):
        return _fail(f"A recipe called “{title}” is already in the cookbook.", exists=True)
    return res


async def save_recipe(
    run: Runner, caller: Caller, title: Any, content: Any, path: Any = None, if_match: Any = None,
) -> dict:
    """Add a recipe (owned by whoever adds it), or change one the caller owns."""
    name = str(title or "").strip()
    if not name:
        return _fail("Give the recipe a name.")
    body = without_owner(str(content or ""))
    target = clean_path(path)
    if not target:
        owner = caller.author
        res = await _create(run, caller, name, with_owner(body, owner))
        if _succeeded(res):
            res.setdefault("detail", {})["owner"] = owner
        return res

    current = await read_recipe(run, caller, target)
    if not _succeeded(current):
        return current
    detail = current["detail"]
    owner = detail["owner"]
    if not detail["can_change"]:
        return _fail(
            f"Only {owner_label(owner, caller)} can change this recipe. Suggest the change instead.",
            owner=owner, forbidden=True,
        )
    stamped = with_owner(body, owner)
    if name == detail["title"]:
        res = await run(caller.house_creds, _note(
            "write", title=name, category=COOKBOOK_DIR, path=target, content=stamped,
            if_match=str(if_match) if if_match else None,
        ))
        if _succeeded(res):
            res.setdefault("detail", {})["owner"] = owner
        return res

    # A rename writes the new file first and only then removes the old one,
    # whose attachments follow the new copy.
    res = await _create(run, caller, name, stamped)
    if not _succeeded(res):
        return res
    new_path = clean_path((res.get("detail") or {}).get("path"))
    left_old = False
    if new_path and new_path != target:
        old = await run(caller.house_creds, _note(
            "delete", title=detail["title"], path=target, attachments_to=new_path,
        ))
        left_old = not _succeeded(old)
    res.setdefault("detail", {}).update(owner=owner, left_old_copy=left_old)
    return res


async def delete_recipe(run: Runner, caller: Caller, path: Any) -> dict:
    current = await read_recipe(run, caller, path)
    if not _succeeded(current):
        return current
    detail = current["detail"]
    if not detail["can_change"]:
        return _fail(
            f"Only {owner_label(detail['owner'], caller)} can delete this recipe.",
            owner=detail["owner"], forbidden=True,
        )
    res = await run(caller.house_creds, _note("delete", title=detail["title"], path=detail["path"]))
    if _succeeded(res):
        # Its waiting suggestions go with it: there is nothing left to change.
        for suggestion in await _suggestions(run, caller):
            if suggestion["recipe"] == detail["path"]:
                await run(caller.house_creds, _note("delete", title=suggestion["id"], path=suggestion["path"]))
    return res


async def attach(run: Runner, caller: Caller, path: Any, filename: Any, data: Any) -> dict:
    """Keep a file (the original photo) beside a recipe the caller may change."""
    current = await read_recipe(run, caller, path)
    if not _succeeded(current):
        return current
    if not current["detail"]["can_change"]:
        return _fail("Only the person who added this recipe can attach files to it.", forbidden=True)
    return await run(caller.house_creds, _note(
        "attachment_upload", path=current["detail"]["path"], filename=filename, data=data,
    ))


async def suggest(run: Runner, caller: Caller, path: Any, content: Any, message: Any = None) -> dict:
    """Leave a change to someone else's recipe for its owner (or an admin) to approve."""
    current = await read_recipe(run, caller, path)
    if not _succeeded(current):
        return current
    detail = current["detail"]
    if detail["can_change"]:
        return _fail("This is your recipe: save the change instead.")
    body = without_owner(str(content or ""))
    if body.strip() == str(current["message"]).strip():
        return _fail("That is the recipe as it already stands.")
    note_title = f"Suggestion {datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    text = suggestion_text(detail["path"], detail["title"], caller.user, str(message or ""), body)
    res = await run(caller.house_creds, _note(
        "write", title=note_title, category=SUGGESTIONS_DIR, content=text, if_none_match=True,
    ))
    if not _succeeded(res):
        return res
    return _ok(
        f"Sent to {owner_label(detail['owner'], caller)} to approve.",
        path=(res.get("detail") or {}).get("path"),
    )


async def _suggestions(run: Runner, caller: Caller) -> list[dict]:
    notes, _ = await _listing(run, caller.house_creds, SUGGESTIONS_DIR)
    out: list[dict] = []
    for note in notes or []:
        path = clean_path(note.get("path"))
        if not is_suggestion_path(path):
            continue
        text, _, _ = await _read(run, caller.house_creds, path)
        parsed = parse_suggestion(text)
        if parsed:
            out.append({"id": path.rsplit("/", 1)[-1][:-3], "path": path, **parsed})
    out.sort(key=lambda s: s["at"])
    return out


async def list_suggestions(run: Runner, caller: Caller) -> dict:
    """The changes the caller can approve, and the ones they are waiting on."""
    shown: list[dict] = []
    recipes: dict[str, dict] = {}
    for suggestion in await _suggestions(run, caller):
        recipe = recipes.get(suggestion["recipe"])
        if recipe is None:
            recipe = recipes[suggestion["recipe"]] = await read_recipe(run, caller, suggestion["recipe"])
        found = _succeeded(recipe)
        owner = recipe["detail"]["owner"] if found else caller.house
        mine = suggestion["by"] == caller.user
        reviewable = can_review(owner, caller) if found else caller.is_admin
        if not (mine or reviewable):
            continue
        shown.append({
            **suggestion,
            "current": recipe["message"] if found else None,
            "owner": owner,
            "owner_label": owner_label(owner, caller),
            "mine": mine,
            "can_review": reviewable,
        })
    return _ok(f"{len(shown)} suggested change(s)", suggestions=shown)


async def resolve_suggestion(run: Runner, caller: Caller, path: Any, decision: Any) -> dict:
    """Approve (apply it), reject, or withdraw (the person who sent it) a suggestion."""
    target = clean_path(path)
    if decision not in ("approve", "reject", "withdraw"):
        return _fail("Choose approve, reject or withdraw.")
    if not is_suggestion_path(target):
        return _fail("That is not a suggested change.")
    text, _, res = await _read(run, caller.house_creds, target)
    if text is None:
        return res
    suggestion = parse_suggestion(text)
    if suggestion is None:
        return _fail("That suggestion could not be read.")
    recipe = await read_recipe(run, caller, suggestion["recipe"])
    found = _succeeded(recipe)

    if decision == "withdraw":
        if suggestion["by"] != caller.user:
            return _fail("Only the person who suggested it can withdraw it.", forbidden=True)
    else:
        allowed = can_review(recipe["detail"]["owner"], caller) if found else caller.is_admin
        if not allowed:
            return _fail("Only the recipe's owner or an admin can decide on this change.", forbidden=True)
    if decision == "approve":
        if not found:
            return _fail("That recipe is no longer in the cookbook, so the change cannot be applied.")
        detail = recipe["detail"]
        write = await run(caller.house_creds, _note(
            "write", title=detail["title"], category=COOKBOOK_DIR, path=detail["path"],
            content=with_owner(suggestion["content"], detail["owner"]),
        ))
        if not _succeeded(write):
            return write
    gone = await run(caller.house_creds, _note("delete", title=target.rsplit("/", 1)[-1][:-3], path=target))
    if not _succeeded(gone) and decision != "approve":
        return gone
    message = {"approve": "Change approved.", "reject": "Change turned down.", "withdraw": "Suggestion withdrawn."}
    return _ok(message[decision], recipe=suggestion["recipe"])


async def personal_recipes(run: Runner, caller: Caller) -> list[dict]:
    """Recipes still in the caller's own Recipes/ folder, from before the cookbook."""
    if caller.user == caller.house:
        return []
    notes, _ = await _listing(run, caller.own_creds, COOKBOOK_DIR)
    return [n for n in notes or [] if is_recipe_path(n.get("path"))]


async def adopt(run: Runner, caller: Caller) -> dict:
    """Move the caller's own recipes into the cookbook, owned by them.

    Each is written to the cookbook first (with its attachments) and only then
    removed from the caller's folder. One already in the cookbook under that
    name, or one whose attachments could not be copied, stays where it is.
    """
    moved: list[str] = []
    kept: list[dict] = []
    for note in await personal_recipes(run, caller):
        path = clean_path(note.get("path"))
        text, _, _ = await _read(run, caller.own_creds, path)
        if text is None:
            kept.append({"title": note.get("title"), "reason": "could not be read"})
            continue
        title = stored_title(text) or str(note.get("title") or path.rsplit("/", 1)[-1][:-3])
        stamped = with_owner(strip_stored_header(text), caller.user)
        body = stamped
        created = await _create(run, caller, title, body)
        if not _succeeded(created):
            kept.append({"title": title, "reason": created.get("message") or "could not be copied"})
            continue
        new_path = clean_path((created.get("detail") or {}).get("path"))
        copied_all = True
        for link in attachment_links(body):
            got = await run(caller.own_creds, _note("attachment_read", path=path, attachment=link))
            data = (got.get("detail") or {}).get("data") if _succeeded(got) else None
            put = await run(caller.house_creds, _note(
                "attachment_upload", path=new_path, filename=link.rsplit("/", 1)[-1], data=data,
            )) if data else {}
            new_link = (put.get("detail") or {}).get("link") if _succeeded(put) else None
            if not new_link:
                copied_all = False
                continue
            body = body.replace(link, new_link)
        if body != stamped:
            await run(caller.house_creds, _note(
                "write", title=title, category=COOKBOOK_DIR, path=new_path, content=body,
            ))
        if not copied_all:
            kept.append({"title": title, "reason": "a photo could not be copied, so your copy was kept"})
            continue
        await run(caller.own_creds, _note("delete", title=title, path=path))
        moved.append(title)
    message = f"Moved {len(moved)} recipe(s) into the family cookbook."
    if kept:
        message += f" {len(kept)} stayed in your folder."
    return _ok(message, moved=moved, kept=kept)
