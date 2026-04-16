# idea-pipeline

Business idea validation & generation pipeline based on an Obsidian vault.

## Was macht das?

Liest Ideen, Chancen und Wissensbereiche aus einem Obsidian-Vault, lässt das
LLM die mühsame Arbeit machen (Chancen aus Ideen ableiten, Verknüpfungen
generieren, fehlende Felder ausfüllen), recherchiert Marktdaten extern,
scort die Ideen, schreibt alles zurück in den Vault.

Ziel: aus ~100 Geschäftsideen 5 Fokushypothesen herauskondensieren.

## Architektur

```
Du ──spricht──> Claude/Claude Code ──ruft──> CLI/Tools ──liest/schreibt──> Vault
                                                  │
                                                  ├──> Research-Cache (SQLite)
                                                  └──> Web/APIs (Statista, Destatis, ...)
```

## Setup

```bash
cd ~/idea-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ideapipe hello

# Vault path (default: ~/vaults/idea-validation)
export IDEAPIPE_VAULT=~/vaults/idea-validation
```

## Pipeline-Steps

| # | Step | Status | Liefert |
|---|------|--------|---------|
| 1 | Skeleton + Env | ✓ | Pipeline läuft |
| 2 | Schemas (db-based detection) | ✓ | Type-safe Daten, `schema check` |
| 3 | Vault-IO (read/write/list/doctor) | ✓ | atomare Vault-Operationen |
| 4 | Validation-Vault aufsetzen | TODO | echte Notizen importiert |
| 5 | Idea-Intake CLI (`ingest`) | TODO | aus Liste → Idee-Stubs |
| 6 | LLM-Enrichment: Chancen-Generierung | TODO | LLM erzeugt Chance-Stubs |
| 7 | LLM-Linking: Idee↔Chance↔Wissen | TODO | LLM verknüpft Notizen |
| 8 | T0 Scoring (vault-only) | TODO | erstes Leaderboard |
| 9 | Research-Layer (Cache + Web) | TODO | externe Daten in Felder |
| 10 | Tier-Funnel + Review-CLI | TODO | T0→T3 mit Cuts |
| 11 | Mehr Quellen + LLM-Derivat-Felder | TODO | bessere Score-Quality |
| 12 | Idea-Generator + NL-Interface | TODO | Variationen, NL-Steuerung |

## CLI-Commands

```bash
# Basics
ideapipe hello
ideapipe info

# Schema validation
ideapipe schema check FILE [-v]
ideapipe schema check-dir DIR [--show-unknown]

# Vault operations (Step 3)
ideapipe vault read FILE [-v]
ideapipe vault list [--vault PATH] [--type idee|chance|wissen] [-v]
ideapipe vault doctor [--vault PATH]
ideapipe vault write-test FILE
```

## Conventions

- Note type is detected from `database` field (not filename)
- `id` = filename without `.md` extension (never stored in YAML)
- Score values: integers 1-6 (1 = best, 6 = worst)
- Wikilinks are parsed on read, reconstructed on write
- Writes are atomic (temp + rename) — no half-written files
- Vault typos (`credebility`, `umprella_problem`) are tolerated via aliases
