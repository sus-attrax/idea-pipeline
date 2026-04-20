# Idea Pipeline — Agent Handoff

## Current State
*(Update this section at the end of every work session)*
- 199+ ideas in vault, scoring v2.1 active
- Research: T1=all, T2=100 (or pct-limited), T3=53, T4=9
- Enrich-intrinsic: full corpus run complete (no top-4 bias)
- Generator: implemented (Path A: --domain, Path B: --from-vault, --cascade active)
- Commands: ingest, enrich, link, enrich-intrinsic, score, research, report, generate, select-hypotheses, full-report

## Vault Path
`$IDEAPIPE_VAULT` or `~/vaults/idea-validation`

## Setup
```bash
source .venv/bin/activate
pip install -e .  # if dependencies changed
ideapipe info     # verify vault found
```

## Core Commands
```bash
ideapipe vault doctor              # data quality check
ideapipe score                     # re-score all ideas
ideapipe report -o LEADERBOARD.md  # regenerate leaderboard
ideapipe research --tier 2         # T2 research (dynamic limit)
ideapipe generate --domain "X"     # Path A: bottleneck → new idea
ideapipe generate --from-vault     # Path B: auto low-fit high-market
ideapipe select-hypotheses         # pick 5–10 diverse T4 hypotheses
ideapipe full-report               # detailed per-idea report for T4
```

## Architecture
```
src/idea_pipeline/
  cli.py           — Typer CLI (all commands)
  schemas.py       — Pydantic v2: IdeeNote, ChanceNote, WissenNote
  scoring.py       — v2.1: 4-dimension weighted scoring
  generator.py     — bottleneck analysis → idea generation
  vault_io.py      — atomic read/write, validation
  report.py        — full-report rendering
  research/
    web.py         — tier dispatcher + resolve_tier_limit()
    cache.py       — SQLite cache (cache/research.db)
    sources/
      tavily.py    — T1: snippet scoring (Haiku)
      claude_search.py — T2: Claude + web_search (Sonnet)
      perplexity.py    — T3: sonar-pro
      firecrawl.py     — T4: full-page scrape + Sonnet
      autoresearch.py  — T5: autonomous 3-loop
config/
  weights.yaml     — scoring weights (v2.1 active)
  tiers.yaml       — tier limits (n + pct-based)
  prompts/         — all LLM prompts (never hardcode in Python)
```

## Scoring Model (v2.1)
`score = 0.35 × market + 0.28 × fit + 0.20 × chance + 0.17 × attractiveness`
Scale: 1 = best, 6 = worst. Internal: inverted log scale.

## Tier Pipeline (configured in config/tiers.yaml)
- T1 Tavily: all ideas (free pre-sort)
- T2 Claude+Web: top 100 or 50% of vault
- T3 Perplexity: top 50 or 25% of vault
- T4 Firecrawl: top 25 or 12% of vault
- T4 → select-hypotheses: 5–10 diverse picks → HYPOTHESES.md
- T4 → full-report: all findings → FULL_REPORT.md

## Invariants (never break these)
1. Always `--dry-run` before real API call or vault write
2. All vault writes are atomic (vault_io.py)
3. Vault typos (`credebility`, `umprella_problem`) are intentional — Pydantic aliases preserve them
4. Score 1 = best, 6 = worst (do not flip)
5. Prompts in `config/prompts/`, never hardcoded
6. All API responses cached in `cache/research.db`
7. After every session: git add + commit + push

## Extension Guide
- New research tier: create `src/idea_pipeline/research/sources/new_source.py` (subclass `BaseResearcher`)
- New scoring field: add to `schemas.py`, update `scoring.py` formula, update `config/weights.yaml`
- New CLI command: add `@app.command()` to `cli.py`
- New prompt: add to `config/prompts/{tier}/filename.txt`, load path in source module

## Where We Left Off
*(Agent: update this line at session end)*
Finalization plan fully executed (2026-04-20). All 9 tasks complete.
Recommended next steps:
- Run ideapipe select-hypotheses and commit HYPOTHESES.md
- Run ideapipe full-report and commit FULL_REPORT.md
- Replenish Firecrawl credits, run T4 on remaining top-25 ideas
- Replenish Perplexity credits, run T3 on remaining top-50 ideas
- For top 3 hypotheses: run T5 autoresearch
