---
schema: aether.architecture-document/v1
id: mindcap-ontology
title: Mindcap Ontology
kind: architecture-document
version: 0.1.0
status: draft
owners:
  - egohygiene
created: 2026-08-19
updated: 2026-08-19
governed_by:
  - architecture-ontology
depends_on:
  - mindcap-purpose
  - mindcap-vision
  - mindcap-principles
  - mindcap-epistemology
related:
  - mindcap-pillars
  - mindcap-manifesto
  - mindcap-ai-constitution
  - mindcap-personal-model
supersedes: []
---

# Mindcap Ontology

## Domain scope

Mindcap models the concepts needed for capture important digital context with provenance and integrity so it can be reviewed, restored, and safely transformed into knowledge. The ontology names conceptual entities and relationships; it is not a source-code class model, API schema, or database design.

## Canonical concepts

| Concept | Meaning |
| --- | --- |
| Source | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |
| Provider | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |
| Capture strategy | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |
| Capture envelope | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |
| Raw artifact | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |
| Normalized artifact | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |
| Vault | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |
| Generation | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |
| Manifest | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |
| Verification | A canonical concept in the Mindcap domain whose exact fields belong to specifications or schemas, not this ontology. |

## Core relationships

- A repository or person provides source context to one or more domain artifacts.
- A specification constrains how an artifact is interpreted or produced.
- A plan separates proposed action from execution.
- Evidence supports a claim; a decision authorizes a durable direction.
- Provenance connects derived artifacts to their inputs and processing context.
- A consumer integrates through an explicit interface rather than internal structure.

## Boundaries

- Conceptual identity is distinct from filesystem path, database identifier, or display label.
- Observed state is distinct from desired state.
- Proposed relationships are not accepted facts.
- Neighboring repositories retain ownership of their domain concepts.

## Evidence and uncertainty

- **Observed:** The repository README and checked-in implementation establish an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs.
- **Decided for this draft:** The repository owns the bounded concern described here and participates through versioned contracts.
- **Proposed:** Target systems and later roadmap phases remain proposals until accepted and implemented.
- **Open question:** Which parts of this draft should become active in the first independently versioned release?
