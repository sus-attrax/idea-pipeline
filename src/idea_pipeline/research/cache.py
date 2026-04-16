"""SQLite cache for research results. TODO Step 6.

Key: hash(query + source). Value: raw response + timestamp.
Reason: re-running scoring should never re-fetch the same data.
"""
