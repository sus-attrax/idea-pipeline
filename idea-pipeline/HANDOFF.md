# HANDOFF.md — Idea-Pipeline Build Continuation
# Dieses Dokument ist die vollständige Übergabe an Claude Code.
# Es enthält alles was nötig ist um die Pipeline ab Step 6 weiterzubauen.

---

## KONTEXT: Was ist das Projekt?

Ein solo Gründer ("Meister") in Freiburg hat ~100 Geschäftsideen und will sie auf 5 Fokushypothesen reduzieren. Dafür baut er eine Pipeline die auf einem Obsidian-Vault basiert.

**Der Vault enthält drei Entitätstypen:**
- **Idee** (geschaeftsideen🎲) — Lösungskandidat, verlinkt auf Chancen + Wissen
- **Chance** (chancen🌋) — Problemfeld/Opportunity, hierarchisch via `umprella_problem`
- **Wissen** (wissen🤯) — persönliche Kompetenzbereiche des Users

**Die Pipeline soll:**
1. Aus einfachen Ideen-Descriptions den ganzen Vault anreichern (Chancen generieren, Links setzen, Scores schätzen)
2. Über Web-Research Marktdaten einholen
3. Alles in einem gewichteten Score zusammenführen
4. Am Ende 5 Top-Ideen als Fokushypothesen liefern

**Philosophie des Users:**
- So wenig manuellen Input wie möglich — nur Ideen-Descriptions + Wissen-Scores kommen vom User
- Natural Language Interface (Claude Code) statt manuelle Code-Interaktion
- Rechen- und Token-effizient (Caching, Tier-Funnel, Batch API)
- Future-proof (modularer Code, Git-versioniert, skalierbar)

---

## INFRASTRUKTUR

- **Server:** Hetzner (Debian), dauerhaft laufend
- **Python:** 3.11.2, venv unter `~/idea-pipeline/.venv`
- **Code-Repo:** `~/idea-pipeline/` → GitHub `sus-attrax/idea-pipeline` (main branch)
- **Vault:** `~/vaults/idea-validation/` (eigenes lokales Git, flache Dateistruktur)
- **Vault-Pfad als Default:** `export IDEAPIPE_VAULT=~/vaults/idea-validation` (in .bashrc)
- **API-Key:** muss in `~/idea-pipeline/.env` als `ANTHROPIC_API_KEY=sk-ant-...`

---

## AKTUELLER STAND: Was ist gebaut (Steps 1-5)

### Dateistruktur
```
~/idea-pipeline/
├── pyproject.toml                          # Package-Definition, CLI entry point
├── .env                                    # ANTHROPIC_API_KEY (nicht in Git)
├── .gitignore
├── README.md
├── config/
│   ├── weights.yaml                        # Scoring-Gewichte (additiv, gewichtet)
│   ├── sources.yaml                        # Research-Quellen pro Tier
│   └── prompts/v1/                         # Versionierte LLM-Prompts (leer, ab Step 6)
├── src/idea_pipeline/
│   ├── __init__.py                         # v0.1.0
│   ├── cli.py                              # Typer CLI — alle Commands
│   ├── schemas.py                          # Pydantic v2 Schemas: IdeeNote, ChanceNote, WissenNote
│   ├── settings.py                         # Vault-Pfad Resolution
│   ├── vault_io.py                         # Read/Write (atomar)/List/Doctor
│   ├── ingest.py                           # Note-Erstellung aus name:description Paaren
│   ├── scoring.py                          # LEER — Step 8
│   ├── generator.py                        # LEER — Step 12
│   └── research/                           # LEER — Steps 9-11
│       ├── __init__.py
│       ├── web.py
│       ├── cache.py
│       └── sources/__init__.py
├── vault-templates/_templates/             # Kanonische Obsidian-Templates
│   ├── idee.md
│   ├── chance.md
│   └── wissen.md
├── cache/                                  # SQLite Research-Cache (ab Step 9)
└── runs/                                   # Run-Logs (ab Step 10)
```

