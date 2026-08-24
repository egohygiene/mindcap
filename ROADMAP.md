---
schema: aether.architecture-document/v1
id: mindcap-roadmap
title: Mindcap Roadmap
kind: architecture-document
version: 0.1.0
status: draft
owners:
  - egohygiene
created: 2026-08-19
updated: 2026-08-24
governed_by:
  - architecture-roadmap
depends_on:
  - mindcap-vision
  - mindcap-pillars
  - mindcap-architecture
  - mindcap-decisions
related:
  - mindcap-purpose
  - mindcap-principles
  - mindcap-manifesto
  - mindcap-epistemology
supersedes: []
---

# Mindcap Roadmap

<!-- BEGIN ROADMAP EXECUTION SNAPSHOT -->
<!-- roadmap-manifest
schema: hygiene.roadmap/v1alpha1
repository: egohygiene/mindcap
visibility: public
publication: central
route: /roadmap/mindcap/
updated: 2026-08-24
-->
## 2026-08-24 execution snapshot

> This evidence-reconciled snapshot is the issue-generation and visual-roadmap handoff. The longer-horizon strategy below remains canonical context; generated HTML, JSON, progress, issue plans, and commit lists are projections.

**Lifecycle:** functional capture and vault alpha  
**Current gate:** Apply Ruff formatting to the vault catalog and CLI, restore CI, and reconcile stale extraction-era documentation.  
**North-star outcome:** A privacy-aware capture and vault layer that normalizes personal sources into durable, portable records.

### Visual roadmap publication

**Mode:** `central`  
**Route:** `/roadmap/mindcap/`  
**Current publication evidence:** Source-only Python library and CLI; no Pages or release publication observed.

Publish the public-safe projection through egohygiene.io at /roadmap/mindcap/. This repository owns intent and acceptance evidence; it does not add a second site deployment.

### Quest line

<!-- roadmap-step
id: MIC-Q01
status: complete
depends_on: []
issues: [13, 16, 18, 21, 24, 26, 28]
-->
#### MIC-Q01 — Build capture and vault foundations

**State:** `complete`  
**Depends on:** None

**Outcome:** Multiple source adapters, a library and CLI, and a vault implementation exist.

**Exit criteria:**

- [x] Representative sources can be captured into the vault.
- [x] Library and CLI entry points are present.

**Current evidence:**

- Issues #13, #16, #18, #21, #24, #26, and #28 correspond to implemented source and vault work.
- Architecture PR #29 merged at abce04b0d502504741061ab4c8ea65fe446e5f13 on 2026-08-20.

<!-- roadmap-step
id: MIC-Q02
status: blocked
depends_on: [MIC-Q01]
issues: []
-->
#### MIC-Q02 — Restore CI and standalone documentation

**State:** `blocked`  
**Depends on:** `MIC-Q01`

**Outcome:** The standalone repository formats, tests, packages, and documents itself correctly.

**Exit criteria:**

- [ ] Ruff is green for the vault catalog and CLI.
- [ ] README instructions no longer point to tools/mindcap in the former host repository.

**Current evidence:**

- CI was red because Ruff would reformat src/mindcap/vault/catalog.py and src/mindcap_cli/app.py.
- README retained stale tools/mindcap extraction instructions.

<!-- roadmap-step
id: MIC-Q03
status: ready
depends_on: [MIC-Q02]
issues: []
-->
#### MIC-Q03 — Add redaction and attachment guarantees

**State:** `ready`  
**Depends on:** `MIC-Q02`

**Outcome:** Sensitive content and binary attachments follow explicit, tested storage and export rules.

**Exit criteria:**

- [ ] Redaction policies are configurable and covered by fixtures.
- [ ] Attachment integrity, limits, and failure recovery are tested.

**Current evidence:**

- Redaction and attachment guarantees were absent from the observed implementation and backlog.

<!-- roadmap-step
id: MIC-Q04
status: planned
depends_on: [MIC-Q03]
issues: []
-->
#### MIC-Q04 — Stabilize the source-plugin SDK

**State:** `planned`  
**Depends on:** `MIC-Q03`

**Outcome:** New capture sources can be added without coupling them to vault internals.

**Exit criteria:**

- [ ] A versioned adapter contract and conformance fixtures exist.
- [ ] At least two existing sources migrate without behavioral regression.

