# Mindcap

Mindcap is an extensible Python CLI for capturing source material, preserving a
verified archive, and preparing canonical inputs for knowledge extraction and
the Ego Hygiene Mind Garden.

Mindcap currently ships ChatGPT and Suno source plugins. The architecture is
designed for future webpage, PDF, image, repository, document, and media
plugins.

> [!WARNING]
> The private ChatGPT browser strategy is experimental. ChatGPT does not expose
> a documented public API for retrieving one private conversation by URL.
> Mindcap observes the network traffic produced by an authenticated browser and
> isolates that behavior behind a replaceable capture strategy.

## What This Bootstrap Includes

- `mindcap` Typer CLI
- Plugin registry and strategy protocols
- ChatGPT URL and identifier parsing
- Working saved-JSON import strategy
- Experimental Playwright browser strategy
- Dedicated persistent ChatGPT browser profile outside the repository
- Provider-independent capture envelope
- ChatGPT message-tree normalization
- Selected-path Markdown rendering with alternate-response appendix
- Immutable version directories and canonical content hashes
- Manifest, raw index, report, latest pointer, and version history
- Offline fixtures and tests

Knowledge extraction, synapse derivation, attachments, complete redaction,
Gardenization, and Google Drive upload are deliberately outside this bootstrap.

## Installation

Unzip this directory into the Ego Hygiene repository:

```text
~/src/egohygiene/egohygiene/tools/mindcap/
```

From the Mindcap directory:

```bash
cd "$HOME/src/egohygiene/egohygiene/tools/mindcap"
uv sync
uv run playwright install chromium
```

