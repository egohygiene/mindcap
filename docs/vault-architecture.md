# Mindcap Vault Architecture

Mindcap vaults package many finalized provider archives into one logical, provider-neutral archive without copying media into a mutable database.

## Logical model

A vault is a directory containing immutable pack files, immutable catalog generations, append-only receipts, and an `incomplete/` quarantine area:

```text
<provider>.mindcap-vault/
├── vault.json
├── catalog/generations/
├── packs/
├── imports/
├── reports/
└── incomplete/
```

`vault.json` is created once and records the vault ID, format (`mindcap.vault/v1`), version, creation timestamp, and hashing algorithm.

## Why media is not stored as SQLite BLOBs

The catalog must remain small, portable, and safe to publish as immutable generations. Large media and raw response payloads live in ZIP64 pack files, while SQLite stores only searchable metadata, archive relationships, and object locations.

## Why Google Drive catalogs are immutable generations

Mindcap does not mutate a live SQLite file in place on a mounted Drive path. Each ingestion stages catalog work locally, validates foreign keys and `PRAGMA integrity_check`, then publishes a new sealed catalog generation. Readers select the highest sealed valid generation.

A synced Google Drive copy is **not** automatically a complete backup strategy. Users must wait for Drive synchronization to finish independently.

## Catalog schema

The catalog stores:

- schema migrations
- vault metadata
- catalog generations
- ingestion runs
- archive units (`provider + source_id + capture_version`)
- normalized provider records as JSON text plus indexed identity fields
- deduplicated objects by SHA-256
- immutable packs
- object locations inside packs
- archive-file references from bundle-relative paths to object hashes

Foreign-key enforcement is always enabled.

## Pack lifecycle

Packs are standard ZIP64 files written with `ZIP_STORED` by default. Member names are deterministic content-addressed paths derived from SHA-256 object hashes. A pack is usable only when its seal exists and validates.

## Ingestion states

1. Preflight path safety checks
2. Deterministic bundle discovery
3. Source validation with the provider adapter
4. Exact hashing and dedup planning
5. Pack writing and sealing for new objects only
6. Local catalog staging and validation
7. Immutable catalog publication and sealing
8. Import receipt publication
9. Final vault verification

Interrupted or partial artifacts stay in `incomplete/` and are never referenced by a sealed catalog.

## Deduplication

Every file in every source bundle is hashed independently, including `manifest.json` and `checksums.json`. Objects are keyed only by SHA-256, so duplicate content can be reused across paths, workspaces, and capture versions.

## Recovery behavior

Mindcap never deletes or prunes source archives during vault ingestion. Failed pack writes and failed catalog publications leave the previous sealed vault readable. Stale writer locks require explicit recovery.

If sealed packs exist without a published catalog generation, later ingestions can reuse them safely after seal validation.

## Verification modes

- **Fast verification** validates vault metadata, the latest sealed catalog generation, referenced packs, and seal hashes.
- **Deep verification** additionally reads every stored object and recomputes SHA-256.

Before considering any local deletion, users must run deep verification and a restoration test.

## Restoration

`mindcap vault restore` recreates the original bundle-relative directory tree for a selected archive identity, streams objects from packs, verifies each object before committing the restored file, and emits a restore receipt.

## Provider extension points

Providers opt into vault ingestion by supplying a `VaultArchiveAdapter` from the existing plugin registry. The generic vault subsystem does not import provider implementations directly.

## Storage backend boundary (ADR)

Vault artifact construction remains local and deterministic, while durable
publication is delegated to a backend contract under `mindcap.vault.backends`.

- `filesystem` locators remain native local paths.
- `google-drive` locators are canonical `gdrive://<folder-id>` identifiers.
- Core vault orchestration does not import Google Drive integration modules
  directly; integration details stay under
  `mindcap.integrations.google_drive`.

This preserves the vault format and enables resumable remote backends without
forcing provider plugins or core catalog logic to become backend-specific.

## Capacity and operational guidance

- Expect pack overhead in addition to stored object bytes.
- Choose conservative pack sizes for mounted-drive caching and recovery work.
- Keep the staging directory local; it only needs space for the working catalog.
- This feature does **not** implement source deletion or pruning.
