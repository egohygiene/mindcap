# Recent Chrome bookmark URLs

Mindcap already captures Chrome bookmarks through `ChromeBookmarksPlugin` and
the existing filesystem `CaptureStrategy`, preserving raw profile snapshots and
all normalized bookmark occurrences. This small provider-owned library view
adds the missing recent-only URL projection from [issue #43](https://github.com/egohygiene/mindcap/issues/43).
It does not add another provider, discover profiles, read browsing History,
fetch URLs, modify bookmarks, or change canonical capture/storage behavior.

## Use the existing normalization seam

Pass the document returned by `ChromeBookmarksPlugin.normalize(envelope,
canonical_identifier)` to the projection. Keep the normal capture, persistence
and verification steps unchanged:

```python
from datetime import UTC, datetime

from mindcap.plugins.chrome_bookmarks.recent import project_recent_bookmark_urls

# normalized_capture is the existing plugin's normalized document.
view = project_recent_bookmark_urls(
    normalized_capture,
    as_of=datetime(2026, 11, 14, 12, tzinfo=UTC),
)
private_text = view.text()
```

The result is a frozen `RecentBookmarkURLs` value. `urls` is a tuple, and
`text()` returns one exact URL per line with a final newline, or an empty
string. The function performs no I/O. The caller chooses whether and where to
store this private derived text; it does not overwrite a source or output file.
URLs are omitted from the result's default representation and from errors.

This is a library seam for an already normalized capture, not a new saved-bundle
loader or a new CLI command. Review and verification of an externally supplied
bundle remain the existing capture consumer's responsibility.

## Exact selection rules

- `as_of` must be explicitly supplied and timezone-aware. It is normalized to
  UTC; a naive clock is rejected. No implicit wall clock or capture clock is
  substituted.
- The window is the inclusive rolling 168 hours from `as_of - 7 days` through
  `as_of`, using bookmark creation time (`date_added`). Both boundaries are
  included; future timestamps are excluded.
- The existing parser owns conversion from Chrome's microseconds-since-1601
  epoch. This view consumes its `parsed` normalized timestamp and does not
  convert the raw timestamp again. Non-parsed, missing, invalid and naive dates
  are counted as unknown and omitted from this view.
- Every bookmark record in the selected canonical capture is considered, across
  its captured profiles and roots. The current parser supplies `bookmark_bar`,
  `other`, and `synced` in capture order. This view does not discover additional
  profiles/roots or establish that capture coverage is complete.
- Filter by time before deduplicating. An older occurrence cannot suppress a
  qualifying later occurrence of the same URL. Keep the first qualifying
  occurrence in source order, deduplicating exact strings across profiles/roots.
- Do not rewrite case, fragments, query parameters, escaping or trailing slashes.
  Semantically distinct source strings remain distinct. No URL is visited or
  classified. Non-HTTP schemes remain eligible.
- Empty/whitespace-only/non-string URLs, control characters, Unicode line/paragraph separators
  and unpaired surrogates are excluded so each selected URL remains one UTF-8
  text line. This is an output representation check, not a URL safety verdict.

`source_count`, `duplicate_count`, `older_count`, `future_count`,
`unknown_date_count` and `invalid_url_count` make omissions explicit. Counts are
disjoint: their sum plus `len(urls)` equals `source_count`. Invalid source shapes
or unsupported normalized schema/provider identities fail rather than producing
an apparently valid empty view. Original records, warnings and capture bytes
remain unchanged; a view never upgrades a partial capture to complete.
The reference clock filters the supplied snapshot; it does not reconstruct
bookmarks that were absent or deleted before that capture.

## Private view and pending review batches

Exact source URLs can themselves contain private information or credentials.
This text is private derived data, not share-ready output or permission to
publish. Do not print it in ordinary logs or commit real captures as fixtures.

[PR #37](https://github.com/egohygiene/mindcap/pull/37), inspected at
`d568285c3089f690b345029ed88eeda749327b99`, separately owns capture/review commands,
saved-bundle hardening and screened sharing batches. Those batches deliberately
preserve duplicate occurrences and may transform URLs; this projection keeps
exact strings and deduplicates only its own view. No pending PR code is imported
or merged here. Future composition must keep those distinct purposes explicit.
Incremental snapshots and provider-neutral handoff remain under
[issue #35](https://github.com/egohygiene/mindcap/issues/35) and its existing
contracts; this view is not a deletion, synchronization or gardenization plan.

## Archived prototype disposition

The original [bookmarks.py](https://github.com/egohygiene/mindcap/blob/b67fddb68a43a8c2bb6fab747273e45a59e393f3/docs/source-intake/2026-11-13/bookmarks.py)
is retained unchanged at the immutable intake revision, with its
[archive manifest](https://github.com/egohygiene/mindcap/blob/b67fddb68a43a8c2bb6fab747273e45a59e393f3/docs/source-intake/2026-11-13/manifest.json).
It was read as reference, not executed, installed or copied into product code.

- Bytes: `1953`
- SHA-256: `cda4a331835eb1adab6e004d26558b9ab0ec7802ab32bcdb11bb912a23de668f`
- Git blob: `6d49f01feebac7b7c1d74c32dde8ce502bad87e0`

| Prototype behavior | Disposition |
| --- | --- |
| macOS Default-profile path | Existing cross-platform profile selection remains the capture owner. |
| Recursive `bookmark_bar` and `other` traversal | Existing normalizer also retains `synced` and all selected profiles. |
| Chrome epoch conversion | Reuse existing UTC normalization. |
| Seven days from a naive local clock | Use an explicit aware clock and inclusive bounded UTC window. |
| `set()` of URLs | Deterministic first-qualifying exact URL deduplication in the derived view only. |
| Overwrite `chrome_recent_bookmarks.txt` | Return typed data/text; caller owns explicit private output policy. |

## Offline validation

```sh
uv sync --locked --group dev
uv run --locked pytest tests/test_recent_chrome_bookmarks.py tests/test_chrome_bookmarks.py tests/test_source_bundle.py tests/architecture -q
uv run --locked ruff check src/mindcap/plugins/chrome_bookmarks/recent.py tests/test_recent_chrome_bookmarks.py
uv run --locked ruff format --check src/mindcap/plugins/chrome_bookmarks/recent.py tests/test_recent_chrome_bookmarks.py
```

Tests use synthetic fixtures only, including the existing capture/persist/verify
pipeline and unchanged raw/normalized hashes. No real browser profile,
authentication material, URL visit, provider operation or sharing workflow is
qualified by these tests.
