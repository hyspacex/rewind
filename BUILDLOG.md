# Rewind build log

This file is updated as milestones land. The initial package and signing core are an explicit bootstrap exception: a recorder cannot truthfully govern its own installation.

## 2026-07-20T05:23Z — Locked scope and bootstrap

- Codex implemented: package metadata; deterministic canonical JSON, SHA-256 content IDs, Ed25519 envelopes, chained JSONL verification, repository-local storage, safe initialization, policy v1/v2 objects, CLI shell, MCP bootstrap, Codex workflow instructions, and verified project-local MCP configuration.
- I decided: the individual developer receipt is the front door; deterministic verification is the product; PyNaCl is the single signing dependency; macOS/Linux and Python 3.10+ are the initial supported environment.
- Alternatives rejected: runtime LLM calls, live dashboard, destructive recovery, general policy language, blockchain, and an optional Codex hook before core acceptance is complete.
- Tests run: toolchain inspection; installed Codex 0.145.0-alpha.18 `mcp add` schema generation in a temporary home; official Codex MCP configuration reference lookup; 2 signing tests; editable install; CLI help.
- Commit SHA: `1640e0b`.
- Limitation: the active Codex session will not discover a newly written project MCP config until a new/restarted session.

## 2026-07-20T05:31Z — Recorder activated

- Codex implemented: fixed the first-event bootstrap boundary, initialized the trusted recorder, started task `task_97167c332e`, and recorded the initial private Git checkpoint.
- I decided: generated `.rewind/` state remains entirely local and ignored; the repository ships its defaults in the package rather than committing a live recorder identity.
- Alternatives rejected: committing a demo/private key or treating the bootstrap files as if Rewind had governed their creation.
- Tests run: successful `rewind init`; signed genesis and policy activation verification; `rewind status`.
- Commit SHA: `1640e0b` is the explicit bootstrap parent; feature commit pending.
- Limitation: private Git ref writes require sandbox approval in this Codex session, although normal local Git does not.

## 2026-07-20T05:44Z — Developer receipt and historical replay

- Codex implemented: temporary-index Git checkpoints, untracked-file capture, recorded checks, content-addressed evidence, task lifecycle, deterministic risk signals, Rich receipt/timeline, branch-only recovery, self-contained HTML, narrow v1/v2 replay, four forensic fixtures, the offline demo, and five stdio MCP tools with CLI parity.
- I decided: Git-ignored files stay ignored while all other untracked files are captured; an observed deployment is evidence that an external action happened, not an authorization request; L3 is blocked when L2 fails.
- Alternatives rejected: filtering common generated files inside Rewind, retroactive approval of an observed deployment, a second receipt logic path for HTML, and adding a Codex hook before acceptance.
- Tests run through Rewind: 2 passed at `cp_03`; an intentional expanded-suite failure at `cp_05` exposed an unignored test cache; 10 passed at `cp_06`; 11 passed including MCP at `cp_08`; offline demo passed at `cp_07` and again from a fresh console install at `cp_09`.
- Commit SHA: `d9d8e41`.
- Limitations: the audit engine intentionally supports only the two documented policies; the active Codex session predates `.codex/config.toml`, so MCP is verified through a real stdio client round trip and must be discovered by Codex after restart.

## 2026-07-20T05:44Z — Visual and install verification

- Codex implemented: macOS-safe `venv/` setup for the project MCP command and improved blocked-stage audit copy.
- I decided: keep the calm graphite instrument aesthetic at a 1280×720 target; show the developer receipt first and the L0–L3 language only in the audit reveal.
- Alternatives rejected: installing Node solely for Playwright, external fonts/assets, JavaScript UI, gradients, and decorative effects.
- Tests run: fresh `venv/bin/pip install -e '.[dev]'`; installed `rewind --help` and `--version`; in-app real-browser checks at 1280×720 for both reports; zero horizontal overflow, external assets, scripts, console warnings, or errors.
- Commit SHA: core `d9d8e41`; tests `028d934`.
- Limitation: the dot-prefixed `.venv/` on this Mac inherited a filesystem hidden flag that Python used to skip editable `.pth` loading; the documented non-hidden `venv/` path avoids that environment-specific issue.

## 2026-07-20T05:49Z — Code freeze and judge handoff

- Codex implemented: complete README, exact three-minute demo script, generated-fixture guide, CLI-replayable forensic repositories, architecture and format documentation, and explicit collaboration/threat-model sections.
- I decided: generated fixtures remain disposable rather than committed; no `/feedback` Session ID is claimed because one was not captured in the build-session materials.
- Alternatives rejected: a checked-in private/demo identity, a misleading screenshot that could drift, and claims of Windows validation or universal enforcement.
- Tests run: 12 passed including the full generated-demo layout; final offline demo generated; both documented CLI replay commands passed; recovery branch exists while `main` remains checked out; Git hygiene, report self-containment, and secret/private-state audit passed; `codex mcp list/get` recognizes the enabled project server.
- Commit SHA: `136301d`.
- Limitation: the already-running desktop task cannot inject newly configured MCP tools into its active tool set; the Codex CLI recognizes the server and the stdio transport/exact five tools are acceptance-tested directly. A new task or client restart is required for the recorded MCP demo call.

## 2026-07-20T07:33Z — Independent verification and trust hardening

- Codex implemented: cross-process lifecycle/event locks; atomic evidence-object writes; durable failed evidence for command-launch errors; incomplete-attempt detection; semantic agreement checks between signed fields and hashed command output; integrity-gated recovery; task-qualified recovery commands; provisional active-task receipts; signed task-level protected globs; fresh-clone initialization that preserves committed shared config; fail-fast nested lifecycle handling; exact task diffs; hash-verified evidence inspection; and a public audit lifecycle.
- I decided: any unverifiable, contradictory, incomplete, ambiguous, or missing recovery state must fail closed; arbitrary commands remain checks rather than tests; active receipts cannot claim completion; report recovery commands always name their task; recorded output is rendered without replaying terminal control characters; and no `/feedback` Session ID is fabricated.
- Alternatives rejected: trusting a signed `passed` boolean without reconciling exit status, overwriting shared `.rewind` configuration, allowing checkpoint IDs to race or silently resolve across tasks, calling unverified checkpoints safe, blocking forever on nested lifecycle commands, and keeping audit replay accessible only through demo fixtures.
- Tests run through Rewind: 45 passed, including parallel subprocess checkpoints/appends, in-flight finish ordering, missing executables, incomplete attempts, semantic evidence mismatch, active receipt behavior, cross-task recovery, missing checkpoint refs, shared-config initialization, public audit replay, CLI inspection, MCP schema/call, and all four forensic fixtures. The final offline demo passed and produced the expected PFFF/PPFF/PPPF/PPPP matrix.
- Commit SHA: pending integration.
