# Codex Standard Agents Starter

A deliberately simple multi-agent setup for local Codex / Codex Desktop.

This starter gives you:

- a normal primary Codex agent (whatever model you select in the app)
- Luna as the default subagent model
- four focused subagent roles:
  - `explorer`
  - `tester`
  - `reviewer`
  - `implementer`
- lightweight instructions in `AGENTS.md`
- no blackboard, database, MCP server, or shared wiki

## Install

Copy both `AGENTS.md` and the `.codex` directory into the root of your repository:

```text
your-project/
├── AGENTS.md
├── .codex/
│   ├── config.toml
│   └── agents/
│       ├── explorer.toml
│       ├── implementer.toml
│       ├── reviewer.toml
│       └── tester.toml
└── ...
```

Then open that repository in Codex Desktop.

Current Codex releases have subagent workflows enabled by default. This starter also
sets them explicitly and caps spawned subagents at four concurrent threads.

## First thing to try

In a normal Codex chat, paste:

> Investigate this task before changing code. Delegate independent parts to the
> explorer, tester, and reviewer subagents in parallel. Wait for their results,
> synthesize them, and then propose the smallest implementation plan. Do not edit
> anything until the investigation is complete.

Then describe your issue.

For a straightforward implementation:

> Use an explorer subagent to identify the relevant code and a tester subagent to
> identify the right verification. Then implement the change. Afterward, have the
> reviewer inspect the diff for correctness and regressions.

For a bug with several plausible causes:

> Spawn separate explorer subagents for each plausible hypothesis. Keep them
> read-only. Compare their evidence before making a change.

## How the roles are intended to work

### explorer
Cheap, read-only repository investigation. It should find files, symbols,
execution paths, and evidence without editing anything.

### tester
Figures out how to reproduce and verify behavior. It is allowed workspace access
so that real test suites can run, but its instructions tell it not to modify
production code.

### reviewer
Read-only review of a proposed change or existing code. It prioritizes concrete
bugs and regressions instead of style commentary.

### implementer
A bounded coding worker. Use it when a piece of implementation is sufficiently
isolated that delegating the edit makes sense.

## Cost strategy

The primary/lead model is intentionally NOT pinned in this repository. Choose the
lead model in Codex Desktop.

A useful starting pattern is:

- Luna subagents for exploration, testing, and review
- Sol as the normal lead for nontrivial work
- Astra only when the problem genuinely needs it

The project config makes Luna the default spawned-agent model. Individual agent
profiles also pin Luna, so the cheap-worker behavior is explicit.

## Important limitation

More agents do not automatically make a task better. Parallelize work that is
actually independent. Avoid having several implementation agents edit the same
code simultaneously unless the work is cleanly separated.

Start with 2–3 subagents. Increase concurrency only when the task naturally
decomposes.
