"""Tests for graph integrity verification."""

from __future__ import annotations

from mindcap.plugins.chatgpt.graph import (
    GraphIntegrityReport,
    verify_raw_mapping,
    verify_selected_path,
)


def _minimal_mapping(
    current_node: str = "asst",
) -> dict[str, object]:
    return {
        "root": {"id": "root", "parent": None, "children": ["user"], "message": None},
        "user": {
            "id": "user",
            "parent": "root",
            "children": ["asst"],
            "message": {"id": "user", "author": {"role": "user"}, "content": {}},
        },
        "asst": {
            "id": "asst",
            "parent": "user",
            "children": [],
            "message": {"id": "asst", "author": {"role": "assistant"}, "content": {}},
        },
    }


# ---------------------------------------------------------------------------
# Basic integrity
# ---------------------------------------------------------------------------


def test_valid_linear_mapping_is_complete() -> None:
    report = verify_raw_mapping(_minimal_mapping())
    assert report.complete is True
    assert report.errors == []
    assert report.warnings == []
    assert report.node_count == 3


def test_empty_mapping_is_incomplete() -> None:
    report = verify_raw_mapping({})
    assert report.complete is False
    assert report.errors


def test_non_dict_mapping_is_incomplete() -> None:
    report = verify_raw_mapping("not a dict")  # type: ignore[arg-type]
    assert report.complete is False


# ---------------------------------------------------------------------------
# Missing parents / children
# ---------------------------------------------------------------------------


def test_missing_parent_detected() -> None:
    mapping = {
        "a": {"id": "a", "parent": "MISSING", "children": [], "message": None},
    }
    report = verify_raw_mapping(mapping)
    assert report.complete is False
    assert report.missing_parent_count == 1
    assert any("MISSING" in w for w in report.warnings)


def test_missing_child_detected() -> None:
    mapping = {
        "a": {"id": "a", "parent": None, "children": ["MISSING"], "message": None},
    }
    report = verify_raw_mapping(mapping)
    assert report.complete is False
    assert report.missing_children_count == 1


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_cycle_detected() -> None:
    mapping = {
        "a": {"id": "a", "parent": None, "children": ["b"], "message": None},
        "b": {"id": "b", "parent": "a", "children": ["a"], "message": None},
    }
    report = verify_raw_mapping(mapping)
    assert report.cycle_count >= 1
    assert report.complete is False


def test_cycle_does_not_hang() -> None:
    """A tight cycle (A → B → A) must terminate quickly."""
    mapping = {
        "a": {"id": "a", "parent": "b", "children": ["b"], "message": None},
        "b": {"id": "b", "parent": "a", "children": ["a"], "message": None},
    }
    # This must complete without hanging.
    report = verify_raw_mapping(mapping)
    assert isinstance(report, GraphIntegrityReport)


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------


def test_orphan_detected() -> None:
    # "orphan" claims root as its parent, but root does not list it as a
    # child.  Reachability from root therefore excludes "orphan".
    mapping = {
        "root": {
            "id": "root",
            "parent": None,
            "children": [],  # orphan is NOT listed here
            "message": None,
        },
        "orphan": {
            "id": "orphan",
            "parent": "root",
            "children": [],
            "message": None,
        },
    }
    report = verify_raw_mapping(mapping)
    assert report.orphan_count >= 1
    assert report.complete is False


# ---------------------------------------------------------------------------
# current_node validation
# ---------------------------------------------------------------------------


def test_invalid_current_node_warns() -> None:
    mapping = _minimal_mapping()
    report = verify_raw_mapping(mapping, current_node="nonexistent")
    assert report.complete is False
    assert any("nonexistent" in w for w in report.warnings)


def test_valid_current_node_no_warning() -> None:
    mapping = _minimal_mapping()
    report = verify_raw_mapping(mapping, current_node="asst")
    assert report.complete is True


# ---------------------------------------------------------------------------
# Parent-child disagreement
# ---------------------------------------------------------------------------


def test_parent_child_disagreement() -> None:
    mapping = {
        "a": {"id": "a", "parent": None, "children": ["b"], "message": None},
        "b": {"id": "b", "parent": "a", "children": ["c"], "message": None},
        "c": {
            "id": "c",
            "parent": "WRONG",  # claims wrong parent
            "children": [],
            "message": None,
        },
    }
    report = verify_raw_mapping(mapping)
    assert report.complete is False
    assert any(
        "disagreement" in w.lower() or "parent" in w.lower()
        for w in report.warnings
    )


# ---------------------------------------------------------------------------
# as_dict
# ---------------------------------------------------------------------------


def test_as_dict_has_required_fields() -> None:
    report = verify_raw_mapping(_minimal_mapping())
    d = report.as_dict()
    required = {
        "complete",
        "node_count",
        "message_count",
        "cycle_count",
        "orphan_count",
        "missing_parent_count",
        "missing_children_count",
        "duplicate_id_count",
        "warnings",
        "errors",
    }
    assert required.issubset(d.keys())


# ---------------------------------------------------------------------------
# Selected path validation
# ---------------------------------------------------------------------------


def test_valid_selected_path_no_warnings() -> None:
    mapping = _minimal_mapping()
    warnings = verify_selected_path(mapping, ["root", "user", "asst"])
    assert warnings == []


def test_missing_node_in_selected_path_warns() -> None:
    mapping = _minimal_mapping()
    warnings = verify_selected_path(mapping, ["root", "MISSING"])
    assert any("MISSING" in w for w in warnings)


def test_path_break_warns() -> None:
    mapping = _minimal_mapping()
    # "root" then "asst" skips "user"
    warnings = verify_selected_path(mapping, ["root", "asst"])
    assert any("break" in w.lower() or "parent" in w.lower() for w in warnings)


def test_empty_selected_path_no_warnings() -> None:
    mapping = _minimal_mapping()
    assert verify_selected_path(mapping, []) == []
