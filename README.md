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
# ideapipe research --tier 5               # optional, top 5 only, ~$3/idea

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
| `LEADERBOARD.md` | All ideas ranked by v2.1 score |
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
