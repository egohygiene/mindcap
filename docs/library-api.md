# Mindcap Library API

The `mindcap` package is a reusable Python library for capturing, normalizing,
and managing source material for knowledge extraction.  It is independent of
Typer, Rich, and all terminal-specific behavior.

---

## Installation

```bash
pip install egohygiene-mindcap
```

Or with Poetry:

```bash
poetry add egohygiene-mindcap
```

---

## Quick start

```python
from mindcap import MindcapApplication
from mindcap.contracts import CaptureCommand

app = MindcapApplication.default()
```

---

## Listing providers

```python
from mindcap import MindcapApplication

app = MindcapApplication.default()
result = app.list_plugins()

for plugin in result.plugins:
    print(plugin.source_type, plugin.strategies)
```

---

## Capturing one source

```python
from mindcap import MindcapApplication
from mindcap.contracts import CaptureCommand

app = MindcapApplication.default()

result = app.capture(
    CaptureCommand(
        provider="chatgpt",
        source="tests/fixtures/chatgpt/branching-conversation.json",
        strategy="saved-json",
        output_root=Path("/tmp/my-archives"),
    )
)

print(result.status)  # "complete" or "unchanged"
print(result.archive_path)  # Path to the versioned bundle
print(result.archive_version)  # 1, 2, …
```

---

## Verifying an archive

```python
from mindcap import MindcapApplication
from mindcap.contracts import VerifyCommand
from pathlib import Path

app = MindcapApplication.default()

result = app.verify(VerifyCommand(bundle_path=Path("/path/to/bundle/v1")))

if result.status == "pass":
    print("Archive is intact")
else:
    print("Verification failed:", result.error)
```

---

## Inspecting an archive

```python
from mindcap import MindcapApplication
from mindcap.contracts import InspectCommand
from pathlib import Path

app = MindcapApplication.default()

data = app.inspect(
    InspectCommand(
        provider="suno",
        archive_path=Path("/path/to/bundle"),
    )
)
print(data)
```

---

## Running provider sync

```python
from mindcap import MindcapApplication
from mindcap.contracts import SyncCommand

app = MindcapApplication.default()

result = app.sync(
    SyncCommand(
        provider="suno",
        concurrency=2,
        resume=True,
    )
)

print(result.status)  # "complete", "interrupted", …
print(result.completed)  # number of successfully captured items
print(result.failed)  # number of failed items
```

---

## Receiving progress events

Implement the :class:`~mindcap.contracts.EventSink` protocol to receive
structured progress events from application services:

```python
from mindcap import MindcapApplication
from mindcap.contracts import (
    CaptureCommand,
    EventSink,
    MindcapEvent,
    OperationStarted,
    OperationCompleted,
    PhaseStarted,
)


class MyEventSink:
    def emit(self, event: MindcapEvent) -> None:
        if isinstance(event, OperationStarted):
            print(f"Starting {event.operation} for {event.provider}")
        elif isinstance(event, PhaseStarted):
            print(f"  Phase: {event.phase}")
        elif isinstance(event, OperationCompleted):
            print(f"Done in {event.elapsed_seconds:.1f}s")


app = MindcapApplication(event_sink=MyEventSink())
result = app.capture(CaptureCommand(provider="chatgpt", source="..."))
```

---

## Handling expected errors

```python
from mindcap import MindcapApplication
from mindcap.contracts import CaptureCommand
from mindcap.core.errors import (
    AuthenticationRequiredError,
    CaptureFailedError,
    InvalidSourceError,
    MindcapError,
)

app = MindcapApplication.default()

try:
    result = app.capture(
        CaptureCommand(provider="suno", source="https://suno.com/create?wid=...")
    )
except InvalidSourceError as exc:
    print("Bad source:", exc)
except AuthenticationRequiredError:
    print("Not authenticated — run: mindcap auth suno")
except CaptureFailedError as exc:
    print("Capture failed:", exc)
except MindcapError as exc:
    print("Unexpected library error:", exc)
```

---

## Custom registry (dependency injection)

```python
from mindcap import MindcapApplication
from mindcap.registry import PluginRegistry


class FakePlugin:
    source_type = "fake"

    def supports(self, value: str) -> bool:
        return True

    # … implement SourcePlugin protocol …


registry = PluginRegistry()
registry.register(FakePlugin())

app = MindcapApplication(registry=registry)
result = app.list_plugins()
# result.plugins → [PluginDescriptor(source_type="fake", ...)]
```

---

## Serializing results

All result models are Pydantic models and support standard serialization:

```python
import json

result = app.capture(...)

# JSON-safe dict
data = result.model_dump(mode="json")
print(json.dumps(data, indent=2))
```

---

## Stable public interfaces

The following are considered stable:

- `mindcap.MindcapApplication`
- `mindcap.registry.PluginRegistry`
- `mindcap.registry.create_default_registry`
- `mindcap.contracts.*` (commands, results, events, protocols)
- `mindcap.core.errors.*` (exception hierarchy)
- `mindcap.core.models.CaptureRequest`
- `mindcap.core.models.CaptureEnvelope`
- `mindcap.core.models.StoredBundle`

The following are provisional and may change:

- `mindcap.application.facade` internal methods
- Plugin-specific submodules under `mindcap.plugins.*`

Internal modules (leading underscores or `_` prefixed names) are not part of
the public API.
