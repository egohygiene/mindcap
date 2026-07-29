from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mindcap.core.errors import NormalizationError
from mindcap.core.models import CaptureEnvelope

# ---------------------------------------------------------------------------
# Classification constants
# ---------------------------------------------------------------------------

_VISIBLE_ROLES = {"user", "assistant", "tool", "developer"}

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify_node(
    raw_message: dict[str, Any] | None,
) -> tuple[str, str, bool, bool, str | None]:
    """Classify a provider graph node.

    Returns
    -------
    (visibility, semantic_type, renderable, knowledge_eligible, exclusion_reason)

    Visibility values: structural | hidden | visible | unknown
    Semantic-type values: structural_node | internal_context | system_message |
        user_message | assistant_message | tool_message | unsupported
    """
    if raw_message is None:
        return "structural", "structural_node", False, False, "no_provider_message"

    author = raw_message.get("author") or {}
    role = str(author.get("role") or "unknown")
    content = raw_message.get("content") or {}
    content_type = str(content.get("content_type") or "")
    metadata = raw_message.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    # --- user_editable_context / is_user_system_message ---
    if content_type == "user_editable_context" or metadata.get(
        "is_user_system_message"
    ):
        return "hidden", "internal_context", False, False, "user_editable_context"

    # --- model_editable_context ---
    if content_type == "model_editable_context":
        return "hidden", "internal_context", False, False, "model_editable_context"

    # --- Explicit visibility flag ---
    is_visually_hidden = metadata.get("is_visually_hidden_from_conversation")

    if is_visually_hidden is True:
        semantic = _role_to_semantic(role)
        return "hidden", semantic, False, False, "is_visually_hidden_from_conversation"

    # --- Contextual system-message metadata ---
    if (
        metadata.get("is_contextual_answers_system_message")
        or metadata.get("rebase_developer_message")
        or "exclusive_key" in metadata
    ):
        semantic = _role_to_semantic(role)
        return "hidden", semantic, False, False, "contextual_system_message"

    # --- Explicitly visible ---
    if is_visually_hidden is False:
        semantic = _role_to_semantic(role)
        renderable = content_type != "" and _has_renderable_content(content)
        return "visible", semantic, renderable, True, None

    # --- Fallback: ordinary user / assistant without explicit flag ---
    if role in {"user", "assistant"}:
        semantic = _role_to_semantic(role)
        renderable = _has_renderable_content(content)
        return "visible", semantic, renderable, True, None

    # --- System / developer / tool without explicit flag → implicitly hidden ---
    if role in {"system", "developer", "tool"}:
        semantic = _role_to_semantic(role)
        return "hidden", semantic, False, False, "implicit_system_hidden"

    return "unknown", "unsupported", False, False, "unknown_role"


def _role_to_semantic(role: str) -> str:
    return {
        "user": "user_message",
        "assistant": "assistant_message",
        "system": "system_message",
        "developer": "system_message",
        "tool": "tool_message",
    }.get(role, "unsupported")


def _has_renderable_content(content: dict[str, Any]) -> bool:
    """Return True if the content node contains at least one non-empty part."""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return bool(content)
    return any(p for p in parts if p != "" and p is not None)


