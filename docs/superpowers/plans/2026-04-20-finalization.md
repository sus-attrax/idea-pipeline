# Finalization Plan — Idea Pipeline System
**Date:** 2026-04-20  
**Branch:** main  
**Status:** Ready to execute

## Context

The system is ~85% complete. Architecture, schemas, scoring (v2.1), research tiers (T1–T4), and generator (Path A/B) are all implemented and working. This plan closes the remaining gaps to make the system production-ready, self-documenting, and continuously usable.

**Current state:**
- 199 ideas in vault
- Research: T1=199, T2=50+, T3=52, T4=9
- Scoring: v2.1 active, but Top-4 bias exists (only 4 ideas have LLM-enriched intrinsics)
- Tier limits: T2=50 (should be 100 or pct-based), T3=10–20, T4=5
- Missing: dynamic tier limits, auto-push, tier-cascade for new ideas, `select-hypotheses` command, `full-report` command, updated docs

**Key files:**
- `src/idea_pipeline/cli.py` — main CLI (1500+ lines)
- `src/idea_pipeline/schemas.py` — Pydantic schemas
- `src/idea_pipeline/scoring.py` — v2.1 scoring
- `src/idea_pipeline/generator.py` — bottleneck → idea generator
- `src/idea_pipeline/research/web.py` — tier dispatcher
- `config/weights.yaml` — scoring weights
- `cache/research.db` — SQLite API cache
- `CLAUDE.md` — agent handoff doc (outdated, needs overhaul)
- `README.md` — 78-line German overview (needs overhaul)

**Vault path:** `~/vaults/idea-validation` (or `$IDEAPIPE_VAULT`)  
**Python venv:** `.venv` in project root  
**Important:** Score 1 = best, 6 = worst (inverted log scale). Vault typos (`credebility`, `umprella_problem`) are intentional — Pydantic aliases preserve them.

---

## Execution Rules

- Always `source .venv/bin/activate` before running any `ideapipe` command
- Test with `--dry-run` before any API call or vault write
- After each completed task: `git add . && git commit -m "..." && git push`
- Mark each checkbox `[x]` immediately after completion
- If a task fails, stop and diagnose before continuing

---

## Tasks

### TASK 1: Dynamic Tier Limits via config/tiers.yaml
**Goal:** Replace hardcoded tier defaults with percentage-based scaling from a config file, so the system adapts to any vault size.

- [ ] Create `config/tiers.yaml`:
```yaml
# Tier limits — n = absolute max, pct = fraction of vault size
# Resolved limit = min(n, pct * vault_size) when both set
# --limit flag always overrides
tiers:
  t1:
    limit: null          # all ideas
  t2:
    limit: 100
    pct: 0.50
  t3:
    limit: 50
    pct: 0.25
  t4:
    limit: 25
    pct: 0.12
  t5:
    limit: 5
    pct: 0.03
```

- [ ] Add `load_tiers_config() -> dict` in `src/idea_pipeline/settings.py` that reads `config/tiers.yaml` relative to project root (use `importlib.resources` or `Path(__file__).parent.parent.parent / "config/tiers.yaml"`)

- [ ] Add `resolve_tier_limit(tier: int, vault_size: int, explicit_limit: int | None) -> int` in `src/idea_pipeline/research/web.py`:
  - If `explicit_limit` is not None: return it (CLI `--limit` flag wins)
  - Otherwise: load tiers config, compute `min(n, int(vault_size * pct))` for whichever fields are set
  - If neither set: return vault_size (no limit)

- [ ] In `cli.py` research command: call `resolve_tier_limit()` before the research loop. Print resolved limit to user: `"T2 limit: 47 ideas (50% of 94 vault ideas, config max: 100)"`

- [ ] Smoke test: `ideapipe research --tier 2 --dry-run` — verify printed limit is reasonable

- [ ] Commit: `"feat: dynamic tier limits from config/tiers.yaml — pct-based scaling"`

---

### TASK 2: Auto-Push after Research + Leaderboard Generation
**Goal:** After each research tier run (non-dry-run), automatically generate the tier-specific leaderboard and push to git.

