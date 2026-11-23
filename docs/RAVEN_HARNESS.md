# Raven harness

How a Raven mission keeps a local model within its context window and recovers when it does not.

## Fuel gauge (context budgeting)

- Every turn the mission estimates its tokens (4 characters a token plus a 10 percent margin, the same
  estimate as `prompt_budget.py`) against a budget of `num_ctx - num_predict - 1024`.
- Above 70 percent of the budget the conversation is compacted: older assistant file writes and big
  old tool results become short stubs, the newest turns that fit in about 35 percent of the budget
  stay verbatim (the current tool result always stays whole), and everything older becomes one
  bounded "Mission log so far" note (about 1,500 tokens) that keeps the newest entries. The note
  replaces the previous one; it never stacks.
- Each turn records a `fuel` event (`fuel_used`, `fuel_budget`) in the mission's event stream, so the
  UI and logs can show how close a mission is to the limit.

## Overflow recovery

When the model server answers that the request exceeds its context (llama.cpp
`exceed_context_size_error`, OpenAI-style `context_length_exceeded`), the mission does not resend the
same messages: it compacts hard to 50 percent of the budget (keeping at most the last four turns, and
always the tail of the newest one) and retries once. A second overflow ends the mission with the
reason "Context window exceeded".

## Field kit (workspace tools)
> The workspace tools Raven (and the single-turn executor) call are not
> generic wrappers around the shell. They are a small, precise toolkit
> designed to keep Raven's context window tight and its edits correct.
> Every tool has hard caps, exact-text semantics and a short, structured
> reply. This doc describes those choices so Raven, reviewers and anyone
> reading a mission log can tell why the tool answered the way it did.
> Code: `services/execution/handlers/workspace.py`,
> `services/execution/schemas.py`, `services/gateway/schemas.py`.

### Patch (`workspace_patch`)

**Exact-text, all-or-nothing.** Each chunk carries an `old_text`. The handler
looks for that *exact* string in the target file:

- Not found at all → `400 "old_text not found"`
- Found more than once → `400 "old_text found N times: add more surrounding lines to disambiguate"`
- Found exactly once → replace it.

When `replace_all: true` is set on a chunk, every occurrence is replaced; the
"found more than once" error is bypassed.

Chunks are applied **all at once** (all-or-nothing). If any chunk would fail,
the whole patch is rejected and no file is written, so a partial edit never
lands.

The reply is a **short unified diff** (max 60 lines) so the model sees
exactly what changed without re-reading the whole file.

```python
# Example chunk
{"old_text": "    return 42", "new_text": "    return prompt.count('!')"}
# replace_all defaults to false per chunk
```

**Why:** Raven's context is expensive. Returning 200 lines of diff when a
one-line change was made wastes tokens and buries the signal. Exact-text
matching prevents accidental replacements in look-alike files.

### Read (`workspace_read`)

**Numbered lines, paged.** Every line is prefixed with its 1-based index:

```
   1| def hello():
   2|     print("hello")
   3|
   4| if __name__ == "__main__":
   5|     hello()
```

The reply carries `total_lines` and `next_offset` so the model knows exactly
how much is left and where to page next. The default `limit_lines` is 300;
reads larger than that return the first 300 lines and a `next_offset`. The
model pages by sending `offset` until `next_offset` is null.

**Why:** Raven cannot afford to read a 5 000-line file in one call. Paging
keeps every turn bounded and lets the model jump straight to the region it
needs with `offset`.

### Search (`workspace_search`)

**Bounded match count.** The default `max_results` is 100. When there are
more matches, the reply says so explicitly ("N more matches not shown").
The `glob` parameter lets the model narrow the search to a file pattern
before it fires, saving both time and tokens.

```python
# Search only Python files
{"pattern": "def test_", "glob": "**/*.py", "max_results": 50}
```

**Why:** A search across an entire workspace can return thousands of hits.
Without a cap the reply is unusable; without `glob` the model searches
`.git` dirs, node_modules and generated files by mistake.

### List files (`workspace_list`)

Returns the workspace's file tree (relative paths, no content) so the model
can navigate without reading anything. Hidden directories (`.` and `..`) and
git-ignored paths are excluded.

**Why:** Raven needs to know what exists before it can patch or read it. A
lightweight listing is cheaper than a full tree walk inside the sandbox.

### Shell (`workspace_shell`)

**Stdout and stderr capped separately.** Each stream has its own cap
(default 4 000 characters). When a stream exceeds the cap, the reply keeps
the tail and says how much was cut. The **full** output (both streams
concatenated) is saved to `.raven/last_output.txt` inside the workspace,
and the reply mentions the file path so the model can read it with
`workspace_read` when it needs the complete picture.

**Why:** Long command output (stack traces, verbose test runs) blows up the
turn. Capping the inline reply keeps the conversation tight; the full dump
in `.raven/last_output.txt` is there when the model genuinely needs it.

**Safety:** Dangerous system commands (`rm -rf /`, `mkfs`, raw device writes)
are refused by the sandbox before they run. The shell runs as the workspace's
non-root user (`raven`, uid 1000) inside the sandbox container.

### What the field kit refuses

- **No fuzzy matching on patch `old_text`.** If the text does not match
  exactly, the patch fails. The error message names the count and suggests
  adding more surrounding lines.
- **No silent truncation.** Every cap is named in the reply ("6 000 of
  12 340 characters kept").
- **No partial patches.** A chunk failure rolls back the entire patch.
- **No uncapped searches.** The model must set `max_results` (default 100);
  it cannot ask for "all matches".
- **No bare root commands.** The sandbox refuses anything that writes to
  `/`, `/dev`, `/proc` or that runs as root.

### Where it is tested

All cases are covered by
`services/execution/tests/test_workspace_field_kit.py` (15 tests):

| Test | What it verifies |
|---|---|
| `test_patch_exact_text_once` | Exact match replaces, reply is a short unified diff |
| `test_patch_not_found` | Missing `old_text` returns 400 |
| `test_patch_ambiguous` | Duplicate `old_text` returns 400 with count |
| `test_patch_replace_all` | `replace_all: true` replaces every occurrence |
| `test_patch_all_or_nothing` | Second-chunk failure leaves file unchanged |
| `test_read_numbered_lines` | Output is line-numbered, carries `total_lines` and `next_offset` |
| `test_read_paging` | `limit_lines` pages the result correctly |
| `test_read_large_file` | Files over 300 lines return `next_offset` |
| `test_search_bounded` | `max_results` caps the match count |
| `test_search_with_glob` | `glob` narrows the search to matching files |
| `test_search_excess_noted` | Reply names "N more matches not shown" when capped |
| `test_list_files` | Returns relative paths, excludes hidden/gitignored |
| `test_shell_output_capped` | Stdout/stderr capped with tail kept |
| `test_shell_full_dump` | Full output saved to `.raven/last_output.txt` |
| `test_pdf_read_numbered` | PDFs are read in numbered-line format |

## Next

Field kit (exact unique edits with diffs, numbered reads, capped search and output), then mission
discipline (a live objectives checklist, a verified MissionComplete debrief, waypoint checkpoints
with rollback, cancel and retry on a hung inference). A harness benchmark compares Raven missions
with opencode on the same tasks so progress is measured.
