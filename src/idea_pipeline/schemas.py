"""Pydantic schemas for vault entities: Idee, Chance, Wissen.

Why Pydantic v2:
- Validate frontmatter on read, fail-fast on bad data
- Type-safe access throughout the pipeline (IDE autocomplete, mypy)
- Built-in serialization back to YAML for write operations (Step 3)
- Field aliases handle existing typos without forcing vault migration

Design decisions:
- BaseNote holds the fields common to all three types
- Score values: int 1-6 (smaller = better, per user convention)
- Wikilinks like '[[01_07_chance]]' are parsed to bare IDs ('01_07_chance')
- 'id' is derived from the filename, not stored in frontmatter
- Note type is detected from the `database` field (NOT the filename),
  because real vault uses semantic filenames without type suffixes
- Existing typos in user vault (credebility, umprella_problem) preserved via
  aliases — clean Python names, original YAML preserved on write
- Unknown fields are tolerated (extra="allow") so vault can grow without
  schema migration overhead — but core typed fields are validated strictly
"""

from __future__ import annotations

import re
from typing import Annotated, Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# A score value: integer 1-6 inclusive. 1 = best, 6 = worst (user convention).
ScoreValue = Annotated[int, Field(ge=1, le=6)]


# Wikilink pattern: matches [[target]] or [[target|alias]]
_WIKILINK_RE = re.compile(r"^\[\[([^\]|]+)(?:\|[^\]]+)?\]\]$")


def _parse_wikilink(value: Any) -> str | None:
    """Strip [[ ]] from a single wikilink string, return the bare target ID.

    Returns None for empty/None input. Returns the value unchanged if it
    isn't a wikilink (some fields might hold plain strings).
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return None
    match = _WIKILINK_RE.match(value.strip())
    if match:
        return match.group(1).strip()
    return value.strip() or None


def _parse_wikilink_list(values: Any) -> list[str]:
    """Parse a list of wikilink strings into a list of bare IDs.

    Accepts:
      - None or empty → []
      - Single string → treated as a one-item list (defensive: handles
        cases where a user accidentally writes `chancen: "[[foo]]"` instead
        of `chancen:\n  - "[[foo]]"`)
      - List of strings → each parsed
    Filters out empty entries. Handles weird YAML edge cases like '- []'
    which pyyaml parses as a nested empty list.
    """
    if not values:
        return []
    if isinstance(values, str):
        # Defensive: single string → one-item list
        parsed = _parse_wikilink(values)
        return [parsed] if parsed else []
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for v in values:
        if not v:  # None, "", [], etc.
            continue
        parsed = _parse_wikilink(v)
        if parsed:
            result.append(parsed)
    return result


def _coerce_string_list(values: Any) -> list[str]:
    """Normalize a field that should be a list-of-strings.

    YAML may give us None, a single string, or a list. We want list[str].
    """
    if values is None or values == "":
        return []
    if isinstance(values, str):
        return [values]
    if isinstance(values, list):
        return [str(x) for x in values if x not in (None, "", [])]
    return [str(values)]


class ScoreHistoryEntry(BaseModel):
    """One entry in the per-idea score history log. Append-only."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    date: str                    # ISO date string, e.g. "2026-04-19"
    version: str                 # "v1" or "v2.1"
    score: float
    rank: Optional[int] = None
    trigger: Optional[str] = None


class BaseNote(BaseModel):
    """Fields shared across Idee, Chance, Wissen notes."""

    model_config = ConfigDict(
        # Allow unknown fields — vault frontmatter may grow over time
        # (prio, monat, custom user fields, etc. pass through untouched)
        extra="allow",
        # Allow both alias and field name on input
        populate_by_name=True,
    )

    # Identity — populated programmatically from filename, not from YAML
    id: str = Field(default="", description="Note ID, derived from filename")

    # Common Obsidian metadata
    database: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    project: Optional[str] = None
    source: Optional[str] = None
    link: Optional[str] = None
    type: str = "notiz"

    # Content + timestamps (common across all real vault notes)
    description: Optional[str] = None
    created: Optional[str] = None
    modified: Optional[str] = None

    # Pipeline-managed fields (filled by scoring/research in later steps)
    score: Optional[float] = None
    score_breakdown: Optional[dict] = None
    score_version: Optional[str] = None
    scored_at: Optional[str] = None
    research_fidelity: Optional[str] = None  # tier1 … tier5
    research_notes: Optional[str] = None    # T5 qualitative findings (markdown)

    @field_validator("database", mode="before")
    @classmethod
    def _parse_database_links(cls, v: Any) -> list[str]:
        return _parse_wikilink_list(v)

    @field_validator("tags", "aliases", mode="before")
    @classmethod
    def _coerce_to_list(cls, v: Any) -> list:
        return _coerce_string_list(v)


