"""Embedded metadata + sidecar helpers (Background.md §5)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .identity import IdentityInfo, normalize_path

EMBED_SENTINEL = ":edna:"
# Embedding metadata changes the tracked file contents and interferes with
# hash-based versioning. Disable embedding until we support canonical hashing.
EMBED_ENABLED = False


@dataclass
class Handler:
    prefix: str
    suffix: str = ""
    supports_embed: bool = True


COMMENT_HANDLERS: dict[str, Handler] = {
    ".py": Handler(prefix="# "),
    ".txt": Handler(prefix="# "),
    ".md": Handler(prefix="<!-- ", suffix=" -->"),
    ".yaml": Handler(prefix="# "),
    ".yml": Handler(prefix="# "),
    ".csv": Handler(prefix="# "),
}


def get_sidecar_path(file_path: Path) -> Path:
    return file_path.with_name(file_path.name + ".edna")


def read_identity(file_path: Path) -> Optional[IdentityInfo]:
    handler = COMMENT_HANDLERS.get(file_path.suffix.lower())
    if handler and handler.supports_embed:
        embedded = _read_embedded_identity(file_path, handler)
        if embedded:
            return embedded
    return _read_sidecar_identity(file_path)


def write_identity(
    file_path: Path,
    dna_token: str,
    file_hash: str,
    artefact_type: Optional[str],
    stored_path: str,
) -> None:
    payload = {
        "dna": dna_token,
        "hash": file_hash,
        "type": artefact_type,
        "path": stored_path,
    }

    handler = COMMENT_HANDLERS.get(file_path.suffix.lower())
    embedded = False
    if EMBED_ENABLED and handler and handler.supports_embed:
        embedded = _write_embedded_identity(file_path, handler, payload)

    sidecar_path = get_sidecar_path(file_path)
    sidecar_path.write_text(json.dumps(payload, indent=2))

    if embedded and sidecar_path.stat().st_size == 0:
        # Paranoid guard – we always expect content
        sidecar_path.write_text(json.dumps(payload))


def _write_embedded_identity(file_path: Path, handler: Handler, payload: dict) -> bool:
    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    marker = _format_marker(payload, handler)
    lines = [line for line in lines if EMBED_SENTINEL not in line]
    lines.append(marker)
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _format_marker(payload: dict, handler: Handler) -> str:
    return f"{handler.prefix}{EMBED_SENTINEL} {json.dumps(payload)}{handler.suffix}"


def _read_embedded_identity(file_path: Path, handler: Handler) -> Optional[IdentityInfo]:
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    for line in reversed(text.splitlines()):
        if EMBED_SENTINEL in line:
            json_blob = line.split(EMBED_SENTINEL, 1)[1].strip()
            if json_blob.endswith("-->"):
                json_blob = json_blob[: -3].strip()
            if json_blob.startswith("{"):
                try:
                    data = json.loads(json_blob)
                except json.JSONDecodeError:
                    continue
                return IdentityInfo(
                    dna_token=data.get("dna"),
                    file_hash=data.get("hash"),
                    path=data.get("path", normalize_path(file_path)),
                )
    return None


def _read_sidecar_identity(file_path: Path) -> Optional[IdentityInfo]:
    sidecar_path = get_sidecar_path(file_path)
    if not sidecar_path.exists():
        return None
    try:
        data = json.loads(sidecar_path.read_text())
    except json.JSONDecodeError:
        return None
    return IdentityInfo(
        dna_token=data.get("dna"),
        file_hash=data.get("hash"),
        path=data.get("path", normalize_path(file_path)),
    )
