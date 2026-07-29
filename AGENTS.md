# Mindcap Agent Guidance

## Purpose

Mindcap captures heterogeneous sources, normalizes them into stable canonical
representations, and prepares them for knowledge extraction and Gardenization.
ChatGPT is the first source plugin, not a special case in the core pipeline.

## Architecture Rules

- Keep provider behavior inside `src/mindcap/plugins/`.
- Add sources by implementing `SourcePlugin` and registering the plugin.
- Add acquisition mechanisms by implementing `CaptureStrategy`.
- Do not add provider conditionals to the core pipeline or filesystem storage.
- Keep browser authentication state outside repository and archive directories.
- Never log or persist cookies, authorization headers, session tokens, or
  Playwright storage state.
- Treat provider network endpoints and payloads as unstable adapter details.
- Preserve raw response boundaries; merge only during normalization.
- Keep source capture deterministic and separate from AI knowledge extraction.
- Prefer typed contracts, dependency injection, and small composition-based
  components over deep inheritance.

## Commands

```bash
uv sync
uv run playwright install chromium
uv run pytest
uv run ruff check .
uv run mindcap plugins list
```

## Verification

Before considering a change complete:

1. Run the unit tests.
1. Run Ruff.
1. Confirm fixtures work without network access.
1. Confirm generated artifacts contain no authentication material.
1. Confirm provider-specific changes do not modify core orchestration unless a
   genuinely source-agnostic contract changed.
