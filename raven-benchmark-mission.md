# Raven Benchmark Learning Mission

Submit via: `POST /api/raven/missions` with the query below.
Model: `qwen3.6-35b-a3b:q4_k_m` (Qwen 3.6 MTP)
Workspace: your choosing (leave empty for auto-assigned)

---

## Mission Query (paste as the `query` field)

```
You are Raven, an agentic LLM with persistent memory. Your teacher has just
completed a benchmark curriculum session with you. Your job in this mission is
three-fold: STUDY, SELF-TEST, and REPORT.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 1 — STUDY: Internalize the benchmark curriculum below
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Your teacher will present Q&A pairs drawn from the Alpaca benchmark suite
(coding, knowledge, reasoning, math, logic, and more). For each Q&A pair:
  1. Understand the rule, concept, or answer.
  2. Note WHY the answer is correct (not just WHAT it is).
  3. If you are uncertain, flag it as a GAP to revisit later.

Here is the benchmark curriculum your teacher will present (live, in the chat):

[TEACHER: Paste benchmark Q&A pairs here. Format each as:]
Q: <question>
A: <correct answer>
[End of pair]

[TEACHER: You will present 5-10 Q&A pairs from categories like:
- coding: debug_find, refactor, game_tasks
- knowledge: MMLU, GPQA-Diamond, HLE, Math-Hard, IFEval
- reasoning: logic puzzles, train problems
- math_hard: combinatorics, algebra, geometry
- logic: Knights and Knaves, wolf-goat-cabbage
Keep each Q&A pair concise. Focus on concepts Raven should remember.]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 2 — SELF-TEST: Prove you learned the material
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After studying all Q&A pairs, answer WITHOUT looking at the answers.
For EACH question your teacher presented:
  - Restate the question in your own words (proves you understood it).
  - Give your answer.
  - Explain your reasoning step-by-step (proves depth of learning).
  - Rate your confidence: HIGH / MEDIUM / LOW.

If you rate any answer LOW confidence, flag it for re-study.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 3 — RSI REPORT: What Raven learned, gaps, and next steps
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Produce a structured report:
  ## Learned (HIGH confidence — solid)
  [List each concept with one-line summary]

  ## Gaps (MEDIUM/LOW confidence — needs reinforcement)
  [List each gap with the original Q&A and why it's weak]

  ## Validation Checklist
  [For each gap, propose a concrete task Raven should attempt
   on its next mission to validate it (e.g. "Solve 3 combinatorics
   problems from memory" or "Explain the Knights and Knaves
   rule to your teacher without notes")]

  ## Memory Trace
  Save the key rules and answers to your workspace memory file
  (raven_memory.md) under sections by category. This is your
  persistent study notes.

Rules:
  - Cite nothing you cannot explain from first principles.
  - If you realize you got something wrong during self-test,
    CORRECT yourself and flag it — honesty improves learning.
  - Use Apply: [benchmark-{category}] notation for any
    benchmark lesson you reference.
  - Save your report and memory trace to your workspace.
```

---

## Teacher Guide (instructions for the user)

When you submit this mission:

1. **During Phase 1 (Study):** Raven will wait for you to paste benchmark Q&A pairs. Paste them one at a time or in batches. Categories to mix:
   - coding (debugging, refactoring, game logic)
   - knowledge (MMLU, science, math)
   - reasoning (logic puzzles, trains, Knights and Knaves)
   - IfEval (constraint-following: "include word penguin")
   - math_hard (combinatorics, algebra)
   - GPQA-Diamond (deep science questions)

2. **During Phase 2 (Self-Test):** Raven will answer from memory. Don't help unless Raven explicitly asks for a hint. This is the real test.

3. **During Phase 3 (Report):** Raven will produce a structured learning report. Review it carefully:
   - ✅ CONFIRMED: Mark gaps that Raven validated correctly
   - ❌ REJECTED: Mark answers that are wrong — Raven should self-correct
   - 🔄 RE-STUDY: Flag items that need another teaching session

4. **After the mission:** Raven's report feeds into dreaming. Gaps become validation candidates for the next mission. Confirmed knowledge boosts priority scores. This is the RSI cycle.

---

## Expected Output Structure from Raven

```
[Phase 1 Complete] Studied N Q&A pairs across M categories.
[Phase 2 Complete] Self-test: X/Y correct, Z gaps flagged.
[Phase 3 Report]
## Learned
- ...
## Gaps
- ...
## Validation Checklist
- ...
## Memory Trace
[Saved to workspace raven_memory.md]
```
