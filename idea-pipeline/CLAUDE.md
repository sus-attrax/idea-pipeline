# CLAUDE.md — Project instructions for Claude Code

## Project
Business idea validation pipeline. Obsidian vault → LLM enrichment → scoring → top 5 ideas.

## Commands
```bash
source .venv/bin/activate        # Always activate venv first
pip install -e .                 # After changing pyproject.toml
ideapipe --help                  # See all commands
ideapipe vault doctor            # Check vault health
ideapipe vault list --type idee  # List ideas
```

## Architecture
- CLI built with Typer. Each pipeline step = one CLI command.
- Vault at ~/vaults/idea-validation/ (env: IDEAPIPE_VAULT)
- Schemas in schemas.py (Pydantic v2). Three types: IdeeNote, ChanceNote, WissenNote.
- Type detection via `database` YAML field, NOT filename.
- Atomic writes (temp + rename) in vault_io.py. Never use open(path, 'w') directly.
- API key in .env (python-dotenv).

## Rules
- Every command must support --dry-run and --vault flags.
- Every command must be idempotent (safe to run multiple times).
- LLM prompts go in config/prompts/v1/ as separate files.
- Batch LLM calls (5-10 items per call) — never 1 call per note.
- Cache all research in cache/research.db (SQLite).
- Git commit after every working step.
- Vault typos preserved: `umprella_problem`, `credebility` (Pydantic aliases handle this).
- Score values: int 1-6, 1=best. Invert to (7-x) for calculation.
- Wikilinks: YAML has [[name]], Python has bare string "name". Reconstructed on write.

## Current state
Steps 1-5 complete. See HANDOFF.md for full details on what's built and what's next.
Next: Step 6 (LLM Enrichment — chance generation + descriptions).

## Git
After changes: git add . && git commit -m "step N: description" && git push
