from __future__ import annotations


def render_soundcloud_markdown(normalized: dict[str, object]) -> str:
    """Render a normalized SoundCloud capture as Markdown."""
    source_type = str(normalized.get("source_type") or "unknown")
    source_id = str(normalized.get("source_id") or "")
    warnings_value = normalized.get("warnings")
    warnings = warnings_value if isinstance(warnings_value, list) else []

    lines = [
        "---",
        f'source_id: "{source_id}"',
        'provider: "soundcloud"',
        f'source_type: "{source_type}"',
        "---",
        "",
    ]

    if source_type == "track":
        track = normalized.get("track")
        if isinstance(track, dict):
            title = str(track.get("title") or track.get("permalink") or source_id)
            lines += [
                f"# {title}",
                "",
                f"- Track ID: `{track.get('track_id') or '-'}`",
                f"- Permalink: {track.get('permalink_url') or '-'}",
                f"- ISRC: {track.get('isrc') or '-'}",
                f"- Visibility: {track.get('sharing') or '-'}",
                f"- Duration: {track.get('duration_ms') or '-'} ms",
                f"- Genre: {track.get('genre') or '-'}",
                f"- Plays: {track.get('playback_count') or '-'}",
                f"- Likes: {track.get('likes_count') or '-'}",
                "",
            ]
        else:
            lines += [f"# SoundCloud Track {source_id}", ""]

    elif source_type == "playlist":
        playlist = normalized.get("playlist")
        if isinstance(playlist, dict):
            title = str(playlist.get("title") or playlist.get("permalink") or source_id)
            track_ids = playlist.get("track_ids")
            track_count = (
                len(track_ids)
                if isinstance(track_ids, list)
                else playlist.get("track_count")
            )
            lines += [
                f"# {title}",
                "",
                f"- Playlist ID: `{playlist.get('playlist_id') or '-'}`",
                f"- Permalink: {playlist.get('permalink_url') or '-'}",
                f"- Type: {'album' if playlist.get('is_album') else 'playlist'}",
                f"- Visibility: {playlist.get('sharing') or '-'}",
                f"- Tracks: {track_count or '-'}",
                "",
            ]
        else:
            lines += [f"# SoundCloud Playlist {source_id}", ""]

    elif source_type == "account":
        account = normalized.get("account")
        tracks_value = normalized.get("tracks")
        tracks = tracks_value if isinstance(tracks_value, list) else []
        playlists_value = normalized.get("playlists")
        playlists = playlists_value if isinstance(playlists_value, list) else []
        if isinstance(account, dict):
            display_name = str(
                account.get("display_name") or account.get("username") or source_id
            )
            lines += [
                f"# {display_name}",
                "",
                f"- User ID: `{account.get('user_id') or '-'}`",
                f"- Permalink: {account.get('permalink_url') or '-'}",
                f"- Verified: {account.get('verified') or '-'}",
                f"- Tracks: {account.get('track_count') or '-'}",
                f"- Followers: {account.get('followers_count') or '-'}",
                "",
            ]
        else:
            lines += [f"# SoundCloud Account {source_id}", ""]

        lines += [
            "## Captured Tracks",
            "",
            f"Tracks captured: {len(tracks)}",
            "",
            "## Captured Playlists",
            "",
            f"Playlists captured: {len(playlists)}",
            "",
        ]
    else:
        lines += [f"# SoundCloud Capture {source_id}", ""]

    if warnings:
        lines += ["## Warnings", ""]
        for w in warnings:
            lines.append(f"- ⚠ {w}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
