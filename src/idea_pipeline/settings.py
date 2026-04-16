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

_DEFAULT_VAULT = "~/vaults/idea-validation"


def get_vault_path(override: str | Path | None = None) -> Path:
    """Resolve the active vault path."""
    if override:
        return Path(override).expanduser().resolve()
    raw = os.environ.get("IDEAPIPE_VAULT", _DEFAULT_VAULT)
    return Path(raw).expanduser().resolve()