**Current evidence:**

- Multiple adapters exist, but no stable plugin SDK release was observed.

<!-- roadmap-step
id: MIC-Q05
status: planned
depends_on: [MIC-Q03, MIC-Q04]
issues: []
-->
#### MIC-Q05 — Prove vault recovery and Mindgarden ingestion

**State:** `planned`  
**Depends on:** `MIC-Q03`, `MIC-Q04`

**Outcome:** A backed-up vault restores cleanly and feeds Mindgarden through a versioned privacy boundary.

**Exit criteria:**

- [ ] Backup and restore preserve identity and attachment integrity.
- [ ] Mindgarden ingests a sanitized fixture without direct dependence on Mindcap internals.

**Current evidence:**

- No release, backup/restore proof, or Mindgarden integration evidence was observed.

### Roadmap-to-issue handoff

- A step is complete only when its exit criteria and required evidence are satisfied; commit count never determines progress.
- Ready or planned steps without an issue are candidates for the private, duplicate-aware roadmap.issue-plan.json dry run.
- Issue creation or reconciliation requires human approval or an explicitly authorized Pace operation and returns issue references through a reviewable roadmap pull request.
- Pull requests and commits should include Roadmap-Step: <ID>; historical evidence may be linked through existing issue and pull-request relationships.
- Public rendering uses only allowlisted build-time evidence and never places a GitHub token or private issue plan in the browser artifact.

<!-- END ROADMAP EXECUTION SNAPSHOT -->

## Strategic context

This roadmap describes capability evolution, not promised dates or an issue queue. Sequence follows architecture dependencies and may change when evidence or risk changes.

## Phase 1: Stabilize current provider contracts

**Outcome:** A bounded capability advances from documented intent to validated, independently usable behavior.

**Exit signals:**

- The owning contract and acceptance criteria are versioned.
- Implementation and documentation agree.
- Relevant tests and safety checks pass.
- Downstream consumers and migration impact are understood.
- Remaining uncertainty is visible.

## Phase 2: Complete redaction and attachment handling

**Outcome:** A bounded capability advances from documented intent to validated, independently usable behavior.

**Exit signals:**

- The owning contract and acceptance criteria are versioned.
- Implementation and documentation agree.
- Relevant tests and safety checks pass.
- Downstream consumers and migration impact are understood.
- Remaining uncertainty is visible.

## Phase 3: Generalize plugin development

**Outcome:** A bounded capability advances from documented intent to validated, independently usable behavior.

**Exit signals:**

- The owning contract and acceptance criteria are versioned.
- Implementation and documentation agree.
- Relevant tests and safety checks pass.
- Downstream consumers and migration impact are understood.
- Remaining uncertainty is visible.

## Phase 4: Connect verified Garden ingestion

**Outcome:** A bounded capability advances from documented intent to validated, independently usable behavior.

**Exit signals:**

- The owning contract and acceptance criteria are versioned.
- Implementation and documentation agree.
- Relevant tests and safety checks pass.
- Downstream consumers and migration impact are understood.
- Remaining uncertainty is visible.

## Phase 5: Prove backup and restore operations

**Outcome:** A bounded capability advances from documented intent to validated, independently usable behavior.

**Exit signals:**

- The owning contract and acceptance criteria are versioned.
- Implementation and documentation agree.
- Relevant tests and safety checks pass.
- Downstream consumers and migration impact are understood.
- Remaining uncertainty is visible.

## Cross-cutting tracks

- Security, privacy, accessibility, licensing, and provenance.
- Documentation, architecture portals, examples, and onboarding.
- Packaging, release, compatibility, and self-hosting.
- Organization integration through explicit contracts.
- Observatory evidence and Pace conformance when those systems exist.

## Deferred direction

Optional managed services, enterprise controls, marketplaces, and the conversational organization compiler remain later architecture work. Current choices should preserve portability and avoid foreclosing them.

## Evidence and uncertainty

- **Observed:** The repository README and checked-in implementation establish an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs.
- **Decided for this draft:** The repository owns the bounded concern described here and participates through versioned contracts.
- **Proposed:** Target systems and later roadmap phases remain proposals until accepted and implemented.
- **Open question:** Which parts of this draft should become active in the first independently versioned release?
