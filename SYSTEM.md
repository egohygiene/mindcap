---
schema: aether.architecture-document/v1
id: mindcap-system
title: Mindcap System
kind: architecture-document
version: 0.1.0
status: draft
owners:
  - egohygiene
created: 2026-08-19
updated: 2026-08-19
governed_by:
  - architecture-system
depends_on:
  - mindcap-foundations
  - mindcap-ontology
related:
  - mindcap-purpose
  - mindcap-vision
  - mindcap-principles
  - mindcap-pillars
supersedes: []
---

# Mindcap System

## Purpose and scope

This document identifies Mindcap's logical systems and responsibilities. It answers what the major systems do; [ARCHITECTURE.md](ARCHITECTURE.md) owns their structural organization and dependency rules.

## System inventory

| System | State | Responsibility |
| --- | --- | --- |
| CLI | Current | Owns its bounded portion of an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs; exposes explicit inputs, outputs, failure states, and evidence. |
| Plugin registry | Current | Owns its bounded portion of an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs; exposes explicit inputs, outputs, failure states, and evidence. |
| Provider adapters | Current | Owns its bounded portion of an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs; exposes explicit inputs, outputs, failure states, and evidence. |
| Capture strategies | Current | Owns its bounded portion of an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs; exposes explicit inputs, outputs, failure states, and evidence. |
| Normalization pipeline | Current or evolving | Owns its bounded portion of an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs; exposes explicit inputs, outputs, failure states, and evidence. |
| Artifact bundle | Current or evolving | Owns its bounded portion of an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs; exposes explicit inputs, outputs, failure states, and evidence. |
| Vault catalog and pack | Current or evolving | Owns its bounded portion of an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs; exposes explicit inputs, outputs, failure states, and evidence. |
| Verification and restore | Current or evolving | Owns its bounded portion of an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs; exposes explicit inputs, outputs, failure states, and evidence. |

## External systems

- ChatGPT, Suno, and DistroKid
- Mindgarden ingestion
- local and synchronized storage
- future web, PDF, image, repository, and media plugins

External systems are integrations, not hidden implementation units. Each requires version, authentication, availability, data, error, and replacement boundaries appropriate to its risk.

## System interactions

Inputs enter through an adapter or validated contract, move through domain systems, produce artifacts and diagnostics, and leave through a stable interface. Evidence flows back to validation, review, and future decisions.

## Failure model

Systems fail closed at destructive, publication, privacy, and security boundaries. Partial results identify coverage and remain distinguishable from complete success.

## Evidence and uncertainty

- **Observed:** The repository README and checked-in implementation establish an extensible Python CLI for capturing source material, preserving verified archives, and preparing canonical knowledge inputs.
- **Decided for this draft:** The repository owns the bounded concern described here and participates through versioned contracts.
- **Proposed:** Target systems and later roadmap phases remain proposals until accepted and implemented.
- **Open question:** Which parts of this draft should become active in the first independently versioned release?
