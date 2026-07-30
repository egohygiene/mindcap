"""Conversation-graph integrity verification for ChatGPT conversations.

This module provides deterministic, non-recursive integrity checks that can
be applied to both raw provider mappings (during normalisation) and to
already-normalised message dictionaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphIntegrityReport:
    """Structured result of a graph integrity check.

    ``complete`` is True only when **all** checks passed without warnings.
    Callers may archive malformed evidence and attach this report to the
    resulting bundle.
    """

    complete: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # Counters for reporting
    node_count: int = 0
    message_count: int = 0
    cycle_count: int = 0
    orphan_count: int = 0
    missing_parent_count: int = 0
    missing_children_count: int = 0
    duplicate_id_count: int = 0

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)
        self.complete = False

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.complete = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "node_count": self.node_count,
            "message_count": self.message_count,
            "cycle_count": self.cycle_count,
            "orphan_count": self.orphan_count,
            "missing_parent_count": self.missing_parent_count,
            "missing_children_count": self.missing_children_count,
            "duplicate_id_count": self.duplicate_id_count,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def verify_raw_mapping(
    raw_mapping: dict[str, Any],
    current_node: str | None = None,
) -> GraphIntegrityReport:
    """Check a ChatGPT provider ``mapping`` dict for structural integrity.

    Designed to terminate in O(N) time regardless of graph shape; it will
    not recurse indefinitely even in the presence of cycles.

    Parameters
    ----------
    raw_mapping:
        The ``mapping`` object from a ChatGPT conversation JSON.
    current_node:
        The ``current_node`` field from the conversation, if present.

    Returns
    -------
    GraphIntegrityReport
        A populated report.  ``complete`` is True only when no issues were
        found.
    """
    report = GraphIntegrityReport(complete=True)

    if not isinstance(raw_mapping, dict) or not raw_mapping:
        report.add_error("Mapping is empty or not a dict.")
        return report

    # --- Detect duplicate keys (JSON allows duplicates; we normalise via dict) ---
    all_keys = list(raw_mapping.keys())
    if len(all_keys) != len(set(all_keys)):
        seen: set[str] = set()
        for key in all_keys:
            if key in seen:
                report.add_warning(f"Duplicate mapping key: {key!r}")
                report.duplicate_id_count += 1
            seen.add(key)

    report.node_count = len(raw_mapping)

    # Build parent/children maps with coercion from the raw mapping.
    parent_of: dict[str, str | None] = {}
    children_of: dict[str, list[str]] = {}

    for node_key, raw_node in raw_mapping.items():
        if not isinstance(raw_node, dict):
            report.add_warning(f"Node {node_key!r} is not a dict; skipped.")
            continue
        node_id = str(raw_node.get("id") or node_key)
        parent = raw_node.get("parent")
        children = [str(c) for c in (raw_node.get("children") or []) if c is not None]
        parent_of[node_id] = str(parent) if parent is not None else None
        children_of[node_id] = children

        if raw_node.get("message") is not None:
            report.message_count += 1

    known_ids = set(parent_of.keys())

    # --- Missing parents ---
    for node_id, parent_id in parent_of.items():
        if parent_id is not None and parent_id not in known_ids:
            report.add_warning(
                f"Node {node_id!r} references unknown parent {parent_id!r}."
            )
            report.missing_parent_count += 1

    # --- Missing children ---
    for node_id, child_ids in children_of.items():
        for child_id in child_ids:
            if child_id not in known_ids:
                report.add_warning(
                    f"Node {node_id!r} references unknown child {child_id!r}."
                )
                report.missing_children_count += 1

    # --- Parent-child disagreement ---
    for node_id, child_ids in children_of.items():
        for child_id in child_ids:
            if child_id in parent_of and parent_of[child_id] != node_id:
                report.add_warning(
                    f"Parent-child disagreement: {node_id!r} lists {child_id!r} as "
                    f"child, but {child_id!r} declares parent "
                    f"{parent_of[child_id]!r}."
                )

    # --- Cycle detection (iterative topological sort / DFS colouring) ---
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n: WHITE for n in known_ids}
    cycle_detected: set[str] = set()

    for start in known_ids:
        if colour[start] != WHITE:
            continue
        # Iterative DFS
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                colour[node] = BLACK
                continue
            if colour[node] == GREY:
                # Back-edge → cycle
                if node not in cycle_detected:
                    cycle_detected.add(node)
                    report.add_warning(f"Cycle detected involving node {node!r}.")
                    report.cycle_count += 1
                continue
            if colour[node] == BLACK:
                continue
            colour[node] = GREY
            stack.append((node, True))
            for child_id in children_of.get(node, []):
                if child_id in colour:
                    stack.append((child_id, False))

    # --- Orphan detection (reachability from roots) ---
    roots = [n for n, p in parent_of.items() if p is None]
    if not roots:
        report.add_error("No root nodes found (no node without a parent).")
    else:
        reachable: set[str] = set()
        work = list(roots)
        while work:
            current = work.pop()
            if current in reachable:
                continue
            reachable.add(current)
            for child_id in children_of.get(current, []):
                if child_id not in reachable:
                    work.append(child_id)
        unreachable = known_ids - reachable
        if unreachable:
            for node_id in sorted(unreachable):
                report.add_warning(f"Unreachable (orphaned) node: {node_id!r}.")
                report.orphan_count += 1

    # --- current_node validation ---
    if current_node is not None and str(current_node) not in known_ids:
        report.add_warning(
            f"current_node {current_node!r} is not present in the mapping."
        )

    return report


def verify_selected_path(
    mapping: dict[str, Any],
    selected_path: list[str],
) -> list[str]:
    """Return a list of warning messages for an invalid selected path.

    An empty list means the path is valid.
    """
    warnings: list[str] = []
    if not selected_path:
        return warnings
    for i, node_id in enumerate(selected_path):
        if node_id not in mapping:
            warnings.append(
                f"Selected-path position {i}: node {node_id!r} not in mapping."
            )
            continue
        if i > 0:
            prev_id = selected_path[i - 1]
            node = mapping.get(node_id)
            if isinstance(node, dict) and node.get("parent") != prev_id:
                warnings.append(
                    f"Selected-path break between position {i - 1} "
                    f"({prev_id!r}) and {i} ({node_id!r}): declared parent "
                    f"is {node.get('parent')!r}."
                )
    return warnings
