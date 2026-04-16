# idea-pipeline

Business idea validation & generation pipeline based on an Obsidian vault.

## Was macht das?

Liest Ideen, Chancen und Wissensbereiche aus einem Obsidian-Vault, lässt das
LLM die mühsame Arbeit machen (Chancen aus Ideen ableiten, Verknüpfungen
generieren, fehlende Felder ausfüllen), recherchiert Marktdaten extern,
scort die Ideen, schreibt alles zurück in den Vault.

Ziel: aus ~100 Geschäftsideen 5 Fokushypothesen herauskondensieren.

## Architektur (Übersicht)

```
Du ──spricht──> Claude/Claude Code ──ruft──> CLI/Tools ──liest/schreibt──> Vault
                                                  │
                                                  ├──> Research-Cache (SQLite)
                                                  └──> Web/APIs (Statista, Destatis, ...)
```

## Setup auf dem Server

```bash
cd ~/idea-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"

ideapipe hello

# Optional: Anthropic API Key (brauchen wir ab Step 6)
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# Git config (einmalig, wenn noch nicht gemacht)
git config --global user.name "Meister"
git config --global user.email "deine@email.de"
```

## Pipeline-Steps (Build-Plan)

| # | Step | Status | Liefert |
|---|------|--------|---------|
| 1 | Skeleton + Env | ✓ | Pipeline läuft |
| 2 | Pydantic-Schemas (db-based detection) | ✓ | Type-safe Daten, `schema check` |
| 3 | Vault-IO (read/write/list/doctor) | TODO | atomare Vault-Operationen |
| 4 | Validation-Vault aufsetzen | TODO | echte Notizen importiert |
| 5 | Idea-Intake CLI (`ingest`) | TODO | aus Liste → Idee-Stubs |
| 6 | LLM-Enrichment: Chancen-Generierung | TODO | LLM erzeugt Chance-Stubs |
| 7 | LLM-Linking: Idee↔Chance↔Wissen | TODO | LLM verknüpft Notizen |
| 8 | T0 Scoring (vault-only) | TODO | erstes Leaderboard |
| 9 | Research-Layer (Cache + Web) | TODO | externe Daten in Felder |
| 10 | Tier-Funnel + Review-CLI | TODO | T0→T3 mit Cuts |
| 11 | Mehr Quellen + LLM-Derivat-Felder | TODO | bessere Score-Quality |
| 12 | Idea-Generator + NL-Interface | TODO | Variationen, NL-Steuerung |

## CLI-Commands (wachsen mit jedem Step)

```bash
# Step 1
ideapipe hello                          # Smoke test
ideapipe info                           # Pipeline-Status

# Step 2
ideapipe schema check FILE              # Validate one note
ideapipe schema check FILE -v           # ... with parsed model dump
ideapipe schema check-dir DIR           # Batch-validate
ideapipe schema check-dir DIR --show-unknown  # ... including unrecognized files

# Coming next:
# ideapipe vault read NOTE              # Step 3
# ideapipe vault doctor                 # Step 3 — find data quality issues
# ideapipe ingest "name: desc"          # Step 5
# ideapipe enrich --type chance         # Step 6
# ideapipe link --type idee             # Step 7
# ideapipe score --tier 0               # Step 8
# ...
```

## Convention: Note-Type-Erkennung

Note-Typ wird aus dem `database`-Feld im YAML-Frontmatter erkannt
(NICHT aus dem Dateinamen — Dateinamen sind semantisch).

```yaml
database:
  - "[[geschaeftsideen🎲]]"   # → IdeeNote
  - "[[chancen🌋]]"            # → ChanceNote
  - "[[wissen🤯]]"             # → WissenNote
```

Notizen ohne erkennbare Database werden vom Pipeline-Detector geskippt.

## Datenqualitäts-Konventionen

- `id` wird IMMER aus dem Dateinamen abgeleitet, NIE im YAML stehen
- Score-Werte sind Integers 1-6 (1 = best, 6 = worst)
- Wikilinks `[[name]]` werden beim Lesen geparst, beim Schreiben rekonstruiert
- Tipos im Vault (`credebility`, `umprella_problem`) werden via Aliases toleriert