Or if you have [Task](https://taskfile.dev/) installed:

```bash
cd "$HOME/src/egohygiene/egohygiene/tools/mindcap"
task setup
```

Run the offline test suite:

```bash
uv run pytest
uv run ruff check .
```

Or with Task:

```bash
task check
```

## Protect the Artifact Cache

Mindcap defaults to the Ego Hygiene repository root at:

```text
.cache/mindcap/
```

Before capturing private data, verify that the root `.cache/` directory is
ignored:

```bash
cd "$HOME/src/egohygiene/egohygiene"
mkdir -p ".cache/mindcap"
git check-ignore ".cache/mindcap"
```

The final command must print `.cache/mindcap`. If it prints nothing, add this to
the repository root `.gitignore` before continuing:

```gitignore
.cache/
```

## Inspect the CLI

From `tools/mindcap/`:

```bash
uv run mindcap --help
uv run mindcap --version
uv run mindcap plugins list
uv run mindcap paths
```

## Suno Workspace Capture

Mindcap includes an experimental Suno workspace archive flow with a provider
default `api` capture strategy.

Store a Clerk `__client` cookie safely from standard input:

```bash
printf '%s' "${SUNO_CLERK_COOKIE}" | \
  uv run mindcap auth suno --cookie-stdin
```

Inspect safe Suno diagnostics:

```bash
uv run mindcap doctor suno
```

Capture a workspace archive using a placeholder workspace UUID:

```bash
uv run mindcap capture suno "00000000-0000-0000-0000-000000000000"
```

## Prove the Offline Pipeline

Import the included fixture without accessing ChatGPT:

```bash
uv run mindcap import chatgpt \
  "tests/fixtures/chatgpt/branching-conversation.json"
```

Mindcap prints the finalized bundle path. Verify it with:

```bash
uv run mindcap verify "<bundle-path>"
```

Open the generated transcript:

```text
<bundle-path>/normalized/conversation.md
```

## Authenticate the Dedicated ChatGPT Browser

### Why Google Rejects Chrome for Testing

Mindcap previously attempted authentication inside Playwright's bundled
**Chrome for Testing** browser. Google rejects OAuth from that browser with:

> Couldn't sign you in — This browser or app may not be secure.

This message does **not** indicate a problem with your Google account. It is
Google's security policy rejecting a browser that does not meet its trust
requirements. Do not repeatedly retry the rejected flow. Do **not** disable
account security, MFA, browser protections, or Google security settings to work
around this message.

### Authentication Mode vs Capture Mode

Mindcap has two separate modes:

1. **Authentication mode (`mindcap auth chatgpt`)** launches ordinary stable
   Google Chrome directly and requires manual login.
1. **Capture mode (`mindcap capture chatgpt ... --strategy browser`)** launches
   stable Chrome as an external process, enables loopback-only CDP, and lets
   Playwright connect to that process. Playwright does not launch the profile.

Google OAuth must only happen in authentication mode. Google may reject OAuth
inside controlled/automated contexts, so Mindcap never automates OAuth, MFA, or
account verification.

The dedicated profile is stored outside the repository at a path like:

| Platform | Default path |
|----------|-------------|
| macOS    | `~/Library/Application Support/Ego Hygiene/mindcap/browser/chatgpt` |
| Linux    | `~/.local/share/mindcap/browser/chatgpt` (or `$XDG_DATA_HOME`) |
| Windows  | `%APPDATA%\Ego Hygiene\mindcap\browser\chatgpt` |

Run `uv run mindcap paths` (or `task paths`) to display the exact location on
your system.

> [!IMPORTANT]
> The dedicated profile contains sensitive authentication state (cookies and
> session tokens). It must **never** be committed to git, placed in
> `.cache/mindcap/`, archived, or uploaded to any service.
> Do not copy your everyday Chrome profile into Mindcap's profile path.

### Run Authentication

```bash
cd tools/mindcap
task auth
```

Without Task:

```bash
uv run mindcap auth chatgpt
```

Mindcap will:

1. Locate stable Google Chrome on your system.
1. Launch Chrome **directly** (no Playwright automation, no remote-debugging
   flags, no anti-detection tricks).
1. Open `https://chatgpt.com/` in the dedicated profile.
1. Display a prompt asking you to log in.

Confirm the Chrome process and profile are correct from `chrome://version`:

- **Executable Path** should point to stable Google Chrome.
- **Command Line** should include your dedicated `--user-data-dir`.
- **Profile Path** should end in `/Default` for the dedicated profile.

Complete these steps in the Chrome window:

1. Sign into ChatGPT using Google or your OpenAI credentials.
1. Confirm that your conversation history is visible.
1. **Fully quit Chrome** — on macOS use **Cmd+Q**, on Windows and Linux use
   **File → Exit** or close all windows and confirm the process has stopped.
1. Return to the terminal and press **Enter**.

> [!IMPORTANT]
> Chrome **must be fully quit** before you press Enter. If Chrome is still
> running, the profile will be locked and capture will fail.

After authentication, capture uses Playwright with the **stable Chrome channel**
via an external Chrome + CDP bridge so cookies written by stable Chrome remain
readable. Playwright's bundled Chrome for Testing is never used for
authentication.

### Complete Workflow

With Task:

```bash
cd tools/mindcap
task setup
task paths
task doctor
task auth
task doctor
task capture -- "https://chatgpt.com/c/6a14b69f-7834-83ea-8257-0eceadb41691"
task verify -- "<bundle-path>"
task check
```

Without Task:

```bash
uv sync
uv run mindcap paths
uv run mindcap doctor chatgpt
uv run mindcap auth chatgpt
uv run mindcap doctor chatgpt
uv run mindcap capture chatgpt \
  "https://chatgpt.com/c/6a14b69f-7834-83ea-8257-0eceadb41691" \
  --strategy "browser"
uv run mindcap verify "<bundle-path>"
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

## Attempt the Acceptance Conversation

```bash
uv run mindcap capture chatgpt \
  "https://chatgpt.com/c/6a14b69f-7834-83ea-8257-0eceadb41691" \
  --strategy "browser"
```

The browser strategy:

1. Launches stable Chrome with the dedicated profile and loopback-only CDP.
1. Registers response listeners before navigation.
1. Creates a fresh capture tab and navigates to the requested conversation.
1. Collects JSON responses without persisting headers or cookies.
1. Scores candidates for conversation-shaped data.
1. Archives and normalizes the highest-confidence payload.

If ChatGPT's current frontend does not expose a recognizable JSON response, the
command fails with a diagnostic report instead of scraping an incomplete DOM
and calling it a complete capture.

Increase the observation window if the chat is slow to load:

```bash
uv run mindcap capture chatgpt \
  "https://chatgpt.com/c/6a14b69f-7834-83ea-8257-0eceadb41691" \
  --strategy "browser" \
  --wait-seconds 20
```

## Troubleshooting

### Google rejects the login browser

If you see "This browser or app may not be secure", the authentication flow is
running inside Chrome for Testing instead of stable Chrome. Make sure you are
running `mindcap auth chatgpt` with the current version of Mindcap. Do not
attempt to work around Google's security check by disabling MFA or security
settings.

### Chrome is not installed

Mindcap requires a normally installed stable Google Chrome for authentication.
Install it from <https://www.google.com/chrome/> and retry.

If Chrome is installed in a non-standard location, set the environment variable:

```bash
export MINDCAP_CHROME_EXECUTABLE="/path/to/google-chrome"
```

### The profile is locked

If Mindcap reports that the dedicated profile is locked, Chrome is still
running. Fully quit the dedicated Chrome process and retry. Do not delete the
lock file while Chrome is running — doing so can corrupt the profile.

Run diagnostics:

```bash
task doctor
# or
uv run mindcap doctor chatgpt --verbose
```

### The ChatGPT session expired

Authentication cookies have a limited lifetime. When capture redirects to a
login page, re-authenticate:

```bash
task auth
```

### Browser capture says authentication is required

Confirm that both the authentication command and capture command are using the
same dedicated profile path. Run `task paths` to display both locations.

Then fully quit dedicated Chrome and rerun:

```bash
task auth
task doctor
```

### Conversation capture times out

If the conversation is slow to load, increase the observation window:

```bash
uv run mindcap capture chatgpt "<url>" --strategy "browser" --wait-seconds 30
```

### Display archive and profile paths

```bash
task paths
# or
uv run mindcap paths
```

### Verify session persistence after capture

After a successful capture, relaunch dedicated Chrome manually with the same
`--user-data-dir` and confirm ChatGPT is still authenticated. Mindcap capture
should not invalidate the dedicated profile.

## Reset the Dedicated Authentication Profile

To start over with a fresh, unauthenticated profile, delete the dedicated
profile directory. Run `task paths` (or `uv run mindcap paths`) first to
confirm the exact path, then delete it manually:

```bash
# Step 1 — display the profile path:
uv run mindcap paths

# Step 2 — delete the path shown in the "ChatGPT browser profile" row:
# macOS / Linux example (replace with the actual path printed above):
rm -rf "$HOME/Library/Application Support/Ego Hygiene/mindcap/browser/chatgpt"

# Linux example:
# rm -rf "$HOME/.local/share/mindcap/browser/chatgpt"
```

> [!WARNING]
> Always verify the path shown by `mindcap paths` before deleting. Never
> run `rm -rf` with unverified shell expansions.

After deleting the profile, run `task auth` again to create a fresh one.

## Current CLI

```text
mindcap auth chatgpt
mindcap doctor chatgpt
mindcap capture chatgpt <url-or-id>
mindcap import chatgpt <json-file>
mindcap verify <bundle-path>
mindcap plugins list
mindcap paths
mindcap version
```

## Architecture

```text
CaptureRequest
    -> PluginRegistry
    -> ChatGPTPlugin
    -> CaptureStrategy
       ├── BrowserCaptureStrategy
       └── SavedJsonCaptureStrategy
    -> ChatGPTNormalizer
    -> ChatGPTMarkdownRenderer
    -> FilesystemStorageStrategy
    -> Verified Artifact Bundle
```

Provider-specific behavior belongs under `src/mindcap/plugins/`. Core modules
must not import ChatGPT code.

## Security Boundaries

- Authentication state lives outside the repository and artifact cache.
- Authentication uses stable Chrome directly — no Playwright automation during
  the auth flow.
- Capture uses Playwright with `channel="chrome"` to remain compatible with the
  auth state.
- Capture bundles never include request headers, cookies, or browser storage.
- Raw provider payloads may still contain sensitive conversation content and
  expiring asset references.
- `.cache/mindcap/` must remain ignored and private.
- Do not share an unreviewed HAR file; HAR files commonly include request
  headers and cookies.
- Treat every generated bundle as `sensitive` by default.

## Known Limitations

- ChatGPT capture depends on undocumented frontend network behavior.
- Browser candidate detection is intentionally conservative.
- Attachments are recorded only when present in the captured payload; binary
  download is not implemented.
- Security redaction and redaction ledgers are not implemented yet.
- Normalization targets the common ChatGPT `mapping` representation.
- Knowledge extraction, synapses, and Gardenization are not implemented.
- Version-history writes are local-filesystem oriented and do not yet provide
  cross-process locking.
- `task capture` requires stable Google Chrome to be installed on the host.

## Next Development Milestone

Use the repository specifications as authoritative inputs:

- `../../.github/specs/source-capture.spec.md`
- `../../.github/specs/knowledge-extract.spec.md`
- `../../.github/specs/gardenize.spec.md`

The next milestone should harden live ChatGPT response discovery against the
acceptance conversation, add sanitized diagnostics, complete source-capture
contract coverage, and preserve attachment metadata without leaking temporary
credentials.
