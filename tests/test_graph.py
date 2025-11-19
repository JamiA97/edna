from __future__ import annotations

from pathlib import Path

from eng_dna import operations


def _create_lineage(db, tmp_path: Path) -> tuple[dict, dict, dict]:
    parent_path = tmp_path / "parent.txt"
    parent_path.write_text("parent", encoding="utf-8")
    child_path = tmp_path / "child.txt"
    child_path.write_text("child", encoding="utf-8")
    grandchild_path = tmp_path / "grandchild.txt"
    grandchild_path.write_text("grandchild", encoding="utf-8")

    parent = operations.tag_file(
        db,
        parent_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=None,
    )
    child = operations.tag_file(
        db,
        child_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=None,
    )
    grandchild = operations.tag_file(
        db,
        grandchild_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=None,
    )
    operations.link_artefacts(db, child, [parent], relation_type="derived_from", reason="unit test")
    operations.link_artefacts(db, grandchild, [child], relation_type="derived_from", reason="unit test")
    return parent, child, grandchild


def test_build_lineage_graph_ancestors(db, tmp_path: Path) -> None:
    parent, child, grandchild = _create_lineage(db, tmp_path)

    nodes, edges = operations.build_lineage_graph(db, grandchild, scope="ancestors")

    assert set(nodes) == {parent["id"], child["id"], grandchild["id"]}
    assert {e.parent_id for e in edges} == {parent["id"], child["id"]}
    assert {e.child_id for e in edges} == {child["id"], grandchild["id"]}
    assert len(edges) == 2


def test_build_lineage_graph_descendants(db, tmp_path: Path) -> None:
    parent, child, grandchild = _create_lineage(db, tmp_path)

    nodes, edges = operations.build_lineage_graph(db, parent, scope="descendants")

    assert set(nodes) == {parent["id"], child["id"], grandchild["id"]}
    assert {(e.parent_id, e.child_id) for e in edges} == {
        (parent["id"], child["id"]),
        (child["id"], grandchild["id"]),
    }


def test_formatters() -> None:
    nodes = {
        1: operations.LineageNode(id=1, dna_token="edna_abcd12345678", path="/tmp/setup.py", type="script"),
        2: operations.LineageNode(id=2, dna_token="edna_dcba87654321", path="/tmp/results.csv", type="report"),
    }
    edges = [
        operations.LineageEdge(parent_id=1, child_id=2, relation_type="derived_from", reason=None),
    ]

    mermaid = operations.format_lineage_as_mermaid(nodes, edges, direction="LR")
    assert "flowchart LR" in mermaid
    assert 'n_1["abcd1234 | script | setup.py"]' in mermaid
    assert "n_1 -->|derived_from| n_2" in mermaid

    dot = operations.format_lineage_as_dot(nodes, edges, direction="TB")
    assert dot.startswith("digraph edna_lineage")
    assert "rankdir=TB" in dot
    assert 'n_1 [label="abcd1234 | script | setup.py"];' in dot
    assert 'n_1 -> n_2 [label="derived_from"];' in dot
