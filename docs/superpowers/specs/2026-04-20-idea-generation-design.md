# Spec: `ideapipe generate` — Domain Bottleneck → Idea Generation

**Date:** 2026-04-20  
**Status:** Approved  
**Branch:** feature/scoring-v21 (implements after Task 9 T3 is running; bottleneck-from-T3 is a follow-up feature)

---

## Goal

Given a rough domain (user input) or an existing vault idea with high market potential but poor fit, the pipeline identifies *where exactly* the bottleneck lies (production, market, regulation, technology) and generates 2–3 focused, actionable business idea candidates that address exactly that one limiting factor. Selected candidates are auto-scored (v2.1) and written into the vault, entering the normal leaderboard flow before T3 selection.

---

## Two Input Paths — One Pipeline

**Path A:** `--domain "myzel leder"` — user identifies a field with high potential but unresolved economic viability.

**Path B:** `--from-vault` — system auto-selects existing vault ideas where the v2.1 `market` dimension score is in the top quartile AND the `fit` dimension score is in the bottom quartile. These are ideas with a relevant problem but currently too hard to execute as-is. Default max 5 per run, configurable via `--limit`.

Both paths feed into the same bottleneck→generate pipeline.

---

## Pipeline

```
[Input Resolution]
  Path A: domain string(s)
  Path B: vault ideas matching high-market / low-fit threshold
         ↓
[T1] Tavily Search — quick market/problem context for domain
         ↓
[T2] Claude WebSearch — deeper context, existing solutions, failure modes
         ↓
[LLM Call 1] Bottleneck Analysis
  Input:  T1+T2 research data + linked vault context (chancen/wissen notes)
  Output: structured JSON per domain/idea:
    {
      "domain": str,
      "bottleneck": str,          # one-line diagnosis
      "type": "production|market|regulation|technology",
      "severity": "high|medium",
      "blocking_factor": str      # what specifically is blocked and why
    }
         ↓
[LLM Call 2] Idea Generation
  Input:  bottleneck JSON + vault wissen context (founder knowledge profile)
  Output: 2–3 IdeeNote-compatible JSON candidates, each focused on the bottleneck
         ↓
[Terminal Output]
  Bottleneck diagnosis + all candidates displayed together
  User selects interactively (1 / 2 / 3 / all / none)
  OR non-interactively via --select 1,3
         ↓
[v2.1 Auto-Score] selected candidates scored immediately
         ↓
[Vault Write] new IdeeNote files written; visible in leaderboard before T3
```

---

## CLI

```bash
# Path A
ideapipe generate --domain "myzel leder"
ideapipe generate --domain "mycorrhiza kommerziell" --dry-run

# Path B
ideapipe generate --from-vault
ideapipe generate --from-vault --limit 5

# Non-interactive selection
ideapipe generate --domain "..." --select 1,3
```

`--dry-run`: shows which domains/vault ideas would be processed, estimates API cost, writes nothing.

---

## Schema Changes

Two new optional fields on `IdeeNote` (backward-compatible, default `None`):

```python
generated_from: Optional[str] = None
# "domain:myzel leder" or "idea:<source_idea_id>"

generation_bottleneck: Optional[str] = None
# short bottleneck description, e.g. "Substratproduktion nicht skalierbar"
```

---

## Idempotency

Generated idea IDs are derived deterministically from `domain + bottleneck_hash`. Re-running the same domain skips already-generated ideas (same pattern as `enrich_intrinsic`).

---

## Error Handling

- T1/T2 API failure: abort current domain, continue others, summarize errors at end
- LLM returns invalid JSON: 1 retry, then skip with error log
- `--dry-run`: no API calls, no vault writes

---

## Implementation Location

- `src/idea_pipeline/generator.py` — main pipeline logic (stub already exists)
- `src/idea_pipeline/cli.py` — add `generate` command
- `config/prompts/generate/bottleneck_analysis.txt` — LLM prompt for Step 1
- `config/prompts/generate/idea_candidates.txt` — LLM prompt for Step 2
- `tests/test_generator.py` — unit tests

---

## Tests

- Unit: bottleneck JSON parser, deterministic ID generation, Path B vault candidate selection (market/fit thresholds)
- Integration: `--dry-run` on real vault (no API calls) verifies candidate selection logic
- No LLM mocking (consistent with rest of project)

---

## Cost Estimate

| Scenario | Est. Cost |
|---|---|
| Path A, 1 domain | ~$0.05–0.15 |
| Path B, 5 vault ideas | ~$0.25–0.75 |

---

## Out of Scope (Follow-up)

- **Bottleneck-from-T3:** After T3 research reveals new failure modes in top ideas, trigger bottleneck analysis on those findings to spawn new ideas. Separate feature, same `generator.py` module.
- **T3 top 50:** Extend T3 research cascade from top 25 → top 50 (parameter change in Task 9 of scoring-refactor plan).
