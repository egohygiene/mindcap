# Mindcap CLI Architecture

The `mindcap` command-line interface lives in the `mindcap_cli` Python
package.  It is a thin adapter that:

1. Parses arguments with [Typer](https://typer.tiangolo.com/)
2. Constructs typed command objects from the `mindcap.contracts` module
3. Calls the `MindcapApplication` facade from the `mindcap` library
4. Renders results using [Rich](https://rich.readthedocs.io/)
5. Maps exceptions to documented exit codes

---

## CLI as an adapter

```
┌─────────────────────────────────────────┐
│               mindcap_cli               │
│                                         │
│  parse args → CaptureCommand            │
│       ↓                                 │
│  MindcapApplication.capture()           │
│       ↓                                 │
│  render CaptureResult with Rich         │
│       ↓                                 │
│  map exception → exit code              │
└─────────────────────────────────────────┘
          ↓ imports (one direction only)
┌─────────────────────────────────────────┐
│                mindcap                  │
│                                         │
│  MindcapApplication                     │
│  contracts (commands, results, events)  │
│  application services                   │
│  plugins                                │
│  storage                                │
│  sync                                   │
└─────────────────────────────────────────┘
```

The `mindcap` library must never import `mindcap_cli`.  The architecture
boundary tests in `tests/architecture/test_boundaries.py` enforce this.

---

## Application facade usage

CLI commands call the public `MindcapApplication` API:

```python
from mindcap import MindcapApplication
from mindcap.contracts import CaptureCommand

application = MindcapApplication.default()
result = application.capture(CaptureCommand(...))
```

The CLI does not call provider-specific methods directly:

```python
# NOT THIS — CLI must not do this
plugin = registry.get("suno")
strategy = plugin.strategy("api", reporter=reporter)
envelope = strategy.capture(request)
```

---

## Command organization

```
mindcap_cli/
└── app.py          Typer application and all command registrations
```

The current phase places all command implementations in `app.py`.  A future
phase may split them into `commands/` submodules.

---

## Progress rendering

The CLI constructs a `CaptureProgressReporter` backed by a Rich `Console`.
Provider strategies accept a reporter so they can emit phase labels and
progress bars during capture.

Progress rendering is a CLI concern.  The library emits structured
`MindcapEvent` objects through the `EventSink` protocol instead.

---

## Exception mapping

| Exception                    | Exit code |
|------------------------------|-----------|
| Success                      | 0         |
| `MindcapError`, `OSError`    | 1         |
| Partial batch failure        | 2         |
| Interrupted (SIGINT)         | 130       |

---

## JSON output

Pass `--json` to any command to receive machine-readable output on stdout:

```bash
mindcap capture chatgpt "..." --json
mindcap sync suno --json
```

JSON output is derived from serialized result models:

```python
result = application.capture(...)
console.print_json(data={"status": result.status, ...})
```

Progress is suppressed or redirected to stderr when `--json` is active.

---

## stdout vs stderr rules

- **stdout**: final result (path, JSON, or human-readable summary)
- **stderr**: progress, warnings, errors (via Rich `Console`)
- When `--quiet` is active, only the final path or JSON is printed

---

## Shell completion

```bash
mindcap --install-completion
```

---

## Compatibility guarantees

The following commands are stable:

```bash
mindcap --help
mindcap --version
mindcap capture <provider> <source> [options]
mindcap import <provider> <path> [options]
mindcap sync <provider> [options]
mindcap verify <bundle-path>
mindcap inspect <provider> <archive>
mindcap auth <provider>
mindcap doctor <provider>
mindcap plugins list
mindcap paths
```

The old import `from mindcap.cli import app` is preserved as a
backward-compatibility shim pointing to `mindcap_cli.app:app`.

---

## Future API compatibility

The same `MindcapApplication` facade used by the CLI will be used by the
future FastAPI server:

```
mindcap_cli ─┐
             ├──> MindcapApplication
mindcap_api ─┘
```

This ensures that the library behavior is consistent across all consumers
without duplicating orchestration logic.
