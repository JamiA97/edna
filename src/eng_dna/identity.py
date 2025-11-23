"""Identity utilities that normalise paths and create stable DNA tokens.

This module centralises how eng-dna recognises files: every file path is
canonically resolved before storing in the database, hashes are always SHA-256,
and DNA tokens use a consistent prefix. Having a single place for these rules
avoids divergent identity schemes between CLI commands and background rescans.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DNA_PREFIX = "edna_"


@dataclass
class IdentityInfo:
    dna_token: Optional[str]
    file_hash: Optional[str]
    path: str


def normalize_path(path: os.PathLike | str) -> str:
    """
    Canonicalise a filesystem path for storage and comparisons.

    Ensures EDNA uses the same absolute, expanded path everywhere so that
    move-detection and hash lookups are deterministic across commands.

    Parameters:
        path: Pathlike or string pointing to a file.

    Returns:
        Absolute, expanded path as a string with user components resolved.

    Side Effects:
        None; purely deterministic transformation.
    """
    return str(Path(path).expanduser().resolve())


def compute_file_hash(path: os.PathLike | str, chunk_size: int = 1024 * 1024) -> str:
    """
    Compute a SHA-256 hash of the file at *path*.

    Central hash routine keeps versioning decisions consistent between tag,
    rescan, and housekeeping steps.

    Parameters:
        path: File to hash.
        chunk_size: Bytes per read; tuned to avoid high memory usage.

    Returns:
        Hex digest string of the file contents.

    Side Effects:
        Reads the file from disk.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def generate_dna_token() -> str:
    """
    Generate a new unique DNA token.

    Tokens are UUID-based and prefixed so they are easy to recognise and avoid
    collisions with user input such as file names.

    Returns:
        DNA token string, e.g. ``edna_<uuid>``.

    Side Effects:
        None.
    """
    return DNA_PREFIX + str(uuid.uuid4())


def looks_like_dna(value: str) -> bool:
    """
    Determine if a string matches the EDNA DNA token pattern.

    Keeps heuristics for DNA detection central so CLI parsing, resolver logic,
    and lineage tools all behave identically.

    Parameters:
        value: Candidate string.

    Returns:
        True if *value* begins with the configured DNA prefix.

    Side Effects:
        None.
    """
    return value.startswith(DNA_PREFIX)
