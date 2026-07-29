from __future__ import annotations

import json
from typing import Any


def _render_part(part: dict[str, Any]) -> str:
    """Render a single normalised content part to Markdown text."""
    part_type = str(part.get("type") or "")
    value = part.get("value")

    if part_type == "attachment_reference":
        attachment_id = part.get("attachment_id")
        mime_type = part.get("mime_type") or ""
        archive_path = part.get("archive_path")
        # Prefer the explicit filename; fall back to provider_metadata then ID.
        filename = part.get("filename")
        if not filename:
            meta = part.get("provider_metadata") or {}
            if isinstance(meta, dict):
                filename = meta.get("name") or meta.get("filename")
        if not filename and attachment_id:
            ext = mime_type.split("/")[-1] if "/" in mime_type else "bin"
            filename = f"{attachment_id}.{ext}"
        if archive_path:
            return f"![{filename}]({archive_path})"
        # Attachment was discovered but not downloaded.
        label = filename or attachment_id or "attachment"
        return f"_[Attachment unavailable: {label}]_"

    if part_type == "text":
        return str(value) if value is not None else ""

    if isinstance(value, str):
        return value

    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def _render_message(message: dict[str, Any]) -> str:
    role = str(message.get("role") or "unknown").replace("_", " ").title()
    message_id = message.get("message_id")
    lines = [f"## {role}", "", f"<!-- message-id: {message_id} -->", ""]
    parts = message.get("content") or []
    if not parts:
        lines.append("_[No renderable message content]_\n")
    else:
        for part in parts:
            rendered_part = _render_part(part)
            if rendered_part:
                lines.append(rendered_part)
        lines.append("")
    return "\n".join(lines)


def render_chatgpt_markdown(normalized: dict[str, Any]) -> str:
    title = normalized.get("title") or "Untitled ChatGPT Conversation"
    source_id = normalized["source_id"]
    messages = normalized["messages"]
    # Prefer the explicit visible path; fall back to the legacy selected_path.
    visible_path = normalized.get("visible_selected_path") or normalized.get(
        "selected_path"
    ) or []

    lines = [
        "---",
        f'title: "{str(title).replace(chr(34), chr(92) + chr(34))}"',
        f'source_id: "{source_id}"',
        'provider: "chatgpt"',
        'sensitivity: "sensitive"',
        "---",
        "",
        f"# {title}",
        "",
        "> Captured by Mindcap. The main body follows the provider-selected path.",
        "",
    ]

    rendered: set[str] = set()
    for message_id in visible_path:
        message = messages.get(message_id)
        if message and message.get("renderable") is not False:
            parts = message.get("content") or []
            # Skip messages with no renderable content (e.g. empty strings).
            has_content = any(
                (p.get("value") or p.get("attachment_id")) for p in parts
            )
            if has_content or not parts:
                lines.append(_render_message(message))
                rendered.add(message_id)

    # Show visible messages that were not on the selected path as alternates.
    alternates = [
        message
        for message_id, message in messages.items()
        if message_id not in rendered
        and message.get("visibility") == "visible"
        and message.get("renderable") is not False
        and message.get("content")
    ]
    if alternates:
        lines.extend(["# Alternate and Unselected Messages", ""])
        for message in alternates:
            parent = message.get("parent_id") or "none"
            lines.extend(
                [
                    f"> Parent message: `{parent}`",
                    "",
                    _render_message(message),
                ]
            )

    # Coverage warnings for undiscovered / missing attachments.
    warnings = normalized.get("attachment_warnings") or []
    if warnings:
        lines.extend(["", "---", "", "## Coverage Warnings", ""])
        for warning in warnings:
            lines.append(f"- ⚠ {warning}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
