"""Pipeline settings — vault path resolution and global config.

The vault path is resolved in this order:
  1. Explicit CLI argument (--vault)
  2. IDEAPIPE_VAULT environment variable
  3. Default: ~/vaults/idea-validation

Why a settings module instead of hardcoding everywhere?
- Single source of truth for paths
- Environment variable lets you switch vaults without code changes
- Claude Code / NL interface can set the env once per session
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_VAULT = "~/vaults/idea-validation"
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def get_vault_path(override: str | Path | None = None) -> Path:
    """Resolve the active vault path."""
    if override:
        return Path(override).expanduser().resolve()
    raw = os.environ.get("IDEAPIPE_VAULT", _DEFAULT_VAULT)
    return Path(raw).expanduser().resolve()


def load_tiers_config() -> dict[str, Any]:
    """Load tier limits from config/tiers.yaml."""
    path = _PROJECT_ROOT / "config" / "tiers.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("tiers", {})
