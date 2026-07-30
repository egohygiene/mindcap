"""Provider-agnostic synchronization subsystem for Mindcap.

This package implements the reusable batch sync layer that sits above the
single-source capture pipeline.  Provider-specific collection discovery
belongs in each plugin; batch planning, checkpointing, resume, locking,
progress reporting, cache evaluation, and run reporting live here.

Typical usage::

    from mindcap.sync.runner import SyncRunner
    from mindcap.sync.run_storage import RunStorage

"""
