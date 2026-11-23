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

## Next

Field kit (exact unique edits with diffs, numbered reads, capped search and output), then mission
discipline (a live objectives checklist, a verified MissionComplete debrief, waypoint checkpoints
with rollback, cancel and retry on a hung inference). A harness benchmark compares Raven missions
with opencode on the same tasks so progress is measured.
