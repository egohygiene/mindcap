# Troubleshoot Suno audio downloads

If project metadata, lyrics and artwork download but audio returns HTTP 403,
the metadata login worked. The media endpoint denied its own requests. This
does not establish that the stored Clerk cookie is missing or expired.

## Start with the existing archive

Run the offline diagnostic on the exact version directory printed by capture:

```bash
uv run mindcap inspect suno \
  ".cache/mindcap/workspaces/suno/suno-00000000-0000-0000-0000-000000000000/v2" \
  --asset-diagnostics
```

Successful output is JSON containing counts and fixed labels. It excludes
titles, clip/project IDs, hosts, full URLs, query values and authentication
material. It verifies the saved bundle and makes no network requests. This
also works with captures made before the audio-download fix.

| Field | Meaning |
| --- | --- |
| `clips_with_archived_primary_audio` | Clip records listing a saved primary audio asset. Integrity verification separately checks the saved files. |
| `audio_url_kinds.forbidden_endpoint` | Suno supplied an `/api/forbidden` URL instead of a downloadable file. Refreshing a cookie alone does not establish download eligibility. |
| `audio_url_kinds.suno_cdn` | Primary URLs point to recognized Suno media hosts. A 403 may involve download authorization, an expired link, or CDN restrictions. |
| `audio_url_kinds.suno_api` | Primary URLs point to a recognized Suno API host. |
| `audio_url_kinds.other_http` | Primary URLs point elsewhere; no host values are printed. |
| `audio_url_kinds.missing` / `invalid` | Audio URLs were absent or malformed in the saved clip metadata. |
| `audio_urls_with_signature_keys` | URLs contain recognized signature parameter names. Their values are never printed; this count does not establish validity or expiry. |

You can share this successful diagnostic JSON when investigating a failure.
Keep the original metadata JSON, capture archive, cookies, browser network
exports and signed URLs private.

## Check one song in Suno

Open the same song in Suno and use its **More Actions → Download** menu.
If the site's own download is unavailable, inspect the account's download
eligibility or contact Suno support. Playback working does not establish that
file downloads are allowed.

Suno announced download-policy changes effective September 3, 2026, including
plan-based download allowances. See the current
[official FAQ](https://help.suno.com/en/articles/13614785) and
[download instructions](https://help.suno.com/en/articles/2409921). Mindcap's
experimental adapter predates those changes. It currently consumes the media
URLs supplied in clip metadata; it does not implement a separate download
authorization/allowance flow. Do not infer account limits from a 403 alone.

If the site's download succeeds, the diagnostic counts help distinguish an
outdated adapter URL from a failed media request. The client now sends
credential-free requests to external media hosts and preserves any supplied
signed query string. It keeps API authentication scoped to the configured API
origin and removes it when an asset redirect leaves that origin. This fixes
credential scoping; it does not prove that a particular live 403 is resolved.

## Authentication and retry

For metadata-login failures, run:

```bash
uv run mindcap doctor suno
```

This checks stored authentication and authenticated API reachability, not media
download eligibility. Refresh the Clerk cookie through the existing
`mindcap auth suno --cookie-stdin` flow only when authentication needs renewal.
Never paste credentials into an issue or chat.

After updating Mindcap, capture the workspace again. A new capture reads fresh
provider metadata and preserves earlier versions:

```bash
uv run mindcap capture suno "00000000-0000-0000-0000-000000000000"
```

Progress bars count attempts. The final **Audio** row reports downloaded versus
advertised primary audio. Any failed advertised asset makes the capture
partial, even if every clip's metadata was saved. **Archive integrity PASS**
means the saved files passed verification; it does not mean all audio exists.
Do not use that result alone as evidence that the music is fully backed up.

The primary audio format is whatever Suno supplies. The existing `--audio-format`
option changes its saved extension; it does not transcode audio or negotiate a
WAV export. `--request-wav` and `--include-stems` currently do not implement Suno
export requests. Use the site's supported export controls when those formats
are needed.
