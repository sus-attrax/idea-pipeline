# Step 9: Research Layer (Tavily + Firecrawl) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a two-tier research layer that enriches idea notes with market scores via Tavily (T1) and Firecrawl (T2), cached in SQLite to avoid redundant API calls.

**Architecture:** `research/cache.py` handles a SHA-256-keyed SQLite store with 7-day TTL. `research/web.py` holds `TavilyResearcher` (T1: search → Haiku extracts 1-6 scores) and `FirecrawlResearcher` (T2: find + scrape Destatis/Eurostat → Sonnet extracts scores). The `ideapipe research` CLI command selects top-N ideas by score, runs the appropriate tier, writes updated fields + `research_fidelity` to vault notes, then re-scores.

**Tech Stack:** `tavily-python`, `firecrawl-py`, `anthropic` (Haiku/Sonnet), SQLite (`sqlite3` stdlib), existing `vault_io`, `scoring.score_vault`.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `pyproject.toml` | Add `tavily-python`, `firecrawl-py` |
| Modify | `.env` | Add `TAVILY_API_KEY`, `FIRECRAWL_API_KEY` placeholders |
| Create | `config/prompts/v1/research_t1_extract.txt` | Haiku prompt: Tavily snippets → 1-6 scores |
| Create | `config/prompts/v1/research_t2_extract.txt` | Sonnet prompt: Firecrawl markdown → 1-6 scores |
| Modify | `src/idea_pipeline/research/cache.py` | SQLite cache (get/set/TTL) |
| Modify | `src/idea_pipeline/research/web.py` | `TavilyResearcher`, `FirecrawlResearcher` |
| Modify | `src/idea_pipeline/cli.py` | Add `research` command |

---

### Task 1: Add dependencies + env placeholders

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env`

- [ ] **Step 1: Add dependencies to pyproject.toml**

In the `dependencies` list, add after `"anthropic>=0.39.0"`:
```toml
"tavily-python>=0.3.0",
"firecrawl-py>=1.0.0",
```

- [ ] **Step 2: Install**

```bash
cd /home/homo/idea-pipeline && source .venv/bin/activate && pip install -e . -q
```

Expected: no errors, `tavily` and `firecrawl` importable.

- [ ] **Step 3: Add API key placeholders to .env**

Append to `/home/homo/idea-pipeline/.env`:
```
TAVILY_API_KEY=tvly-...
FIRECRAWL_API_KEY=fc-...
```

- [ ] **Step 4: Verify imports**

```bash
source .venv/bin/activate && python3 -c "from tavily import TavilyClient; from firecrawl import FirecrawlApp; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "step 9: add tavily-python and firecrawl-py dependencies"
```

---

### Task 2: Implement `research/cache.py`

**Files:**
- Modify: `src/idea_pipeline/research/cache.py`

- [ ] **Step 1: Write cache.py**

```python
"""SQLite cache for research results.

Key: sha256(query + "|" + source_name)
Value: raw response JSON + timestamp
TTL: 7 days
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

_TTL_SECONDS = 7 * 24 * 3600

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "cache" / "research.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS research_cache (
    key      TEXT PRIMARY KEY,
    source   TEXT NOT NULL,
    query    TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at REAL NOT NULL
)
"""


def _cache_key(query: str, source: str) -> str:
    return hashlib.sha256(f"{query}|{source}".encode()).hexdigest()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(_CREATE_SQL)
    conn.commit()
    return conn


