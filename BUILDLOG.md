# Rewind build log

This file is updated as milestones land. The initial package and signing core are an explicit bootstrap exception: a recorder cannot truthfully govern its own installation.

## 2026-07-20T05:23Z — Locked scope and bootstrap

- Codex implemented: package metadata; deterministic canonical JSON, SHA-256 content IDs, Ed25519 envelopes, chained JSONL verification, repository-local storage, safe initialization, policy v1/v2 objects, CLI shell, MCP bootstrap, Codex workflow instructions, and verified project-local MCP configuration.
- I decided: the individual developer receipt is the front door; deterministic verification is the product; PyNaCl is the single signing dependency; macOS/Linux and Python 3.10+ are the initial supported environment.
- Alternatives rejected: runtime LLM calls, live dashboard, destructive recovery, general policy language, blockchain, and an optional Codex hook before core acceptance is complete.
- Tests run: toolchain inspection; installed Codex 0.145.0-alpha.18 `mcp add` schema generation in a temporary home; official Codex MCP configuration reference lookup.
- Commit SHA: pending.
- Limitation: the active Codex session will not discover a newly written project MCP config until a new/restarted session.

