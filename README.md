# herdr agent monitor

A herdr plugin that shows, in one pane, what the agents on your herdr server are doing and what they use.

- **activity view**: status, goal, last prompt, current action.
- **usage view** (`v`): model, context size and % of the window, input / cache / output tokens, estimated cost.

Each refresh (every 2 s) reads one `herdr api snapshot`, Claude Code transcripts and Codex logs.
Other agents (opencode, pi, OpenRouter CLIs) show the last lines of their screen and no token data.
It is read-only: it does not change agents, hooks or herdr settings.

## Requirements

- herdr 0.9.5 or later, with its Claude Code / Codex integrations installed.
- `python3` 3.9 or later. No other packages. Linux or macOS (it uses curses).

## Install

```sh
herdr plugin link /path/to/agent-monitor
herdr plugin action list            # shows "Agent monitor" and "Agent monitor (this workspace)"
```

Open it from the herdr action list, or run it in any pane:

```sh
python3 /path/to/agent-monitor/monitor.py
```

Remove: `herdr plugin unlink agent-monitor`.

## Scope

`AGENT_MONITOR_SCOPE` picks which agents are shown:

| Value | Shows |
| --- | --- |
| `all` (default) | every agent on the server |
| `workspace` | the agents in the workspace this monitor pane is in now |
| a workspace id, e.g. `w2` | the agents in that workspace |

With `workspace`, the monitor asks herdr where its pane is on every refresh (`herdr pane current --current`).
If you move the pane to another workspace, the monitor follows it. If herdr cannot answer, it uses
`HERDR_WORKSPACE_ID`. The first line shows the scope, for example `workspace alpha (w1) · 3 agents`.

The "Agent monitor (this workspace)" action opens a scoped monitor split next to the focused pane of the
workspace. You can also start one yourself:

```sh
AGENT_MONITOR_SCOPE=workspace python3 /path/to/agent-monitor/monitor.py
herdr plugin pane open --plugin agent-monitor --entrypoint monitor --target-pane w1:p2 --direction right \
  --env AGENT_MONITOR_SCOPE=workspace
```

## Crews: leads and workers

When panes carry crew tokens (`herdr pane report-metadata <pane> --source crew --token ...`), the monitor
shows each lead with its workers under it:

| Token | On | Value |
| --- | --- | --- |
| `crew_role` | every crew pane | `lead` or `worker` |
| `crew_lead` | worker panes | the lead's pane id, e.g. `w1:p2` |
| `crew_name` | every crew pane | the crew's name |

```
ST PANE      AGENT        GOAL
●  w1:p2     claude       Lead: ship herdr-crew 0.2
○  └ w1:p3   alpha-tests  write monitor tests
●    └ w1:p4 opencode     sleep 600
```

A worker can lead its own workers. A worker whose lead is not shown (another workspace, or gone) is listed
at the top level. The PANE column widens to fit. The details panel (`d`) has a CREW line.

AGENT shows the agent's name when it has one (`herdr agent rename`), else its kind. GOAL shows the title set
with `herdr pane report-metadata <pane> --title ...`, else the terminal title.

## Use

| Input | Effect |
| --- | --- |
| click a row / `d` | full goal, prompt, action and token details (click again or `esc` to close) |
| drag a header `│` | resize a column |
| `←` `→`, `+` `-`, `0`, space | pick a column, change its width, make it fill, hide or show it |
| `↑` `↓` / wheel | select |
| enter | jump to that agent's pane |
| `v` | switch between the activity and usage views |
| `q` | quit |

Column widths are saved in `columns.json` in `HERDR_PLUGIN_STATE_DIR` (plugin panes), else in
`${XDG_STATE_HOME:-~/.local/state}/herdr/plugins/agent-monitor`.

If herdr fails (for example `server_not_running`), the bottom line shows the error in yellow and the last
rows stay on screen. `monitor.py --once` prints the error and exits with code 1.

## Workspace colours

The PANE column uses the workspace colour of
[herdr-plugin-space-colors](https://github.com/ferretorres/herdr-plugin-space-colors). That plugin writes a
token `sc_<palette>` on each pane, and the monitor uses it, so the colours match the sidebar.

Without the token (space-colors not installed), the monitor computes the colour the same way that plugin
would: its config rules if `~/.config/herdr/plugins/config/ferretorres.space-colors/config.toml` exists
(read with Python 3.11+), else its default palettes and its hash of the first pane's directory.
Custom palette names get a colour only when that config file can be read.

## Agent data

- Claude Code: the session's transcript. herdr gives either the transcript path or the session id; an id
  is looked up in `$CLAUDE_CONFIG_DIR/projects` and every `~/.claude*/projects` (for example `~/.claude-alt`).
  Subagent transcripts are added to the token totals.
- Codex: the rollout in `$CODEX_HOME/sessions` (default `~/.codex/sessions`) with the session id, else the
  newest rollout of the last 3 days with the same working directory.
- Other agents: `herdr agent read` in the background, one read per pane at a time. A pane is read again
  when its state changes or every 10 s, so one slow pane never delays the others.

## Costs

COST uses Anthropic first-party list prices for Claude models. Bedrock, Vertex or gateway billing differs, so treat
it as an estimate. To price other models, create `prices.json` next to `monitor.py` (USD per 1M tokens; the key
matches any model name that contains it):

```json
{"my-gateway-model": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "ctx": 258400}}
```

## Limits

- Without a Codex session id, Codex sessions are matched by working directory, so two Codex agents in the
  same directory show the same session.
- Codex subagent tokens are not included; Claude subagent tokens are.
- `crew_lead` holds a pane id, which changes when a pane moves to another workspace. The crew plugin
  rewrites the token after a move; other tools that set it have to do the same.

## Check

```sh
python3 test_monitor.py   # prints "ok" per group
```
