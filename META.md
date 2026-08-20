---
schema: aether.architecture-document/v1
id: mindcap-meta
title: Mindcap Meta
kind: architecture-document
version: 0.1.0
status: draft
owners:
  - egohygiene
created: 2026-08-19
updated: 2026-08-19
governed_by:
  - architecture-meta
depends_on:
  - mindcap-epistemology
  - mindcap-ai-constitution
related:
  - mindcap-purpose
  - mindcap-vision
  - mindcap-principles
  - mindcap-pillars
supersedes: []
---

# Mindcap Meta Architecture

## Architecture-system overview

Mindcap's architecture is an 18-document graph materialized from the Aether architecture specifications. Each document owns one bounded concern. This index maps ownership and relationships without replacing the documents themselves.

## Document inventory

| Artifact | Path | Category | Status | Governing specification | Upstream dependencies |
| --- | --- | --- | --- | --- | --- |
| mindcap-purpose | [PURPOSE.md](PURPOSE.md) | Identity | draft | architecture-purpose | — |
| mindcap-vision | [VISION.md](VISION.md) | Identity | draft | architecture-vision | mindcap-purpose |
| mindcap-principles | [PRINCIPLES.md](PRINCIPLES.md) | Identity | draft | architecture-principles | mindcap-purpose, mindcap-vision |
| mindcap-pillars | [PILLARS.md](PILLARS.md) | Identity | draft | architecture-pillars | mindcap-purpose, mindcap-vision, mindcap-principles |
| mindcap-manifesto | [MANIFESTO.md](MANIFESTO.md) | Identity | draft | architecture-manifesto | mindcap-purpose, mindcap-vision, mindcap-principles, mindcap-pillars |
| mindcap-epistemology | [EPISTEMOLOGY.md](EPISTEMOLOGY.md) | Meta | draft | architecture-epistemology | mindcap-purpose, mindcap-principles |
| mindcap-ai-constitution | [AI_CONSTITUTION.md](AI_CONSTITUTION.md) | Meta | draft | architecture-ai-constitution | mindcap-purpose, mindcap-vision, mindcap-principles, mindcap-epistemology |
| mindcap-ontology | [ONTOLOGY.md](ONTOLOGY.md) | Domain | draft | architecture-ontology | mindcap-purpose, mindcap-vision, mindcap-principles, mindcap-epistemology |
| mindcap-personal-model | [PERSONAL_MODEL.md](PERSONAL_MODEL.md) | Domain | draft | architecture-personal-model | mindcap-purpose, mindcap-vision, mindcap-principles, mindcap-epistemology, mindcap-ontology |
| mindcap-foundations | [FOUNDATIONS.md](FOUNDATIONS.md) | Foundation | draft | architecture-foundations | mindcap-purpose, mindcap-principles, mindcap-epistemology |
| mindcap-system | [SYSTEM.md](SYSTEM.md) | Foundation | draft | architecture-system | mindcap-foundations, mindcap-ontology |
| mindcap-architecture | [ARCHITECTURE.md](ARCHITECTURE.md) | Foundation | draft | architecture-architecture | mindcap-foundations, mindcap-system |
| mindcap-methodology | [METHODOLOGY.md](METHODOLOGY.md) | Foundation | draft | architecture-methodology | mindcap-principles, mindcap-epistemology, mindcap-ai-constitution, mindcap-foundations, mindcap-architecture |
| mindcap-design | [DESIGN.md](DESIGN.md) | Experience | draft | architecture-design | mindcap-purpose, mindcap-vision, mindcap-principles, mindcap-personal-model |
| mindcap-design-system | [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | Experience | draft | architecture-design-system | mindcap-personal-model, mindcap-design |
| mindcap-decisions | [DECISIONS.md](DECISIONS.md) | Governance | draft | architecture-decisions | mindcap-principles, mindcap-epistemology, mindcap-foundations, mindcap-system, mindcap-architecture |
| mindcap-roadmap | [ROADMAP.md](ROADMAP.md) | Foundation | draft | architecture-roadmap | mindcap-vision, mindcap-pillars, mindcap-architecture, mindcap-decisions |
| mindcap-meta | [META.md](META.md) | Meta | draft | architecture-meta | mindcap-epistemology, mindcap-ai-constitution |

## Relationship graph

```mermaid
flowchart TD
  PURPOSE --> VISION --> PRINCIPLES --> PILLARS --> MANIFESTO
  PURPOSE --> EPISTEMOLOGY --> AI[AI Constitution]
  PRINCIPLES --> EPISTEMOLOGY
  EPISTEMOLOGY --> ONTOLOGY --> PERSONAL[Personal Model]
  PRINCIPLES --> FOUNDATIONS
  EPISTEMOLOGY --> FOUNDATIONS
  FOUNDATIONS --> SYSTEM --> ARCHITECTURE --> METHODOLOGY
  PERSONAL --> DESIGN --> DS[Design System]
  ARCHITECTURE --> DECISIONS --> ROADMAP
  PILLARS --> ROADMAP
  AI --> META
  EPISTEMOLOGY --> META
```

## Ownership map

- Identity documents own why the repository exists, its desired future, decision heuristics, strategic capabilities, and public commitments.
- Meta documents own knowledge integrity, AI authority, and navigation of this document system.
- Domain documents own canonical concepts and bounded human assumptions.
- Foundation documents own invariants, logical systems, structure, working method, and strategic evolution.
- Experience documents own intended experience and reusable semantic design language.
- Governance owns accepted architectural decisions and historical lineage.

## Reading order

1. PURPOSE, VISION, and PRINCIPLES.
2. EPISTEMOLOGY and ONTOLOGY.
3. FOUNDATIONS, SYSTEM, and ARCHITECTURE.
4. PERSONAL_MODEL, DESIGN, and DESIGN_SYSTEM when evaluating human-facing surfaces.
5. AI_CONSTITUTION before delegating consequential work.
6. DECISIONS and ROADMAP for accepted constraints and evolution.

## Authoring order

Follow the dependency graph from purpose through identity and evidence, then domain and foundations, experience, governance, roadmap, and finally this META index.

## Lifecycle and validation

All documents begin as draft and require human review before becoming active. Validation covers frontmatter, stable identifiers, links, graph acyclicity, ownership boundaries, evidence labels, Markdown structure, and agreement with repository reality.

## Change propagation

A material upstream change triggers review of every downstream node. Implementation changes first update the owning specification or decision when they alter durable behavior; META changes whenever inventory or relationships change.

## Gaps and omissions

- No document in this set is intentionally omitted because Mindcap has repository, automation, human, AI, and public or documentation surfaces that justify the complete reference set.
- Target systems remain provisional where implementation evidence is absent.
- Repository-local schemas and automated graph validation should be added or connected to Aether in a later conformance pass.

## Evidence and uncertainty

- **Observed:** The repository README and checked-in implementation establish an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs.
- **Decided for this draft:** The repository owns the bounded concern described here and participates through versioned contracts.
- **Proposed:** Target systems and later roadmap phases remain proposals until accepted and implemented.
- **Open question:** Which parts of this draft should become active in the first independently versioned release?