def _compute_branch_index(
    raw_mapping: dict[str, Any],
    roots: list[str],
    selected_path: list[str],
) -> list[dict[str, Any]]:
    """Derive the branch navigation index from the message graph.

    Each branch record covers a linear segment of the message tree from one
    branching point to the next (or to a leaf).  The message graph is
    authoritative; this index is a derived convenience structure.
    """
    selected_set: set[str] = set(selected_path)
    has_selected = bool(selected_set)
    branches: list[dict[str, Any]] = []
    counter = 0
    all_visited: set[str] = set()

    def walk(
        start_id: str,
        parent_branch_id: str | None,
        sibling_index: int,
        branch_point_id: str | None,
    ) -> None:
        nonlocal counter
        branch_id = f"branch-{counter}"
        counter += 1
        segment: list[str] = []
        current_id: str | None = start_id

        while current_id is not None and current_id not in all_visited:
            all_visited.add(current_id)
            node = raw_mapping.get(current_id)
            if not isinstance(node, dict):
                break
            segment.append(current_id)
            children = [str(c) for c in (node.get("children") or [])]

            if len(children) == 1:
                current_id = children[0]
            elif len(children) == 0:
                branches.append(
                    {
                        "branch_id": branch_id,
                        "branch_point_message_id": branch_point_id,
                        "parent_branch_id": parent_branch_id,
                        "branch_index": sibling_index,
                        "message_ids": list(segment),
                        "leaf_message_id": current_id,
                        "is_selected": (
                            any(m in selected_set for m in segment)
                            if has_selected
                            else None
                        ),
                    }
                )
                return
            else:
                branches.append(
                    {
                        "branch_id": branch_id,
                        "branch_point_message_id": branch_point_id,
                        "parent_branch_id": parent_branch_id,
                        "branch_index": sibling_index,
                        "message_ids": list(segment),
                        "leaf_message_id": current_id,
                        "is_selected": (
                            any(m in selected_set for m in segment)
                            if has_selected
                            else None
                        ),
                    }
                )
                for idx, child_id in enumerate(children):
                    walk(child_id, branch_id, idx, current_id)
                return

        # Terminal condition: cycle detected, missing node, or malformed entry.
        if segment:
            branches.append(
                {
                    "branch_id": branch_id,
                    "branch_point_message_id": branch_point_id,
                    "parent_branch_id": parent_branch_id,
                    "branch_index": sibling_index,
                    "message_ids": list(segment),
                    "leaf_message_id": segment[-1],
                    "is_selected": (
                        any(m in selected_set for m in segment)
                        if has_selected
                        else None
                    ),
                }
            )

    for root_id in roots:
        walk(root_id, None, 0, None)

    return branches