### Funktionierende CLI-Commands
```bash
ideapipe hello                                    # Smoke test
ideapipe info                                     # Status + Vault-Pfad + Note-Count
ideapipe schema check FILE [-v]                   # Eine Notiz validieren
ideapipe schema check-dir DIR [--show-unknown]    # Batch-Validierung
ideapipe vault read FILE [-v]                     # Notiz lesen + anzeigen
ideapipe vault list [--type idee|chance|wissen] [-v] [--vault PATH]
ideapipe vault doctor [--vault PATH]              # Datenqualitäts-Checks
ideapipe vault write-test FILE                    # Round-Trip-Test
ideapipe ingest "name: desc" [--type X] [--dry-run] [--file F] [--stdin]
```

### Vault-Inhalt (Stand: nach Step 5)
- 5 Ideen (balkongarten, doorframe_fittness, High_performance_mulch, continuous_bioreaktor_assey_loop, microbial_omega3)
- 20 Chancen (die meisten ohne Description)
- 7 Wissensbereiche (alle mit Descriptions und Scores)
- Bekannte Warnings: 4 broken links, 19 fehlende Descriptions

---

## SCHEMA-DETAILS (wichtig für Code-Generierung)

### IdeeNote
```python
# Felder die der USER setzt:
description: str          # 1-3 Sätze, Input für LLM
# Felder die das LLM/die Pipeline setzt:
first_adopters: list[str] # Kundensegment Early Adopters
mass_customers: list[str] # Kundensegment Massenmarkt
market_size: int 1-6      # 1=groß (Research)
market_potential: int 1-6  # 1=groß (Research)
impact: int 1-6           # 1=groß (LLM-Derivat)
difficulty: int 1-6       # 1=leicht (LLM-Derivat)
time_investment: int 1-6  # 1=wenig (LLM-Derivat)
innovativeness: int 1-6   # 1=innovativ (LLM-Derivat)
chancen: list[str]        # Wikilinks zu Chance-Notizen (LLM)
wissen: list[str]         # Wikilinks zu Wissen-Notizen (LLM)
# Pipeline-managed:
score: float              # Finaler Score (Step 8)
score_breakdown: dict     # Aufschlüsselung
score_version: str        # "v1"
scored_at: str            # ISO Datum
research_fidelity: str    # tier0|tier1|tier2|tier3
```

### ChanceNote
```python
description: str              # Was ist das Problem (LLM generiert)
granularitaet: int 1-6        # 1=spezifisch, 6=breit (LLM)
urgency: int 1-6              # 1=dringend (LLM)
prevalence: int 1-6           # 1=häufig (Research)
impact: int 1-6               # 1=stark (LLM)
personal_experience: int 1-6  # 1=nah (USER — intrinsisch)
market_awareness: int 1-6     # 1=hoch (Research)
umbrella_problem: list[str]   # YAML-Key ist "umprella_problem" (Tippfehler im Vault, Alias in Pydantic)
```

### WissenNote
```python
description: str          # Was umfasst das Wissen (USER)
enjoyment: int 1-6        # 1=hoch (USER)
confidence: int 1-6       # 1=hoch (USER)
credibility: int 1-6      # YAML-Key ist "credebility" (Tippfehler, Alias)
contacts: int 1-6         # 1=viele (USER)
```

### Wichtige Konventionen
- ALLE Scores: 1=best, 6=worst (wie Schulnoten)
- ID = Dateiname ohne .md, NIEMALS im YAML gespeichert
- Typ-Erkennung über `database` Feld (nicht Dateiname)
- Wikilinks: im YAML als `[[name]]`, in Python als bare string `name`
- Extra-Felder toleriert via `extra="allow"` (z.B. `monat`, `prio`)
- Atomare Writes: temp file + rename
- `customer` Feld ist ENTFERNT, ersetzt durch `first_adopters` + `mass_customers`

---

## SCORING-DESIGN

Additiv, gewichtet. Konfiguration in `config/weights.yaml`.

```
idee_total =
    0.40 * avg(verlinkte chance_scores)
  + 0.25 * avg(verlinkte wissen_scores)
  + 0.35 * avg(intrinsische idee_scores)

Jeder Score wird invertiert: (7 - wert), damit größer = besser.
```

Gewichte innerhalb jeder Kategorie: siehe `config/weights.yaml`.
Gewichte sind vom User tunebar ohne Code-Änderung.

---

## RESEARCH-FUNNEL (Tier-System)

