# Rewind workflow

This repository uses Rewind to record meaningful agent work.

- Before meaningful work, run `rewind status`. Start a task with a narrow intent and narrow `--allow` paths if none is active.
- Record a checkpoint at meaningful transitions.
- Run important checks through `rewind run -- <command>` so evidence is bound to exact Git state.
- Run `rewind finish` before presenting completion, then inspect `rewind receipt`.
- Never approve your own protected action. `rewind approve` is reserved for a human at the CLI.
- Never use recovery to reset, clean, stash, overwrite, or switch the developer's working tree. Rewind recovery only creates a branch.
- The Python core and CLI are authoritative. MCP tools are ergonomic adapters with CLI parity.

During bootstrap only, Rewind cannot govern its own installation. The first commit records that explicit exception; all subsequent milestones should use the recorder.