def _select_conversation(payload: Any, identifier: str) -> dict[str, Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("mapping"), dict):
            return payload
        nested = payload.get("conversation")
        if isinstance(nested, dict) and isinstance(nested.get("mapping"), dict):
            return nested

    if isinstance(payload, list):
        matches = [
            item
            for item in payload
            if isinstance(item, dict)
            and str(item.get("id") or item.get("conversation_id")) == identifier
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches and len(payload) == 1 and isinstance(payload[0], dict):
            return payload[0]

    raise NormalizationError(
        "The captured JSON does not contain a recognizable ChatGPT "
        'conversation "mapping".'
    )


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    return str(value)


def _content_parts(
    message: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalise provider content into typed content-part records.

    Returns
    -------
    (parts, discovered_attachments)

    For ``multimodal_text`` nodes each part is normalised individually:
    - ``image_asset_pointer`` → ``attachment_reference``
    - plain strings → ``text``
    - other dicts → the content-type label of the parent node

    For all other content types each part is emitted with ``type=text``
    when the part is a plain string, otherwise with the parent content-type.
    """
    content = message.get("content")
    if not isinstance(content, dict):
        return [], []

    content_type = str(content.get("content_type") or "unknown")
    raw_parts = content.get("parts")
    if not isinstance(raw_parts, list):
        raw_parts = [content]

    # Attachment metadata keyed by provider attachment-ID.
    attachments_meta: dict[str, dict[str, Any]] = {}
    for att in (message.get("metadata") or {}).get("attachments") or []:
        if isinstance(att, dict) and att.get("id"):
            attachments_meta[str(att["id"])] = att

    parts: list[dict[str, Any]] = []
    discovered_attachments: list[dict[str, Any]] = []

    for index, raw_part in enumerate(raw_parts):
        part_id = f"part-{index}"

        if content_type == "multimodal_text" and isinstance(raw_part, dict):
            sub_type = str(raw_part.get("content_type") or "")

            if sub_type == "image_asset_pointer":
                asset_pointer = str(raw_part.get("asset_pointer") or "")
                # Extract the opaque ID after the scheme separator.
                attachment_id: str | None = None
                if "://" in asset_pointer:
                    attachment_id = asset_pointer.split("://", 1)[1] or None
                meta = (
                    attachments_meta.get(attachment_id or "")
                    if attachment_id
                    else {}
                )
                mime_type: str | None = (meta or {}).get("mime_type")

                parts.append(
                    {
                        "part_id": part_id,
                        "type": "attachment_reference",
                        "value": None,
                        "mime_type": mime_type,
                        "attachment_id": attachment_id,
                        "filename": (meta or {}).get("name"),
                        "provider_metadata": {
                            k: v
                            for k, v in raw_part.items()
                            if k != "content_type"
                        },
                    }
                )

                if attachment_id:
                    discovered_attachments.append(
                        {
                            "attachment_id": attachment_id,
                            "provider_attachment_id": attachment_id,
                            "message_id": message.get("id"),
                            "filename": (meta or {}).get("name"),
                            "mime_type": mime_type,
                            "size_bytes": raw_part.get("size_bytes")
                            or (meta or {}).get("size"),
                            "width": raw_part.get("width"),
                            "height": raw_part.get("height"),
                            "asset_pointer": asset_pointer,
                            "archive_path": None,
                            "sha256": None,
                            "capture_status": "discovered",
                            "failure_reason": "attachment_not_downloaded",
                            "provider_metadata": meta or {},
                        }
                    )
                continue

            # Non-image dict part inside multimodal_text
            parts.append(
                {
                    "part_id": part_id,
                    "type": sub_type or content_type,
                    "value": raw_part,
                    "mime_type": None,
                    "attachment_id": None,
                    "provider_metadata": {},
                }
            )
            continue

        # Plain-string parts (any content type)
        if isinstance(raw_part, str):
            parts.append(
                {
                    "part_id": part_id,
                    "type": "text",
                    "value": raw_part,
                    "mime_type": None,
                    "attachment_id": None,
                    "provider_metadata": {},
                }
            )
            continue

        # Fallback: preserve as-is under parent content-type label
        parts.append(
            {
                "part_id": part_id,
                "type": content_type,
                "value": raw_part,
                "mime_type": None,
                "attachment_id": None,
                "provider_metadata": {},
            }
        )

    return parts, discovered_attachments


def _selected_path(mapping: dict[str, Any], current_node: str | None) -> list[str]:
    if current_node not in mapping:
        return []
    reversed_path: list[str] = []
    visited: set[str] = set()
    node_id: str | None = current_node
    while node_id and node_id in mapping and node_id not in visited:
        visited.add(node_id)
        reversed_path.append(node_id)
        node = mapping[node_id]
        node_id = node.get("parent") if isinstance(node, dict) else None
    return list(reversed(reversed_path))


def normalize_chatgpt(
    envelope: CaptureEnvelope, requested_identifier: str
) -> dict[str, Any]:
    if not envelope.response_units:
        raise NormalizationError("Capture produced no response units.")

    import json

    payload = json.loads(envelope.response_units[0].body)
    conversation = _select_conversation(payload, requested_identifier)
    raw_mapping = conversation.get("mapping")
    if not isinstance(raw_mapping, dict) or not raw_mapping:
        raise NormalizationError("ChatGPT conversation mapping is empty.")

    messages: dict[str, dict[str, Any]] = {}
    roots: list[str] = []
    # Only visible participants are included here.
    visible_participants: dict[str, dict[str, Any]] = {}
    all_attachments: list[dict[str, Any]] = []

    # Counters
    provider_node_count = 0
    provider_message_count = 0
    structural_node_count = 0
    hidden_message_count = 0
    visible_message_count = 0
    knowledge_eligible_message_count = 0

    for node_key, raw_node in raw_mapping.items():
        if not isinstance(raw_node, dict):
            continue
        provider_node_count += 1
        node_id = str(raw_node.get("id") or node_key)
        parent = raw_node.get("parent")
        children = [str(value) for value in raw_node.get("children") or []]
        raw_message = raw_node.get("message")
        message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}

        # ------------------------------------------------------------------ #
        # Classify the node
        # ------------------------------------------------------------------ #
        visibility, semantic_type, renderable, knowledge_eligible, exclusion_reason = (
            _classify_node(raw_message if isinstance(raw_message, dict) else None)
        )

        if visibility == "structural":
            structural_node_count += 1
        else:
            provider_message_count += 1
            if visibility == "hidden":
                hidden_message_count += 1
            elif visibility == "visible":
                visible_message_count += 1
            if knowledge_eligible:
                knowledge_eligible_message_count += 1

        # ------------------------------------------------------------------ #
        # Author / role
        # ------------------------------------------------------------------ #
        author = message.get("author") or {}
        if not isinstance(author, dict):
            author = {}
        raw_role = str(author.get("role") or "unknown")
        role = (
            raw_role
            if raw_role in {"user", "assistant", "system", "developer", "tool"}
            else "unknown"
        )

        # Register participant only for visible messages
        if visibility == "visible":
            participant_id = f"role-{role}"
            visible_participants.setdefault(
                participant_id,
                {
                    "participant_id": participant_id,
                    "role": role,
                    "display_name": author.get("name"),
                    "provider_id": None,
                    "provider_metadata": {},
                },
            )

        # ------------------------------------------------------------------ #
        # Content normalisation
        # ------------------------------------------------------------------ #
        content_parts, discovered = _content_parts(message)
        # For hidden/structural nodes strip the content body so that profile
        # context is not duplicated in derived artifacts (it remains in raw).
        if visibility in {"hidden", "structural"}:
            content_parts = []

        all_attachments.extend(discovered)

        # ------------------------------------------------------------------ #
        # Build the normalised message record
        # ------------------------------------------------------------------ #
        messages[node_id] = {
            "message_id": node_id,
            "provider_message_id": message.get("id"),
            "parent_id": str(parent) if parent else None,
            "children_ids": children,
            "role": role,
            "participant_id": f"role-{role}",
            # --- Classification fields ---
            "visibility": visibility,
            "semantic_type": semantic_type,
            "renderable": renderable,
            "knowledge_eligible": knowledge_eligible,
            "exclusion_reason": exclusion_reason,
            "content_preserved_in_raw": visibility in {"hidden", "structural"},
            # --- Content ---
            "content": content_parts,
            "created_at": _timestamp(message.get("create_time")),
            "updated_at": _timestamp(message.get("update_time")),
            "status": str(message.get("status") or "unknown"),
            "model": (
                message.get("metadata", {}).get("model_slug")
                if isinstance(message.get("metadata"), dict)
                else None
            ),
            "provider_metadata": (
                message.get("metadata")
                if isinstance(message.get("metadata"), dict)
                else {}
            ),
            "redactions": [],
        }
        if not parent:
            roots.append(node_id)

    # ---------------------------------------------------------------------- #
    # Paths
    # ---------------------------------------------------------------------- #
    current_node = conversation.get("current_node")
    provider_selected_path = _selected_path(
        raw_mapping, str(current_node) if current_node else None
    )
    visible_selected_path = [
        mid
        for mid in provider_selected_path
        if messages.get(mid, {}).get("visibility") == "visible"
    ]
    knowledge_selected_path = [
        mid
        for mid in visible_selected_path
        if messages.get(mid, {}).get("knowledge_eligible")
    ]

    branch_index = _compute_branch_index(raw_mapping, roots, provider_selected_path)

    # ---------------------------------------------------------------------- #
    # Attachment warnings
    # ---------------------------------------------------------------------- #
    attachment_warnings: list[str] = [
        f"Attachment not downloaded: {a['attachment_id']} "
        f"(filename={a['filename']!r}, size_bytes={a['size_bytes']})"
        for a in all_attachments
        if a.get("capture_status") == "discovered"
    ]

    return {
        "schema": "mindcap.normalized-conversation/v0.1",
        "source_id": f"chatgpt-{requested_identifier}",
        "capture_version": None,
        "provider": "chatgpt",
        "provider_conversation_id": requested_identifier,
        "canonical_url": envelope.canonical_url,
        "title": conversation.get("title"),
        "created_at": _timestamp(conversation.get("create_time")),
        "updated_at": _timestamp(conversation.get("update_time")),
        "participants": list(visible_participants.values()),
        "root_message_ids": roots,
        "messages": messages,
        "branch_index": branch_index,
        # --- Paths ---
        "provider_selected_path": provider_selected_path or None,
        "visible_selected_path": visible_selected_path or None,
        "knowledge_selected_path": knowledge_selected_path or None,
        # Backward-compat alias kept for existing consumers.
        "selected_path": provider_selected_path or None,
        # --- Counts ---
        "provider_node_count": provider_node_count,
        "provider_message_count": provider_message_count,
        "visible_message_count": visible_message_count,
        "hidden_message_count": hidden_message_count,
        "structural_node_count": structural_node_count,
        "knowledge_eligible_message_count": knowledge_eligible_message_count,
        # --- Attachments ---
        "attachments": all_attachments,
        "attachment_warnings": attachment_warnings,
        # ---
        "integrations": [],
        "redactions": [],
        "provider_metadata": {
            "current_node": current_node,
            "conversation_template_id": conversation.get("conversation_template_id"),
        },
    }
