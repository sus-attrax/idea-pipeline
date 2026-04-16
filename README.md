# idea-pipeline

Business idea validation & generation pipeline based on an Obsidian vault.

## Was macht das?

Liest Ideen, Chancen und Wissensbereiche aus einem Obsidian-Vault, recherchiert
fehlende Marktdaten extern, scort die Ideen, schreibt die Scores zurück in den
Vault. Generiert optional neue Ideen aus bestehenden Chancen × Wissen.

Ziel: aus ~100 Geschäftsideen 5 Fokushypothesen herauskondensieren.

## Architektur (Übersicht)

```
Du ──spricht──> Claude ──ruft──> CLI/Tools ──liest/schreibt──> Vault
                                      │
                                      ├──> Research-Cache (SQLite)
                                      └──> Web/APIs (Statista, Destatis, ...)
```

## Setup auf dem Server

```bash
# 1. In den Projektordner
cd ~/idea-pipeline

# 2. venv anlegen
python3 -m venv .venv
source .venv/bin/activate

# 3. Package + Dependencies installieren (editierbar, damit Code-Änderungen sofort greifen)
pip install --upgrade pip
pip install -e ".[dev]"

# 4. Smoke test
ideapipe hello
# → ✓ Pipeline-Skeleton lebt. Hallo, Meister.

# 5. Optional: Anthropic API Key setzen (brauchen wir ab Step 6)
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

## Pipeline-Steps (Build-Plan)

| # | Step | Status |
|---|------|--------|
| 1 | Skeleton + Env | ✓ |
| 2 | Pydantic-Schemas | TODO |
| 3 | Vault-IO (read/write) | TODO |
| 4 | Validation-Vault aufsetzen | TODO |
| 5 | Scoring v1 (T0, ohne Research) | TODO |
| 6 | Research-Layer (Cache + Web) | TODO |
| 7 | Funnel-Logik (Tier-Gates + Review) | TODO |
| 8 | Weitere Quellen (Statista, ...) | TODO |
| 9 | Derivat-Felder via LLM | TODO |
| 10 | Ideen-Generator | TODO |
| 11 | Orchestrierung (MCP-Server) | TODO |

## CLI-Commands (wachsen mit jedem Step)

```bash
ideapipe hello              # Smoke test
ideapipe info               # Pipeline-Status
# Ab Step 5+:
# ideapipe score --tier 0
# ideapipe research --tier 1 --limit 30
# ideapipe generate --n 10
# ...
```