| Tier | Sample | Quelle | Kosten | Zweck |
|------|--------|--------|--------|-------|
| T0 | alle ~100 | nur Vault | 0€ | Baseline-Score |
| T1 | Top 30 | Web-Search, ~3 Queries/Idee | niedrig | Grobe Marktgrößen |
| T2 | Top 10 | + Statista/Destatis/Eurostat | mittel | Belastbare Zahlen |
| T3 | Top 5 | + Branchenberichte, Wettbewerber | hoch | Entscheidungsreife |

Nach jedem Tier: Re-Scoring, Review durch User, Cut. Alle Research-Ergebnisse
zurück in die Vault-Notizen mit `research_fidelity` Feld.

---

## NOCH ZU BAUEN: Steps 6-12

### Step 6: LLM-Enrichment — Chancen-Generierung
**Command:** `ideapipe enrich [--vault PATH] [--dry-run]`

Was passiert:
1. Alle Ideen im Vault lesen die Chancen-Links haben die auf nicht-existierende Notizen zeigen → neue ChanceNote Stubs erstellen
2. Alle Ideen ohne Chancen-Links: LLM analysiert die Idee-Description und schlägt 3-6 passende Chancen vor. Wenn die Chance schon als Notiz existiert → verlinken. Wenn nicht → neue ChanceNote erstellen.
3. Alle Chancen ohne Description: LLM schreibt 1-2 Sätze Description basierend auf dem Chancen-Namen und dem Kontext der verlinkten Ideen.
4. Chancen umbrella_problem Links: LLM schlägt Hierarchie vor (welche Chancen sind Unterproblem von welchen).

Effizienz-Hebel:
- Batch: mehrere Ideen pro LLM-Call (10er Batches mit JSON-Output)
- Chance-Generierung wird geshared: 3 Ideen auf gleicher Chance → nur 1× generieren
- Prompts in `config/prompts/v1/` versioniert ablegen
- Ergebnisse in Vault schreiben + `research_fidelity: tier0` setzen

### Step 7: LLM-Linking — Idee↔Wissen
**Command:** `ideapipe link [--vault PATH] [--dry-run]`

Was passiert:
1. Alle Wissen-Notizen lesen (descriptions + IDs)
2. Für jede Idee: LLM bekommt die Idee-Description + die Liste aller Wissen-Bereiche → schlägt passende Wissen-Links vor
3. Links in die Idee-Notizen schreiben

### Step 8: T0 Scoring (vault-only, KEIN LLM)
**Command:** `ideapipe score [--tier 0] [--vault PATH] [--top N]`

Rein deterministisch: liest alle Ideen + verlinkte Chancen + verlinkte Wissen, berechnet gewichteten Score gemäß `weights.yaml`, schreibt `score`, `score_breakdown`, `score_version`, `scored_at` in jede Idee-Notiz. Gibt ein Leaderboard aus.

### Step 9: Research-Layer
**Command:** `ideapipe research [--tier 1|2|3] [--limit N] [--vault PATH]`

- SQLite-Cache in `cache/research.db` (Key: hash(query+source), Value: response+timestamp)
- T1: Claude API mit web_search Tool für `market_size`, `market_potential`, `prevalence`, `market_awareness`
- T2+: Statista/Destatis/Eurostat (modular in `research/sources/`)
- Ergebnisse in die Notizen schreiben + `research_fidelity` updaten

### Step 10: Tier-Funnel + Review-CLI
**Command:** `ideapipe funnel [--vault PATH]`

Zeigt aktuelles Tier, Score-Leaderboard, ermöglicht Cut (Top N → nächster Tier).
User-Review nach jedem Tier (nicht automatisch).

### Step 11: LLM-Derivat-Felder
**Command:** `ideapipe derive [--vault PATH]`

LLM schätzt `innovativeness`, `difficulty`, `time_investment`, `impact` basierend auf Idee-Description + Chancen + Wissen-Kontext. Nutzt Prompt Caching (Vault-Kontext als Cache-Prefix).

### Step 12: Idea-Generator + NL-Interface
**Command:** `ideapipe generate [--n N] [--vault PATH]`

