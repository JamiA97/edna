from __future__ import annotations

from eng_dna import artefacts
from eng_dna.identity import compute_file_hash, generate_dna_token, looks_like_dna


def test_compute_hash_and_dna(sample_file) -> None:
    digest = compute_file_hash(sample_file)
    assert len(digest) == 64
    dna = generate_dna_token()
    assert dna.startswith("edna_")
    assert looks_like_dna(dna)


def test_lookup_helpers(db, sample_file) -> None:
    digest = compute_file_hash(sample_file)
    dna = generate_dna_token()
    artefacts.create_artefact(
        db,
        dna_token=dna,
        path=str(sample_file),
        file_hash=digest,
        artefact_type="text",
        description="sample",
    )
    by_dna = artefacts.lookup_by_dna(db, dna)
    assert by_dna is not None
    by_path = artefacts.lookup_by_path(db, str(sample_file))
    assert by_path["dna_token"] == dna
    by_hash = artefacts.lookup_by_hash(db, digest)
    assert by_hash["path"] == by_path["path"]
