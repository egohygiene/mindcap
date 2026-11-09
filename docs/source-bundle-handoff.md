# Proposed captured-source handoff checkpoint

Refs #35; consumer egohygiene/mindgarden#11 and #48. This is a bounded draft
contract/producer proof, not full incremental capture or accepted integration.

`mindcap.contracts.source_bundle` defines `CapturedSourceBundle`, `HandoffUnit`,
`Producer`, `build_source_bundle`, and its generated JSON Schema in
`schemas/captured-source-bundle.v1.schema.json`. This provider-neutral ZIP owns
explicit source identity, captured-at time, producer version/commit, private
sensitivity, complete/partial status, prior-package digest, fixed diagnostic
codes, exact payload inventory and normalized unit/evidence declarations.

`CAPTURED-SOURCE.json` inventories every other member by relative path, role,
size and SHA-256. It has no recursive self-hash; downstream capture computes the
immutable whole-package digest. Raw and normalized payloads remain distinct.
Unit identities are scoped by the logical source ID; identical content and IDs
can be reused across captures while new observations retain their own package.
Unknown/ambiguous identities are never resolved by title or URL similarity.

The initial capabilities explicitly declare delta, remote fetching and knowledge
review unavailable. A prior digest records lineage, not proof that the prior
package exists or that a complete delta has been computed. Removed source records
never request deletion of knowledge. Incomplete capture remains partial. The
schema and hashes validate shape/integrity, not producer authenticity or truth.

The generic builder accepts captured byte mappings and typed declarations. It
does not know browser fields, fetch URLs, discover plugins, write canonical
knowledge, choose garden scope, or invoke a model. Consumer adapters validate
each supported evidence locator before application. Paths, duplicate identities,
case/Unicode ambiguity, counts and bytes are bounded before packaging.

## Existing bookmark producer

`mindcap.plugins.chrome_bookmarks.handoff.bookmark_source_bundle` exports an
explicit existing verified capture without changing it. Provider-specific record
mapping remains inside the plugin. Every original file is retained under
`capture/`; a separate normalized record projection removes capture time and
machine profile labels and uses caller-supplied logical profile identities.
Native IDs remain observed; missing IDs are explicitly snapshot-local.
URLs and bookmarklets are untrusted source strings, never acquisition requests.

```python
from mindcap.plugins.chrome_bookmarks.handoff import bookmark_source_bundle

payload = bookmark_source_bundle(
    captured_directory,
    source_id="my-explicit-browser-source",
    producer_revision=reviewed_mindcap_commit,
    profile_identities=explicit_logical_profile_map,
)
```

The operator supplies the logical profile map; physical directory names or path
hashes cannot silently become portable identity. Keep the returned private ZIP
outside public repositories. There is no automatic capture/export/upload step.

## Verification and remaining work

The synthetic suite covers exact raw bytes, deterministic packages, required
profile mappings, an actual multi-profile fixture through the existing capture
plugin, unsafe paths, duplicate/dangling declarations, partial status, explicit
unsupported capabilities and symlink refusal with acquisition sockets blocked.
No real browser profile, archive or credential is accessed.

The first full local run passed 620 tests; two existing loopback endpoint tests
were blocked by the execution sandbox. A socket-enabled full rerun is recorded
in the PR. Ruff passes repository-wide. No GitHub workflow is manually launched.

Full #35 still owns snapshot comparisons, additions/moves/renames/tombstones,
cursor/reset handling, cross-platform recurrence and actual consumer acceptance.
The new schema is proposed until reviewed. Mindgarden must pin this draft's exact
source/schema and prove its own adapter without importing Mindcap internals.