class IdeeNote(BaseNote):
    """A business idea — the entity we ultimately rank."""

    first_adopters: list[str] = Field(default_factory=list)
    mass_customers: list[str] = Field(default_factory=list)

    # v1 intrinsic fields — kept for backward compat, not read by v2.1 scoring
    market_size: ScoreValue = 6
    market_potential: ScoreValue = 6
    impact: ScoreValue = 6
    difficulty: ScoreValue = 6
    time_investment: ScoreValue = 6
    innovativeness: ScoreValue = 6

    # Links to chance and wissen notes
    chancen: list[str] = Field(default_factory=list)
    wissen: list[str] = Field(default_factory=list)

    notes: Optional[str] = None

    # v2.1 Attractiveness (LLM-populated by enrich-intrinsic)
    attractiveness_impact: ScoreValue = 6
    attractiveness_innovativeness: ScoreValue = 6
    attractiveness_mission_fit: ScoreValue = 6

    # v2.1 Fit
    fit_difficulty: ScoreValue = 6
    fit_time_to_first_revenue_months: Optional[int] = Field(default=None, ge=1, le=60)

    # Knowledge signals — computed deterministically from wissen links, never LLM
    mastery_leverage: float = Field(default=0.4, ge=0.0, le=1.0)
    obsession_leverage: float = Field(default=0.4, ge=0.0, le=1.0)
    cross_domain_flag: bool = False

    # Gates (LLM-populated by enrich-intrinsic)
    capital_class: Optional[Literal["bootstrappable", "seed", "vc_dependent"]] = None
    regulation_class: Optional[Literal["unregulated", "low", "high"]] = None
    willingness_to_pay: ScoreValue = 6
    killer_flag: bool = False

    # T5 signal
    t5_risk_flag: bool = False

    # Score metadata
    score_v1: Optional[float] = None
    score_history: List[ScoreHistoryEntry] = Field(default_factory=list)

    @field_validator("chancen", "wissen", mode="before")
    @classmethod
    def _parse_link_lists(cls, v: Any) -> list[str]:
        return _parse_wikilink_list(v)

    @field_validator("first_adopters", "mass_customers", mode="before")
    @classmethod
    def _coerce_customer_lists(cls, v: Any) -> list[str]:
        return _coerce_string_list(v)



class ChanceNote(BaseNote):
    """A problem field / opportunity space — what an idea addresses."""

    granularitaet: ScoreValue = 6
    urgency: ScoreValue = 6
    prevalence: ScoreValue = 6
    impact: ScoreValue = 6
    personal_experience: ScoreValue = 6
    market_awareness: ScoreValue = 6

    # NOTE: User's vault has the typo 'umprella_problem'.
    # Python uses the corrected name; YAML alias preserves the original key.
    umbrella_problem: list[str] = Field(
        default_factory=list,
        alias="umprella_problem",
    )

    @field_validator("umbrella_problem", mode="before")
    @classmethod
    def _parse_umbrella(cls, v: Any) -> list[str]:
        return _parse_wikilink_list(v)


class WissenNote(BaseNote):
    """A personal knowledge area — skills/networks the user can leverage."""

    enjoyment: ScoreValue = 6
    confidence: ScoreValue = 6

    # NOTE: User's vault has the typo 'credebility'.
    # Python uses the corrected name; YAML alias preserves the original key.
    credibility: ScoreValue = Field(default=6, alias="credebility")

    contacts: ScoreValue = 6


# --- Type dispatch -----------------------------------------------------------

# The vault uses an Obsidian database pattern: each note links to a "database"
# wikilink that identifies its type. We strip emoji and check for the keyword.
DATABASE_TYPE_MAP: dict[str, type[BaseNote]] = {
    "geschaeftsideen": IdeeNote,
    "chancen": ChanceNote,
    "wissen": WissenNote,
}


def detect_note_type(metadata: dict) -> Optional[type[BaseNote]]:
    """Detect the appropriate schema class from the note's `database` field.

    The vault uses semantic filenames (no _idee/_chance/_wissen suffix), so
    we read the type from the YAML `database` list. Each note typically links
    to one or more database notes like `[[geschaeftsideen🎲]]`.

    Returns None if no recognized database is found.
    """
    raw_databases = metadata.get("database")
    if not raw_databases:
        return None

    # Reuse the same parser the schema uses — keeps behaviour consistent
    parsed = _parse_wikilink_list(raw_databases)

    for db_id in parsed:
        # Match by prefix to handle emoji suffixes like "geschaeftsideen🎲"
        normalized = db_id.lower()
        for keyword, schema_cls in DATABASE_TYPE_MAP.items():
            if normalized.startswith(keyword):
                return schema_cls
    return None
