# Review Chrome bookmarks in small batches

Mindcap can copy Chrome's bookmark files into a verified local archive, then
prepare small Markdown batches to review with an assistant. Run the commands
on the computer where Google Chrome and your bookmarks are stored.

This workflow reads your existing bookmark files. It does not open Chrome,
authenticate, visit bookmark URLs, modify Chrome, delete duplicates, or decide
which resources belong in Akashic. Reviewing the batches is the next step.

## Start from a checkout

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed,
then run:

```bash
git clone "https://github.com/egohygiene/mindcap.git"
cd "mindcap"
uv sync --locked
uv run mindcap bookmarks prepare
```

If you already have this repository, start from its directory and run the last
two commands. A Playwright browser download, browser login, API key, and
background service are unnecessary for bookmark capture.

The command discovers readable profiles in the stable Chrome channel,
snapshots their bookmark files, verifies the private archive, and creates a
fresh review set under `~/Downloads/mindcap-bookmarks`. It prints the archive
path, review directory, counts, and the first batch file to open.

This is a snapshot of locally available bookmarks. It does not pull directly
from Google Sync, include every Chrome release channel, or guarantee that an
unreadable profile was captured. Make sure Chrome has finished syncing the
bookmarks you expect before capturing. If Chrome is actively changing the
bookmark file and a stable snapshot cannot be made, quit Chrome and retry.

## Inspect and share one batch

The workspace contains two kinds of output:

| Output | Purpose |
| --- | --- |
| `archives/` | Private immutable captures, including original bookmark files, normalized records, and verification metadata. |
| A fresh `review-…/` directory | `batch-0001.md`, subsequent numbered batches, `README.md`, `manifest.json`, and `withheld-local.json`. |

The review directory's README explains the counts and order. Open
`batch-0001.md` in your editor and inspect it before sharing. Attach
that file to the conversation and ask for a source and duplicate review for
Akashic. After reviewing it, move to the next numbered batch and keep track of
the last completed batch locally. There is no automatic upload or review
progress tracking.

Only share the individual batch files you have inspected. Keep the original
archive, manifests, and withheld indices local. A batch omits profile names,
folder paths, timestamps, and local filesystem paths, but its bookmark titles
and public-looking URLs can still reveal sensitive information.

By default, a batch contains at most 50 bookmark occurrences and approximately
20,000 characters. Long entries may make a batch smaller. Duplicate occurrences
remain visible for review; they are not automatically removed, merged, or
reorganized. The original capture preserves the source data independently of
the review projection.

The projection withholds unsupported schemes, local/private hosts,
credential-bearing URLs, and other unsafe URL forms. It removes known tracking
parameters from included URLs. These rules do not establish that a link is
alive, useful, safe to visit, or appropriate to publish. Links and titles are
untrusted source material for human-assisted review.

## Choose a workspace and batch size

```bash
uv run mindcap bookmarks prepare \
  --output "$HOME/Downloads/mindcap-bookmarks" \
  --batch-size 25
```

`--output` chooses the workspace containing the private archive and the new
review set. Each preparation creates a fresh review directory so an earlier
set remains available. Do not commit this workspace to a public repository.
`--batch-size` accepts values from 1 to 200; the text-size limit still applies.

An empty capture produces zero batch files and an explicit summary. Check the
captured profile count and any warnings before assuming it reflects all your
bookmarks.

## Choose Chrome profiles or a different channel

Without a profile selector, Mindcap captures every readable profile found in
the chosen Chrome channel. Select one or more profile **directory names** with
repeated `--profile` options:

```bash
uv run mindcap bookmarks prepare --profile "Default" --profile "Profile 1"
```

Those values are directories such as `Default` and `Profile 1`, rather than
the display names shown in Chrome's profile picker. In the intended profile,
open `chrome://version` and inspect **Profile Path** to find the directory name
and its parent user-data directory. On macOS a typical profile path is:

```text
/Users/your-name/Library/Application Support/Google/Chrome/Default
```

For an unusual Chrome location, pass the **parent** user-data directory rather
than the `Default` directory or the `Bookmarks` file:

```bash
uv run mindcap bookmarks prepare \
  --user-data-dir "$HOME/Library/Application Support/Google/Chrome" \
  --profile "Default"
```

Repeat `--user-data-dir` to capture multiple roots in one run. Profile selectors
apply to the requested directory names across those roots.

To inspect a different installed Chrome channel, choose `stable`, `beta`,
`dev`, or `canary`:

```bash
uv run mindcap bookmarks prepare --channel beta
```

Each run uses one channel. The automatic locations follow macOS, Windows, and
Linux conventions; `--user-data-dir` overrides that discovery.

## Make new batches from a saved capture

Use the exact archive bundle path printed by `prepare` to change the batch
size later without rereading Chrome:

```bash
uv run mindcap bookmarks batches \
  "/path/printed/by/prepare/archives/chrome-bookmarks/chrome-bookmarks-local/v1" \
  --batch-size 25 \
  --output "$HOME/Downloads/mindcap-bookmarks/review-smaller"
```

The saved bundle is verified before reading its normalized bookmark records.
Here, `--output` names the new review directory itself; choose a directory that
does not already exist. If omitted, Mindcap creates a fresh review set in the
default Downloads workspace. Rebatching never edits the saved capture.

## Troubleshooting

**No profiles found:** Run on the machine with the bookmarks. Check
`chrome://version` in the intended Chrome profile and pass its parent
user-data directory with `--user-data-dir`. Check `--channel` if using Chrome
Beta, Dev, or Canary. A profile needs a readable `Bookmarks` file.

**A selected profile is missing:** Use its directory name, not its display
name. Confirm the profile is under the selected user-data directory.

**Capture fails:** Quit Chrome if it is repeatedly changing the file, then
retry. Mindcap can use a valid `Bookmarks.bak` when the primary JSON is invalid
and will report that fallback. An invalid or unreadable selected source can
still fail the operation; no complete review set should be inferred from a
failed command. Existing captures remain available.

**Expected links are absent:** Read the summary and local withheld index, and
check which profiles and channel were captured. Some links are excluded from
the review projection while remaining in the private archive. Mindcap does not
test page availability or resolve redirects during this step.

**Scripting:** Both commands accept `--json` for a machine-readable result or
`--quiet` for the result path. Use one output mode per invocation. Paths in a
JSON result are local filesystem locations, not uploaded files.

## Scope after the trial

These batches support a manual capture → inspect → share → review workflow.
Catalog matching, duplicate judgments, broken-link investigation, placement,
and Akashic PRs happen during curation. No Akashic installation or integration
is required to try the capture workflow.

The broader incremental handoff and review lifecycle remains separate work in
[#35](https://github.com/egohygiene/mindcap/issues/35). Generating batches does
not implement or close that issue.
