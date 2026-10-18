# Terminal output and diagnostics

Mindcap displays timestamped, colored severity labels on an interactive
terminal. Phase progress, warnings, retries, and errors go to **stderr**; the
final result or JSON goes to **stdout**. Redirected output uses plain text with
bounded phase start/end lines instead of animated progress. Rich respects
`NO_COLOR=1` for terminals that should not use color.

## Everyday capture

The normal command already includes progress and warning summaries:

```bash
uv run mindcap capture suno "<workspace-id>"
```

Repeated asset failures are collapsed into a representative warning and a
grouped count. `--verbose` retains individual asset warnings and any per-item
detail supplied by the adapter. A finished progress phase counts **attempts**;
the final capture summary reports assets actually downloaded, capture
completeness, and saved-file integrity separately.

```bash
uv run mindcap capture suno "<workspace-id>" --verbose
uv run mindcap capture suno "<workspace-id>" --quiet
uv run mindcap capture suno "<workspace-id>" --json
```

Quiet mode retains warnings and errors. Capture prints only its archive path;
`--json` takes precedence for the final result, while quiet still suppresses
progress. Source adapters determine which phase and per-item details exist.

## Investigate a failure

Errors show a stable code, a sanitized description, and a next step. Debug mode
adds diagnostic events and exception frame locations, without dumping source
lines or local variables. Authentication, browser locks, invalid input, archive
verification, and filesystem failures have distinct codes.

Global diagnostic options go **before** the command name. Existing capture and
sync `--verbose`, `--debug`, and `--quiet` options still work after the command.

```bash
uv run mindcap --debug capture suno "<workspace-id>"
uv run mindcap --verbose doctor suno
uv run mindcap --debug --log-file "$HOME/Downloads/mindcap-debug.jsonl" \
  capture suno "<workspace-id>"
```

The optional log contains newline-delimited JSON records with UTC timestamps,
severity, logger, and sanitized messages. It appends to a private file with
owner-only permissions, in an existing folder. Log setup errors stop the
command before capture begins; a write failure during capture emits a warning
and lets capture continue. No file is created unless `--log-file` is supplied.
Debug and quiet control the file's verbosity too; quiet takes precedence.

Diagnostics strip common credential fields, cookies, authorization headers,
JWTs, URL credentials, URL queries/fragments, and terminal control sequences.
Request/response bodies and browser storage state are never diagnostic input.
Logs can still contain source identifiers, titles, hosts, and local paths;
inspect a log before sharing it.

## Results and exit codes

| Exit | Meaning |
|---|---|
| `0` | Command succeeded. |
| `1` | Operation failed, including an unexpected error or unavailable log file. |
| `2` | Partial capture/batch result, or command usage error. |
| `130` | Interrupted. Sync prints its checkpoint/resume instructions. |

A partial single-source capture still prints its saved archive or JSON result
before exiting `2`. Archive integrity can pass while required assets are
missing. Inspect the warnings before retrying; a media `403` alone does not
establish that a login cookie is missing. See [Suno audio](suno-audio.md).

Commands that support `--json` emit their success result on stdout. A failure
emits a structured error envelope on stderr, with `code`, `type`, `message`,
and `hint`; debug mode can add sanitized frame locations. Stderr may also
contain preceding phase diagnostics, so consume stdout for result data and use
the exit status to determine success. Argument conversion and missing-argument
errors also honor a recognized JSON option; unrecognized-option errors may
use a text diagnostic.

## Library callers

Importing Mindcap does not configure the root logger or HTTP client loggers.
CLI handlers are installed only for one command and removed on exit. Embedding
applications can configure their own `mindcap` logging namespace. The progress
reporter remains silent when no console is attached; structured application
events remain a separate library contract.