Generiert neue Ideen aus Kombination von Chancen × Wissen × bestehenden Top-Ideen. Schreibt sie als neue IdeeNotes in den Vault. Dann zurück in den Scoring-Loop.

---

## TOKEN-EFFIZIENZ-REGELN

1. **Batch-Calls:** Nie 1 LLM-Call pro Notiz, sondern 5-10 Notizen pro Call mit strukturiertem JSON-Output
2. **SQLite-Cache:** Jede Research-Query gecached, Re-Runs kosten 0
3. **Chance-Sharing:** Research pro Chance, nicht pro Idee (3 Ideen auf gleicher Chance = 1× recherchiert)
4. **Prompt Caching:** Anthropic API Feature — Vault-Kontext als cached prefix, nur die Frage variiert
5. **Modell-Split:** Haiku für Klassifikation/Extraktion, Sonnet für Analyse/Scoring
6. **Batch API:** 50% günstiger, für nicht-interaktive Calls (Research, Enrichment)
7. **Tier-Funnel:** Nur Top-Ideen bekommen teure Deep-Research

---

## CODE-KONVENTIONEN

- **Typer CLI:** Jeder Pipeline-Step = ein CLI-Command mit `--dry-run`, `--vault`, `--verbose`
- **Idempotenz:** Jeder Command kann 5x hintereinander laufen ohne Seiteneffekte
- **Atomare Writes:** Immer temp file + rename (nie `open(path, 'w')` direkt)
- **Settings:** Vault-Pfad aus `IDEAPIPE_VAULT` env var oder `--vault` CLI arg
- **Prompts versioniert:** Alle LLM-Prompts in `config/prompts/v1/` als eigene Dateien
- **Git:** Nach jedem Step committen + pushen
- **Fehlerbehandlung:** ValidationError → klare Meldung, kein stiller Fail
- **Output:** Rich-formatiert (Tabellen, Farben), aber nicht überladen
- **API-Key:** Aus `.env` laden (python-dotenv), nie hardcoden

---

## VAULT-TYPOS DIE BEIBEHALTEN WERDEN

Diese Tippfehler existieren in den echten Vault-Notizen und werden via Pydantic-Aliases toleriert:
- `umprella_problem` (statt `umbrella_problem`) — in ChanceNote
- `credebility` (statt `credibility`) — in WissenNote

Im Python-Code werden die korrekten Namen verwendet. Beim YAML-Write werden die Tippfehler rekonstruiert (by_alias=True).

---

## .env SETUP

```bash
# ~/idea-pipeline/.env
ANTHROPIC_API_KEY=sk-ant-...
```

Laden mit python-dotenv (muss ggf. als Dependency ergänzt werden in pyproject.toml):
```python
from dotenv import load_dotenv
load_dotenv()
```

---

## GIT-WORKFLOW

```bash
cd ~/idea-pipeline
git add .
git commit -m "step N: kurze beschreibung"
git push
```

Remote: `git@github.com-idea-pipeline:sus-attrax/idea-pipeline.git`
SSH-Config nutzt custom Host-Alias `github.com-idea-pipeline`.

---

## ANLEITUNG FÜR CLAUDE CODE: NÄCHSTER SCHRITT

1. Lies dieses Dokument komplett.
2. Lies den bestehenden Code: `schemas.py`, `vault_io.py`, `ingest.py`, `cli.py`, `settings.py`.
3. Lies `config/weights.yaml` und `config/sources.yaml`.
4. Füge `python-dotenv` als Dependency zu `pyproject.toml` hinzu. `pip install -e .`
5. Erstelle `config/prompts/v1/enrich_chances.txt` — der Prompt für Chancen-Generierung.
6. Implementiere Step 6 (LLM-Enrichment) in `src/idea_pipeline/enrich.py`.
7. Füge `ideapipe enrich` Command zu `cli.py` hinzu.
8. Teste mit `ideapipe enrich --dry-run`.
9. Wenn dry-run sauber: `ideapipe enrich` auf dem echten Vault.
10. `ideapipe vault doctor` danach um Verbesserung zu sehen.
11. `git add . && git commit -m "step 6: LLM enrichment" && git push`
12. Weiter mit Step 7, 8, 9... in gleicher Reihenfolge.

Frage den User bei Design-Entscheidungen die nicht in diesem Dokument stehen.
