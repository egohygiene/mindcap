from __future__ import annotations


def render_suno_markdown(normalized: dict[str, object]) -> str:
    title = str(normalized.get("title") or "Untitled Suno Workspace")
    workspace_id = str(normalized["workspace_id"])
    clips = list(normalized.get("clips") or [])
    warnings = list(normalized.get("warnings") or [])

    lines = [
        "---",
        f'title: "{title.replace(chr(34), chr(92) + chr(34))}"',
        f'workspace_id: "{workspace_id}"',
        'provider: "suno"',
        "---",
        "",
        f"# {title}",
        "",
        f"- Workspace ID: `{workspace_id}`",
        f"- Clip count: {len(clips)}",
        f"- Capture completeness: {normalized.get('capture_completeness')}",
        "",
        "## Clips",
        "",
    ]
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        lines.extend(
            [
                f"### {clip.get('title') or clip.get('clip_id')}",
                "",
                f"- Clip ID: `{clip.get('clip_id')}`",
                f"- Status: {clip.get('status') or 'unknown'}",
                f"- Model: {clip.get('model') or 'unknown'}",
                f"- Assets archived: {len(clip.get('archived_assets') or [])}",
                "",
            ]
        )
    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            lines.append(f"- ⚠ {warning}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
