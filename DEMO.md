# Rewind three-minute demo

This path is deterministic, local, and network-free after installation.

## Prepare

```bash
python3 -m venv venv
venv/bin/pip install -e '.[dev]'
venv/bin/rewind demo --output /tmp/rewind-video
ROOT="$PWD"
```

The demo command creates:

- a disposable Git repository at `/tmp/rewind-video/developer-task`;
- a signed task lifecycle and developer receipt;
- a passing check bound to `cp_02`;
- final changes outside scope and after that check;
- the safe branch `rewind/retry-last-tested`;
- `task-report.html`;
- all four forensic lifecycles and `forensic-report.html`.

Use a new or empty output directory for each rehearsal.

## 0:00–0:12 — The immediate pain

Narration:

> I gave a coding agent a narrow task. It says it finished—but what exactly changed, did it stay in scope, and which exact version did we actually test?

Show:

```bash
cd /tmp/rewind-video/developer-task
"$ROOT/venv/bin/rewind" receipt
```

## 0:12–0:40 — Natural Codex workflow

Show the root `AGENTS.md`, then Codex `/mcp` with Rewind’s five tools. If the current session predates the project config, say that once and use the CLI fallback:

```bash
"$ROOT/venv/bin/rewind" timeline
```

Narration:

> Codex starts a scoped Rewind task, records meaningful checkpoints, and runs important checks through Rewind. The check executes normally without a shell, but its full output is hashed and bound to the exact Git tree immediately before it ran.

The deterministic fixture’s task is “Add retry handling to the upload worker,” with only `demo_app/upload/**` and `tests/**` allowed.

## 0:40–1:15 — The receipt

Keep the Rich receipt visible.

Point out:

- `deploy.yaml` and `pyproject.toml` are outside declared scope;
- the deployment file and a worker edit happened after the passing check;
- the dependency manifest changed;
- `cp_02` is the last tested state and `cp_03` is final.

Narration:

> Rewind does not guess whether the agent understood me. These are deterministic diffs against the signed task scope and recorded evidence tree.

## 1:15–1:35 — Safe recovery

Capture the current branch, HEAD, and status:

```bash
git branch --show-current
git rev-parse --short HEAD
git status --short
"$ROOT/venv/bin/rewind" recover cp_02 --branch rewind/video-last-tested
git branch --show-current
git rev-parse --short HEAD
git status --short
git branch --list 'rewind/*'
```

Narration:

> Recovery does not reset, checkout, clean, stash, or overwrite anything. It creates a normal branch pointing at the checkpoint commit and tells me what I may switch to later.

## 1:35–2:15 — Visual report

Open:

```bash
open /tmp/rewind-video/task-report.html
```

At 1280×720, show:

1. the large outcome and four signal cards;
2. changed files grouped by scope and evidence freshness;
3. the intent → action → evidence row;
4. the recovery command and timeline;
5. the collapsed signed audit details.

Narration:

> This is one self-contained HTML file—no server, scripts, external fonts, images, frameworks, or network requests. The CLI and browser report consume the same receipt model.

## 2:15–2:42 — Authenticity is not authority

Run the strongest reveal:

```bash
cd /tmp/rewind-video/forensics/03-role-revoked
"$ROOT/venv/bin/rewind" replay action_deploy_01
```

Narration:

> Every event signature verifies. The evidence, approval, tree, and artifact all bind, so L0 through L2 pass. But the deployer role was revoked before the observed deployment. L3 fails: the record is authentic; the deployment was not authorized.

Then show policy drift:

```bash
cd /tmp/rewind-video/forensics/04-policy-evolution
"$ROOT/venv/bin/rewind" replay action_deploy_01
```

> This action was allowed under v1. Current v2 separation-of-duties rules reject a repeat.

Optionally open the side-by-side report:

```bash
open /tmp/rewind-video/forensic-report.html
```

## 2:42–2:58 — Close

Narration:

> Rewind shows exactly what your coding agent did, catches where it went off course, and gives you a safe place to recover—with a signed record teams can audit later. No blockchain: the useful primitive is a signed, content-addressed chain bound to real Git state. Codex with GPT-5.6 co-designed, implemented, tested, and documented this project; Rewind’s runtime verification remains deterministic.

End on the developer receipt, not the audit page.

## Rehearsal checklist

- Confirm the active Codex session can list at least one actual Rewind MCP tool; otherwise state the verified restart boundary and show CLI parity.
- Use a clean `/tmp/rewind-video` path.
- Keep the browser at 1280×720.
- Do not claim Windows support, multi-party non-repudiation, immutable local storage, universal filesystem mediation, or runtime AI.
- Run `/feedback` and paste the primary Session ID into `README.md` before submission.

