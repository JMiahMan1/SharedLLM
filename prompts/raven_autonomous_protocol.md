# raven autonomous protocol — Template

This is a fill-in-the-blank template. It is tracked in git as a reference.
The actual prompt content goes in `prompts/raven_autonomous_protocol.md` (git-ignored).

## How to use

1. **Copy and populate:**

   ```bash
   cp prompts/raven_autonomous_protocol.md.sample prompts/raven_autonomous_protocol.md
   ```

   Then open `prompts/raven_autonomous_protocol.md` and replace the `[ ... ]` placeholders below
   with your actual prompt content.

2. **Deploy:** `deploy_remote.sh` copies `.md` files (not `.sample`) to the
   server. `deploy.sh` force-reseeds the Identity database from them.

3. **Verify:** After seeding, check the prompt loaded:

   ```bash
   docker exec sharedllm_identity sqlite3 /data/identity.db \
     "SELECT length(value) FROM globalsetting WHERE key='raven_autonomous_protocol';" \
     > /tmp/check_prompt.txt
   cat /tmp/check_prompt.txt
   ```

   A result of `0` means the prompt was not seeded. Force-reseed:

   ```bash
   curl -X POST "http://localhost:8001/api/admin/seed?force=true" \
     -H "X-Internal-Secret: <your_secret>"
   ```

---

## Fill-in-the-Blank Template

> [Replace everything below this line with the actual prompt content]
>
> [Paste the system prompt here. This is what the LLM receives as its
> instructions at the start of every session.]
>
> [The content should be complete and self-contained. Do not reference
> external files or variables that the LLM cannot access.]
>
> [Keep prompts focused — include only instructions relevant to this
> specific role (assistant, librarian, code helper, etc.).]

## Best Practices for Writing System Prompts

### Structure

- **Identity first:** Start with "You are..." — clearly define who the model is
- **Rules in order:** List instructions from most important to least
- **Use sections:** Group related instructions with `##` headings
- **Be explicit:** Say "do X" not "try to X" or "it would be nice to X"

### What to Include

- **Role & identity:** The model's name, personality, worldview
- **Scope:** What the model should handle vs. defer to other systems
- **Tone:** Formal, casual, technical, friendly — be specific
- **Constraints:** What the model must NEVER do (hallucinate, guess credentials)
- **Format:** How the model should structure responses (JSON, plain text, etc.)
- **Fallbacks:** What to do when uncertain or when the request is out of scope

### What to Avoid

- `[ ]` or `{placeholders}` — these confuse the model
- References to files or paths the model cannot see
- Contradictory instructions (e.g., "be concise" and "give full explanations")
- Instructions better handled by code (e.g., "look up the current time" —
  use tools, not prompt text)
- Overly long prompts (>4K tokens) — they dilute important instructions

### Prompt Engineering Tips

1. **Negative instructions last:** Models attend more to instructions at the
   beginning and end of prompts
2. **One concept per line:** Easier to maintain and less ambiguous
3. **Use examples sparingly:** One clear example beats three confusing ones
4. **Anchor to tools:** Reference specific tool names and capabilities, not
   abstract concepts ("use `workspace_shell`" not "execute commands")
5. **Test iteratively:** Change one thing at a time and verify the result
6. **Version comments:** Add `# v1.0 - 2024-06-28` at the top when updating

## Security

- **Never commit `raven_autonomous_protocol.md`** — it contains production prompt content
- Keep `raven_autonomous_protocol.md.sample` as a lightweight reference template only
- Actual prompts are seeded into the Identity `GlobalSettings` table at runtime
- If leaked, rotate the prompt and re-seed immediately

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Prompt missing from UI | Was `seed.py` run? Check `docker logs sharedllm_identity` |
| Prompt wrong/old | Force-reseed with `?force=true` in the seed URL |
| Model ignores instructions | Check prompt for contradictions; verify length > 0 in DB |
| Prompt too short/empty | Verify `raven_autonomous_protocol.md` has actual content (not just placeholders) |
