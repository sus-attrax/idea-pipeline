"""Vault I/O: read and write Obsidian markdown files with YAML frontmatter.

TODO Step 3: Implement atomic read/write using python-frontmatter.
- read_note(path) -> Pydantic model
- write_note(path, model) -> atomic (temp + rename, no half-written files)
- list_notes(vault_path, type) -> iterator over notes of a given type
"""
