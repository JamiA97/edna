"""Identity utilities for eng-dna (Background.md §2, §6)."""
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
    return str(Path(path).expanduser().resolve())


def compute_file_hash(path: os.PathLike | str, chunk_size: int = 1024 * 1024) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def generate_dna_token() -> str:
    return DNA_PREFIX + str(uuid.uuid4())


def looks_like_dna(value: str) -> bool:
    return value.startswith(DNA_PREFIX)