def cache_get(query: str, source: str) -> Optional[Any]:
    """Return cached response or None if missing/expired."""
    key = _cache_key(query, source)
    with _connect() as conn:
        row = conn.execute(
            "SELECT response, created_at FROM research_cache WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    response_json, created_at = row
    if time.time() - created_at > _TTL_SECONDS:
        return None
    return json.loads(response_json)


def cache_set(query: str, source: str, response: Any) -> None:
    """Store response in cache."""
    key = _cache_key(query, source)
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO research_cache (key, source, query, response, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (key, source, query, json.dumps(response, ensure_ascii=False), time.time()),
        )
        conn.commit()


def cache_stats() -> dict:
    """Return {total_entries, expired_entries} for diagnostics."""
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM research_cache").fetchone()[0]
        expired = conn.execute(
            "SELECT COUNT(*) FROM research_cache WHERE created_at < ?",
            (time.time() - _TTL_SECONDS,),
        ).fetchone()[0]
    return {"total": total, "expired": expired}
```

- [ ] **Step 2: Smoke-test cache**

```bash
cd /home/homo/idea-pipeline && source .venv/bin/activate && python3 -c "
from idea_pipeline.research.cache import cache_get, cache_set, cache_stats
cache_set('test query', 'test_source', {'score': 3})
result = cache_get('test query', 'test_source')
assert result == {'score': 3}, result
print('cache ok:', cache_stats())
"
```

Expected: `cache ok: {'total': 1, 'expired': 0}`

- [ ] **Step 3: Commit**

```bash
git add src/idea_pipeline/research/cache.py cache/
git commit -m "step 9: implement SQLite research cache"
```

---

### Task 3: Write LLM extraction prompts

**Files:**
- Create: `config/prompts/v1/research_t1_extract.txt`
- Create: `config/prompts/v1/research_t2_extract.txt`

- [ ] **Step 1: Write T1 extraction prompt**

```
You extract market intelligence scores from web search results for a business idea.

Input: JSON object with:
  - "idea_description": string
  - "field": one of "market_size" | "market_potential" | "prevalence" | "market_awareness"
  - "search_results": list of {"title": "...", "content": "...", "url": "..."}

Output: JSON object with:
  - "score": integer 1-6
  - "reasoning": one sentence

Score meaning (1=best, 6=worst):
  market_size:       1=very large global market (>$10B), 6=very niche (<$10M)
  market_potential:  1=fast growing (>20%/yr), 6=shrinking or stagnant
  prevalence:        1=very widespread problem (millions affected), 6=rare edge case
  market_awareness:  1=well-known established problem, 6=unknown, needs heavy education

If results are insufficient to judge, return score 4 (conservative default).
Output ONLY valid JSON — no explanation, no markdown fences.
```

- [ ] **Step 2: Write T2 extraction prompt**

```
You extract market intelligence scores from structured data sources (Destatis, Eurostat, Statista).

Input: JSON object with:
  - "idea_description": string
  - "scraped_markdown": string (page content from official statistical source)
  - "source_url": string

Output: JSON object with:
  - "market_size": integer 1-6 or null (if not determinable)
  - "market_potential": integer 1-6 or null
  - "prevalence": integer 1-6 or null
  - "market_awareness": integer 1-6 or null
  - "reasoning": one sentence per non-null field as a dict

Score meaning (1=best, 6=worst):
  market_size:       1=very large global market (>$10B), 6=very niche (<$10M)
  market_potential:  1=fast growing (>20%/yr), 6=shrinking or stagnant
  prevalence:        1=very widespread problem (millions affected), 6=rare edge case
  market_awareness:  1=well-known established problem, 6=unknown, needs education

Only score fields where the scraped content provides clear evidence.
Output ONLY valid JSON — no explanation, no markdown fences.
```

- [ ] **Step 3: Commit**

```bash
git add config/prompts/v1/research_t1_extract.txt config/prompts/v1/research_t2_extract.txt
git commit -m "step 9: add research extraction prompts"
```

---

### Task 4: Implement `research/web.py`

**Files:**
- Modify: `src/idea_pipeline/research/web.py`

- [ ] **Step 1: Write web.py**

```python
"""Web research adapters: Tavily (T1) and Firecrawl (T2).

TavilyResearcher  — searches for 4 fields per idea, Haiku extracts 1-6 scores.
FirecrawlResearcher — finds + scrapes Destatis/Eurostat, Sonnet extracts scores.
Both use the research cache to avoid redundant API calls.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from idea_pipeline.research.cache import cache_get, cache_set

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PROMPTS_DIR = _PROJECT_ROOT / "config" / "prompts" / "v1"

_HAIKU = "claude-haiku-4-5-20251001"
_SONNET = "claude-sonnet-4-6"

_RESEARCH_FIELDS = ["market_size", "market_potential", "prevalence", "market_awareness"]

_QUERY_TEMPLATES = {
    "market_size": "{description} market size global annual revenue",
    "market_potential": "{description} market growth rate CAGR forecast",
    "prevalence": "{description} problem frequency how many people affected statistics",
    "market_awareness": "{description} consumer awareness adoption rate survey",
}

_STAT_DOMAINS = ["destatis.de", "eurostat.ec.europa.eu", "statista.com"]


def _get_anthropic():
    from anthropic import Anthropic
    return Anthropic()


def _read_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _parse_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# T1: Tavily
# ---------------------------------------------------------------------------

class TavilyResearcher:
    SOURCE = "tavily_v1"

    def __init__(self):
        from tavily import TavilyClient
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key or api_key.startswith("tvly-..."):
            raise RuntimeError("TAVILY_API_KEY not set in .env")
        self._client = TavilyClient(api_key=api_key)
        self._llm = _get_anthropic()
        self._prompt = _read_prompt("research_t1_extract.txt")

    def research_idea(self, idea_id: str, description: str) -> dict[str, int]:
        """Return {field: score_1_to_6} for all 4 research fields."""
        scores: dict[str, int] = {}
        for field in _RESEARCH_FIELDS:
            query = _QUERY_TEMPLATES[field].format(description=description[:200])
            scores[field] = self._score_field(field, description, query)
        return scores

    def _score_field(self, field: str, description: str, query: str) -> int:
        cached = cache_get(query, self.SOURCE)
        if cached is not None:
            return cached.get("score", 4)

        try:
            results = self._client.search(
                query=query,
                search_depth="basic",
                max_results=5,
            )
            snippets = [
                {"title": r.get("title", ""), "content": r.get("content", "")[:500], "url": r.get("url", "")}
                for r in results.get("results", [])
            ]
        except Exception:
            return 4  # conservative default on API error

        payload = {
            "idea_description": description[:300],
            "field": field,
            "search_results": snippets,
        }

        try:
            resp = self._llm.messages.create(
                model=_HAIKU,
                max_tokens=256,
                system=self._prompt,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            extracted = _parse_json(resp.content[0].text)
            score = max(1, min(6, int(extracted["score"])))
        except Exception:
            score = 4

        cache_set(query, self.SOURCE, {"score": score, "snippets": snippets})
        return score


# ---------------------------------------------------------------------------
# T2: Firecrawl
# ---------------------------------------------------------------------------

class FirecrawlResearcher:
    SOURCE = "firecrawl_v1"
    TAVILY_SOURCE = "tavily_t2_url_discovery"

    def __init__(self):
        from firecrawl import FirecrawlApp
        from tavily import TavilyClient

        fc_key = os.environ.get("FIRECRAWL_API_KEY", "")
        tv_key = os.environ.get("TAVILY_API_KEY", "")
        if not fc_key or fc_key.startswith("fc-..."):
            raise RuntimeError("FIRECRAWL_API_KEY not set in .env")
        if not tv_key or tv_key.startswith("tvly-..."):
            raise RuntimeError("TAVILY_API_KEY not set in .env")

        self._fc = FirecrawlApp(api_key=fc_key)
        self._tv = TavilyClient(api_key=tv_key)
        self._llm = _get_anthropic()
        self._prompt = _read_prompt("research_t2_extract.txt")

    def research_idea(self, idea_id: str, description: str) -> dict[str, Optional[int]]:
        """Return {field: score_or_None} — only fields with evidence are scored."""
        url = self._find_stat_url(description)
        if url is None:
            return {}
        markdown = self._scrape(url)
        if not markdown:
            return {}
        return self._extract_scores(description, markdown, url)

    def _find_stat_url(self, description: str) -> Optional[str]:
        query = f"{description[:200]} statistics site:destatis.de OR site:eurostat.ec.europa.eu"
        cached = cache_get(query, self.TAVILY_SOURCE)
        if cached:
            return cached.get("url")

        try:
            results = self._tv.search(query=query, search_depth="basic", max_results=3)
            urls = [r.get("url", "") for r in results.get("results", []) if r.get("url")]
            stat_urls = [u for u in urls if any(d in u for d in _STAT_DOMAINS)]
            url = stat_urls[0] if stat_urls else (urls[0] if urls else None)
        except Exception:
            url = None

        cache_set(query, self.TAVILY_SOURCE, {"url": url})
        return url

    def _scrape(self, url: str) -> Optional[str]:
        cached = cache_get(url, self.SOURCE)
        if cached:
            return cached.get("markdown")

        try:
            result = self._fc.scrape_url(url, formats=["markdown"])
            markdown = result.get("markdown", "") if isinstance(result, dict) else ""
            markdown = markdown[:8000]  # cap to avoid huge LLM context
        except Exception:
            markdown = None

        cache_set(url, self.SOURCE, {"markdown": markdown})
        return markdown

    def _extract_scores(self, description: str, markdown: str, url: str) -> dict[str, Optional[int]]:
        payload = {
            "idea_description": description[:300],
            "scraped_markdown": markdown,
            "source_url": url,
        }
        try:
            resp = self._llm.messages.create(
                model=_SONNET,
                max_tokens=512,
                system=self._prompt,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            data = _parse_json(resp.content[0].text)
        except Exception:
            return {}

        result = {}
        for field in _RESEARCH_FIELDS:
            val = data.get(field)
            if val is not None:
                result[field] = max(1, min(6, int(val)))
        return result
```

- [ ] **Step 2: Smoke-test import**

```bash
cd /home/homo/idea-pipeline && source .venv/bin/activate && python3 -c "from idea_pipeline.research.web import TavilyResearcher, FirecrawlResearcher; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/idea_pipeline/research/web.py
git commit -m "step 9: implement TavilyResearcher and FirecrawlResearcher"
```

---

### Task 5: Add `research` command to CLI

**Files:**
- Modify: `src/idea_pipeline/cli.py`

- [ ] **Step 1: Add import** (with other imports at top of cli.py)

Add after `from idea_pipeline.scoring import ScoreResult, score_vault`:
```python
from idea_pipeline.research.cache import cache_stats
```

- [ ] **Step 2: Add command** (after `score_cmd`, before `if __name__ == "__main__"`)

```python
@app.command("research")
def research_cmd(
    vault: Optional[Path] = _vault_option,
    tier: int = typer.Option(1, "--tier", "-t", help="Research tier: 1=Tavily, 2=Firecrawl+Tavily"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of top ideas to research"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing or calling APIs"),
) -> None:
    """Enrich top ideas with external market research (T1: Tavily, T2: Firecrawl).

    Reads current scores, picks top --limit ideas, runs research, writes
    market_size/market_potential/prevalence/market_awareness + research_fidelity
    back to vault, then re-scores.
    """
    from idea_pipeline.research.web import FirecrawlResearcher, TavilyResearcher

    vault_path = get_vault_path(vault)
    if not vault_path.is_dir():
        console.print(f"[red]✗ Vault not found:[/red] {vault_path}")
        raise typer.Exit(1)

    if tier not in (1, 2):
        console.print(f"[red]✗ --tier must be 1 or 2 (got {tier})[/red]")
        raise typer.Exit(1)

    if dry_run:
        console.print("[bold yellow]Dry run[/bold yellow] — no APIs called, no files written.\n")

    # Pick top-N ideas by current score
    score_result = score_vault(vault_path, dry_run=True)
    top_ideas = score_result.scored[:limit]

    if dry_run:
        console.print(f"Would research {len(top_ideas)} ideas at tier {tier}:")
        for idea_id, score in top_ideas:
            console.print(f"  [cyan]{idea_id}[/cyan] (score={score:.3f})")
        stats = cache_stats()
        console.print(f"\n[dim]Cache: {stats['total']} entries ({stats['expired']} expired)[/dim]")
        return

    # Load researcher
    try:
        researcher = TavilyResearcher() if tier == 1 else FirecrawlResearcher()
    except RuntimeError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    fidelity = f"tier{tier}"
    from idea_pipeline.schemas import IdeeNote
    from idea_pipeline.vault_io import list_notes, write_note as _write_note

    ideen_by_id = {n.model.id: n for n in list_notes(vault_path, IdeeNote).notes}
    written = 0
    errors = 0

    for idea_id, score in top_ideas:
        vnote = ideen_by_id.get(idea_id)
        if vnote is None:
            continue
        idea = vnote.model
        console.print(f"  Researching [cyan]{idea_id}[/cyan] ...", end=" ")

        try:
            scores = researcher.research_idea(idea_id, idea.description or idea_id)
        except Exception as e:
            console.print(f"[red]error: {e}[/red]")
            errors += 1
            continue

        if not scores:
            console.print("[dim]no data[/dim]")
            continue

        for field, val in scores.items():
            setattr(idea, field, val)
        idea.research_fidelity = fidelity
        _write_note(vnote)
        written += 1
        console.print(f"[green]✓[/green] {scores}")

    # Re-score after research
    console.print(f"\nRe-scoring vault ...")
    score_vault(vault_path)

    stats = cache_stats()
    console.print(
        f"\nDone. {written} enriched · {errors} errors · "
        f"cache: {stats['total']} entries"
    )
```

- [ ] **Step 3: Test dry-run**

```bash
source .venv/bin/activate && ideapipe research --tier 1 --limit 5 --dry-run 2>&1
```

Expected: lists top 5 ideas, shows cache stats, no API calls.

- [ ] **Step 4: Commit**

```bash
git add src/idea_pipeline/cli.py docs/
git commit -m "step 9: add ideapipe research command (T1+T2)"
git push
```