- [ ] Add helper function `_auto_commit_and_push(tier: int, n_researched: int, leaderboard_path: Path)` in `cli.py`:
  - Calls `ideapipe report --min-tier {tier} -o LEADERBOARD_T{tier}.md` (via internal function call, not subprocess)
  - Runs `subprocess.run(["git", "add", str(leaderboard_path), "vault/"], check=False)`
  - Runs `subprocess.run(["git", "commit", "-m", f"research: T{tier} run — {n_researched} ideas researched"], check=False)`
  - Runs `subprocess.run(["git", "push"], check=False)`
  - Prints result to user

- [ ] Add `--no-auto-push` flag to research command (bool, default False). When False and not dry-run: call `_auto_commit_and_push()` at end of run.

- [ ] Only trigger for tier >= 2 (T1 doesn't generate a meaningful separate leaderboard)

- [ ] Test: `ideapipe research --tier 2 --dry-run` — verify no push triggered

- [ ] Commit: `"feat: auto-push after research run with tier-specific leaderboard"`

---

### TASK 3: Tier-Cascade for Generated/Ingested Ideas
**Goal:** When `ideapipe generate` creates new ideas, automatically run them through T1, and advance through T2/T3/T4 only if they rank in the top bracket.

- [ ] Add `--cascade / --no-cascade` flag to generate command (default: `--cascade`)

- [ ] After vault write of new ideas (when `--cascade` and not `--dry-run`):
  1. Collect `new_idea_ids` (list of slugs just written)
  2. Run T1 research: `research(tier=1, include=new_idea_ids)`
  3. Re-score full vault
  4. Check rank of each new idea in current leaderboard
  5. Resolve T2 limit via `resolve_tier_limit(2, vault_size, None)`
  6. If idea rank ≤ T2 limit: run T2 research (`research(tier=2, include=[id])`)
  7. Re-score; check rank against T3 limit; if within: run T3
  8. Re-score; check rank against T4 limit; if within: run T4
  9. After each tier: print "idea_id advanced to T{n} (rank #{r})" or "idea_id stopped at T{n-1} (rank #{r}, limit={limit})"

- [ ] Same cascade logic for `ingest` command with new `--cascade` flag (default off for ingest — user must opt in explicitly)

- [ ] Test: `ideapipe generate --domain "test domain" --dry-run` — verify cascade logic preview prints

- [ ] Commit: `"feat: tier-cascade for generated ideas — auto-advance through tiers by rank"`

---

### TASK 4: select-hypotheses Command
**Goal:** New command that picks 5–10 diverse hypotheses from T4 leaderboard across different business/life domains.

- [ ] Add `@app.command()` `select_hypotheses` in `cli.py`:
```python
@app.command()
def select_hypotheses(
    vault: Path = vault_option,
    n: int = typer.Option(8, "--n", help="Number of hypotheses to select"),
    min_tier: int = typer.Option(4, "--min-tier"),
    out: Path = typer.Option(Path("HYPOTHESES.md"), "--out", "-o"),
    dry_run: bool = dry_run_option,
):
```

- [ ] Algorithm:
  1. Load all ideas with `research_fidelity >= min_tier`, sorted by score descending
  2. Classify each idea into a domain using a single Haiku batch call (10 ideas per batch):
     - Prompt: "Given this idea title and description, classify into exactly one of: B2B_SaaS, B2C_App, Marketplace, DeepTech, Sustainability, BioTech, AgriTech, FinTech, EdTech, HealthTech, Hardware, Services, Other. Return JSON: {id: domain}"
     - Cache domain classifications in a local dict (don't re-classify same idea twice)
  3. Greedy diversity selection: iterate top ideas, select first unseen domain, stop at `n`
  4. Fallback: if fewer than `n` domains available, allow repeats starting from most-represented domains

- [ ] Generate `HYPOTHESES.md`:
```markdown
# Idea Pipeline — Working Hypotheses
Generated: {date} | Source: T{min_tier}+ leaderboard | Selected: {n} diverse ideas

---

## Hypothesis #1: {idea_id}
**Score:** {score} | **Tier:** T{fidelity} | **Domain:** {domain}
**Capital:** {capital_class} | **Regulation:** {regulation_class}

### Why this hypothesis?
{score_breakdown summary in plain language}

### What we know (Research Findings)
{all available narrative fields concatenated: T2, T3, T4 narratives}

### Linked Problems
{chancen IDs and their descriptions}

### Founder Fit
Mastery: {mastery_leverage:.0%} | Obsession: {obsession_leverage:.0%} | Cross-domain: {cross_domain_flag}
Relevant knowledge: {linked wissen IDs}

### Recommended Next Steps
- [ ] Autoresearch (T5): deepen counter-arguments and competitor analysis
- [ ] Expert interview: identify 3 domain experts via LinkedIn/network
- [ ] Customer discovery: 5 conversations with {first_adopters}
- [ ] Build/buy/partner decision: {capital_class} → {recommended_path}

---
```

- [ ] In dry-run mode: print selected idea IDs and domains without writing file

- [ ] Commit: `"feat: select-hypotheses command — diverse T4 picks to HYPOTHESES.md"`

---

### TASK 5: full-report Command
**Goal:** New command generating a detailed per-idea report for T4 ideas, with all collected information, so users can understand scores and review research without reading raw vault files.

- [ ] Create `src/idea_pipeline/report.py` with `build_full_report(ideas: list[IdeeNote], vault_path: Path) -> str`

- [ ] Add `@app.command()` `full_report` in `cli.py`:
```python
@app.command()
def full_report(
    vault: Path = vault_option,
    min_tier: int = typer.Option(4, "--min-tier"),
    limit: int = typer.Option(25, "--limit"),
    out: Path = typer.Option(Path("FULL_REPORT.md"), "--out", "-o"),
    dry_run: bool = dry_run_option,
):
```

- [ ] Output format per idea:
```markdown
## Rank #{rank}: {idea_id}
**Score: {score:.3f}** | Tier: T{fidelity} | Capital: {capital_class} | Regulation: {regulation_class}

### Score Breakdown
| Dimension      | Score | Weight | Contribution |
|----------------|-------|--------|--------------|
| Market         | {x}   | 35%    | {x*0.35}     |
| Fit            | {x}   | 28%    | {x*0.28}     |
| Chance         | {x}   | 20%    | {x*0.20}     |
| Attractiveness | {x}   | 17%    | {x*0.17}     |

### Key Signals
- Willingness to Pay: {wtp_stars}/6
- Mastery Leverage: {mastery_bar} {mastery_pct}%
- Obsession Leverage: {obsession_bar} {obsession_pct}%
- Cross-Domain Bonus: {✓/✗}
- Killer Flag: {✓ BLOCKED/✗ clear}

### Research Findings
**T1:** Market size {market_size}/6, Potential {market_potential}/6, Prevalence {prevalence}/6, Awareness {market_awareness}/6

**T2 (Claude + Web Search):**
> {research narrative — field: check schema for actual field name storing T2 narrative}

**T3 (Perplexity):** *(if available)*
> {T3 narrative}

**T4 (Firecrawl):** *(if available)*
> {T4 narrative}

**Research Notes (T5/manual):** *(if available)*
> {research_notes}

### Linked Problem Fields
{for each chance: "- **{chance_id}** — {chance.description} | Urgency: {urgency}/6, Prevalence: {prevalence}/6, Impact: {impact}/6"}

### Knowledge Areas
{for each wissen: "- **{wissen_id}** — Confidence: {confidence}/6, Enjoyment: {enjoyment}/6, Credibility: {credibility}/6"}

### Idea Notes
{idea.notes if non-empty}

{if generated_from: "### Generation Context\nGenerated from: {generated_from}\nBottleneck: {generation_bottleneck}"}

---
```

- [ ] For narrative fields: read the actual schema fields. Check `schemas.py` for field names that store T2/T3/T4 narratives (likely in `score_breakdown` or a research-specific field). Adapt rendering to actual field names — do NOT assume.

- [ ] Load linked ChanceNotes and WissenNotes from vault to show their details (not just IDs)

- [ ] In dry-run: print first 3 ideas as preview, report total ideas that would be included

- [ ] Commit: `"feat: full-report command — per-idea narrative + score breakdown for T4 leaderboard"`

---

### TASK 6: Enrich-Intrinsic on Full Corpus
**Goal:** Fix the Top-4 bias. Currently only 4 ideas have LLM-enriched intrinsic scores; all others default to 6 (worst), artificially inflating the top 4.

- [ ] First check how many ideas still need enrichment:
```bash
ideapipe vault doctor
```
Count ideas where `attractiveness_impact == 6` AND `attractiveness_innovativeness == 6` AND `attractiveness_mission_fit == 6` (these are the un-enriched ones).

- [ ] Run in batches of 30 to avoid timeouts:
```bash
ideapipe enrich-intrinsic --vault ~/vaults/idea-validation --limit 30
# wait for completion, then repeat
ideapipe enrich-intrinsic --vault ~/vaults/idea-validation --limit 30
# repeat until doctor shows no un-enriched ideas
```

- [ ] If `--force` is needed to re-run already-enriched ideas (check if top 4 need re-enrichment): add `--force` flag

- [ ] After all batches complete, re-score and regenerate all leaderboards:
```bash
ideapipe score --vault ~/vaults/idea-validation --trigger "post-intrinsic-full-corpus"
ideapipe report --vault ~/vaults/idea-validation -o LEADERBOARD.md
ideapipe report --vault ~/vaults/idea-validation -o LEADERBOARD_T3.md --min-tier 3
ideapipe report --vault ~/vaults/idea-validation -o LEADERBOARD_T4.md --min-tier 4
```

- [ ] Commit: `"feat: enrich-intrinsic full corpus — top-4 bias corrected, leaderboards updated"`

---

### TASK 7: CLAUDE.md Overhaul
**Goal:** Replace outdated CLAUDE.md (currently says "Step 6 next") with accurate, machine-readable agent handoff.

- [ ] Replace full content of `CLAUDE.md` with:

```markdown
# Idea Pipeline — Agent Handoff

## Current State
*(Update this section at the end of every work session)*
- 199+ ideas in vault, scoring v2.1 active
- Research: T1=all, T2=100 (or pct-limited), T3=52, T4=25
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
Finalization plan executed: Tasks 1–7 complete. Task 8 (README) remaining.
```

- [ ] Commit: `"docs: overhaul CLAUDE.md — complete agent handoff, current state, extension guide"`

---

### TASK 8: README Overhaul
**Goal:** Replace 78-line German README with comprehensive English documentation readable by humans and AI agents.

- [ ] Replace full content of `README.md` with:

```markdown
# Idea Pipeline

A multi-tier research and scoring system for founder-specific idea validation. Ingests business ideas, enriches them through progressive API tiers (Tavily → Claude → Perplexity → Firecrawl), scores them across four dimensions (market fit, founder fit, opportunity quality, attractiveness), and surfaces the most relevant, realistic, and profitable opportunities. Includes a bottleneck-driven idea generator.

## System Overview

```
Ideas (ingest)
    → T1 Tavily: snippet scoring, all ideas, cheap pre-sort
    → T2 Claude+Web: narrative + market data, top 100
    → T3 Perplexity: deep research, top 50
    → T4 Firecrawl: full-page scrape analysis, top 25
    → select-hypotheses: 5–10 diverse picks → HYPOTHESES.md
    → full-report: all findings → FULL_REPORT.md

Generator (bottleneck analysis → new ideas → cascade through tiers)
    Path A: --domain "myzel leather"   (user-specified domain)
    Path B: --from-vault               (auto: high-market low-fit ideas)
```

## Scoring Model (v2.1)

`score = 0.35 × market + 0.28 × fit + 0.20 × chance + 0.17 × attractiveness`

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Market | 35% | Willingness to pay, market size, potential, awareness |
| Fit | 28% | Difficulty, time-to-revenue, personal knowledge leverage |
| Chance | 20% | Linked problem field quality (urgency, prevalence, impact) |
| Attractiveness | 17% | Impact, innovativeness, mission fit |

Scale: 1 = best, 6 = worst (inverted log scale internally).

## Research Tiers

| Tier | Tool | Ideas | Cost/idea | Output |
|------|------|-------|-----------|--------|
| T1 | Tavily | All | ~$0.01 | 4 market scores from snippets |
| T2 | Claude + Web Search | Top 100 | ~$0.20 | Scores + narrative (market data, CAGR, competitors) |
| T3 | Perplexity sonar-pro | Top 50 | ~$0.75 | Scores + deep narrative |
| T4 | Firecrawl | Top 25 | ~2 credits | Scores + narrative from full web pages |
| T5 | Autonomous loop | Top 5 | ~$3.00 | Qualitative research notes |

Limits scale automatically with vault size (see `config/tiers.yaml`).

## Setup

```bash
git clone <repo> && cd idea-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Create .env with API keys
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...
FIRECRAWL_API_KEY=fc-...
PERPLEXITY_API_KEY=pplx-...  # optional, needed for T3

export IDEAPIPE_VAULT=~/vaults/idea-validation
```

## Full Pipeline Run

```bash
# 1. Add ideas
ideapipe ingest "My Idea: one-line description"
ideapipe ingest --file ideas.txt          # batch

# 2. Enrich and link
ideapipe enrich                           # generate problem fields
ideapipe link                             # match to knowledge areas
ideapipe enrich-intrinsic                 # LLM: attractiveness, fit, gates

# 3. Score and research
ideapipe score
ideapipe research --tier 1               # all ideas
ideapipe research --tier 2               # top 100 (auto-pushes leaderboard)
ideapipe research --tier 3               # top 50
ideapipe research --tier 4               # top 25

# 4. Synthesize
ideapipe select-hypotheses               # pick 5–10 diverse T4 ideas
ideapipe full-report                     # detailed report with all findings
```

## Idea Generation

```bash
# Path A: analyze a domain, find bottleneck, generate ideas
ideapipe generate --domain "myzel leather"

# Path B: auto-select hard-to-execute high-potential vault ideas
ideapipe generate --from-vault

# Both paths cascade new ideas through tiers automatically
# New idea → T1 → if top 100 → T2 → if top 50 → T3 → if top 25 → T4
```

## Leaderboards

| File | Contents |
|------|----------|
| `LEADERBOARD.md` | All 199+ ideas ranked by v2.1 score |
| `LEADERBOARD_T3.md` | Only T3+ researched ideas |
| `LEADERBOARD_T4.md` | Only T4+ researched ideas |
| `HYPOTHESES.md` | 5–10 diverse working hypotheses with next steps |
| `FULL_REPORT.md` | Per-idea detailed report with all research findings |

## Utilities

```bash
ideapipe vault doctor          # data quality check (broken links, unscored)
ideapipe cache-stats           # API cache size and entry count
ideapipe compare-versions      # v1 vs v2.1 score comparison
ideapipe info                  # vault path, idea count, system info
```

## Vault Structure

Markdown + YAML frontmatter files (Obsidian-compatible):

```
vault/
  Ideen/      — IdeeNote: business ideas with scores and research
  Chancen/    — ChanceNote: problem/opportunity fields
  Wissen/     — WissenNote: personal knowledge areas
```

## Extension & Agent Handoff

See `CLAUDE.md` for the full extension guide, invariants, and current system state. All plans in `docs/superpowers/plans/`.
```

- [ ] Commit: `"docs: overhaul README — comprehensive system documentation in English"`

---

### TASK 9: Final Verification
**Goal:** Confirm everything works end-to-end.

- [ ] Run full test suite:
```bash
pytest tests/ -v
```
All tests must pass.

- [ ] Smoke test all new commands:
```bash
ideapipe research --tier 2 --dry-run    # verify dynamic limit printed
ideapipe generate --domain "test" --dry-run  # verify cascade preview
ideapipe select-hypotheses --dry-run    # verify domain classification preview
ideapipe full-report --dry-run          # verify 3-idea preview
```

- [ ] Run vault doctor:
```bash
ideapipe vault doctor
```
No broken links, no schema errors.

- [ ] Update CLAUDE.md "Where We Left Off" section:
```
Finalization plan fully executed (2026-04-20). All 9 tasks complete.
Recommended next steps:
- Run ideapipe select-hypotheses and commit HYPOTHESES.md
- Run ideapipe full-report and commit FULL_REPORT.md  
- Replenish Firecrawl credits, run T4 on remaining top-25 ideas
- Replenish Perplexity credits, run T3 on remaining top-50 ideas
- For top 3 hypotheses: run T5 autoresearch
```

- [ ] Final commit:
```bash
git add . && git commit -m "feat: finalization complete — dynamic tiers, cascade, hypotheses, full-report, docs" && git push
```

---

## Summary of Changes

| Task | What changes | Files |
|------|-------------|-------|
| 1 | Dynamic tier limits | `config/tiers.yaml` (new), `settings.py`, `research/web.py`, `cli.py` |
| 2 | Auto-push after research | `cli.py` |
| 3 | Tier-cascade for new ideas | `cli.py` |
| 4 | select-hypotheses command | `cli.py` |
| 5 | full-report command | `src/idea_pipeline/report.py` (new), `cli.py` |
| 6 | Enrich-intrinsic full corpus | vault files, leaderboards |
| 7 | CLAUDE.md overhaul | `CLAUDE.md` |
| 8 | README overhaul | `README.md` |
| 9 | Verification | — |
