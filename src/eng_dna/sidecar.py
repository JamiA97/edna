"""Read/write helpers for EDNA sidecars and embedded identity markers.

EDNA avoids mutating source artefacts by default and instead stores identity in
``<filename>.edna`` JSON sidecars. Optional embedded markers are supported for
textual formats but are disabled until hashing supports stable canonicalisation
without affecting version detection.
"""
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
    """
    Derive the sidecar path for a given file.

    Parameters:
        file_path: Path to the artefact on disk.

    Returns:
        Path object pointing to ``<name>.edna`` alongside the file.

    Side Effects:
        None.
    """
    return file_path.with_name(file_path.name + ".edna")


def read_identity(file_path: Path) -> Optional[IdentityInfo]:
    """
    Load identity information from an artefact.

    Prefer embedded markers (when enabled and supported) and fall back to the
    JSON sidecar. This ordering allows light-touch restoration if sidecars are
    deleted but content still carries metadata.

    Parameters:
        file_path: Path to the artefact.

    Returns:
        IdentityInfo instance or None if nothing could be read.

    Side Effects:
        Reads the artefact and/or sidecar from disk.
    """
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
    """
    Persist identity information for a tracked artefact.

    Writes both the JSON sidecar (always) and an embedded marker only when
    embedding is enabled. This ensures downstream tools can find DNA tokens
    even if the sidecar is moved or lost.

    Parameters:
        file_path: Artefact path.
        dna_token: Assigned DNA token.
        file_hash: Hash of the current content.
        artefact_type: Optional type label.
        stored_path: Normalised path stored in the DB for reconciliation.

    Returns:
        None.

    Side Effects:
        Writes/overwrites ``<file>.edna``; may append embedded marker to the file
        when embedding is enabled.
    """
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
    """
    Append an embedded identity marker to a supported text file.

    Parameters:
        file_path: Target file.
        handler: Comment syntax to wrap the marker.
        payload: Metadata dictionary to serialise.

    Returns:
        True when the marker is written.

    Side Effects:
        Rewrites the file with a trailing EDNA comment; strips existing markers
        first to avoid duplication.
    """
    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    marker = _format_marker(payload, handler)
    lines = [line for line in lines if EMBED_SENTINEL not in line]
    lines.append(marker)
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _format_marker(payload: dict, handler: Handler) -> str:
    """
    Render the embedded marker string.

    Parameters:
        payload: Metadata to serialise as JSON.
        handler: Comment delimiters for the host format.

    Returns:
        Comment-wrapped marker line containing a JSON blob.

    Side Effects:
        None.
    """
    return f"{handler.prefix}{EMBED_SENTINEL} {json.dumps(payload)}{handler.suffix}"


def _read_embedded_identity(file_path: Path, handler: Handler) -> Optional[IdentityInfo]:
    """
    Parse an embedded identity marker if present.

    Scans from the end of the file so the most recent marker wins and avoids
    deep parsing of large artefacts.

    Parameters:
        file_path: Artefact path.
        handler: Comment syntax used by the format.

    Returns:
        IdentityInfo populated from the marker or None if not found/invalid.

    Side Effects:
        Reads the file from disk.
    """
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
    """
    Load identity information from a ``.edna`` sidecar.

    Parameters:
        file_path: Artefact path used to derive the sidecar location.

    Returns:
        IdentityInfo or None if the sidecar is absent or corrupted.

    Side Effects:
        Reads the sidecar file; tolerates JSON parsing failures to avoid raising.
    """
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
