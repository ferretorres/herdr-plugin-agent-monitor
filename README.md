# herdr agent monitor

A [herdr](https://github.com/herdrdev/herdr) plugin that shows, in one pane, what every agent on your
herdr server is doing and what it uses. It answers "which agent is stuck, what is each one working on,
and how full is its context?" without switching panes.

```
agent monitor · all workspaces · 4 agents
ST PANE    AGENT        AGE   GOAL                               LAST PROMPT                              NOW
▲  w2:p1   codex              Migrate CI to the new runner                                                screen: Allow command: terraform plan? [y/…
●  w1:p1   lead-shop    0s    Lead: checkout refactor            Ship the checkout refactor: split the w… Bash: crew wait --timeout 300
●  └ w1:p3 shop-tests   0s    Tests for price rounding           Write tests for the new price rounding … Edit: tests/test_totals.py
○  └ w1:p4 shop-docs    0s    Checkout API docs                  Update the API docs for the checkout en… Read: docs/api/checkout.md
```

- **Activity view**: status, goal, last prompt and the action the agent is taking right now. Blocked agents
  sort first.
- **Usage view** (`v`): model, context size and % of the window, input / cache / output tokens, estimated cost.

```
agent monitor · all workspaces · 4 agents
ST PANE    AGENT        MODEL              CTX     CTX%  IN      CACHE R  CACHE W  OUT     COST     AGE   GOAL
▲  w2:p1   codex                                                                                          Migrate CI to the new runner
●  w1:p1   lead-shop    claude-opus-5-5    195k    20%   4k      182k     9k       3k      $0.16    0s    Lead: checkout refactor
●  └ w1:p3 shop-tests   claude-sonnet-5-5  70k     7%    2k      64k      4k       2k      $0.04    0s    Tests for price rounding
○  └ w1:p4 shop-docs    claude-haiku-5-5   24k     2%    900     21k      2k       800     $0.00    0s    Checkout API docs
Σ in 7k · cache r 267k · cache w 15k · out 6k · ≈ $0.20 list price
```

- **Per workspace**: scope it to one workspace, and agents that carry crew tokens show as a lead with its
  workers under it.

Each refresh (every 2 s) reads one `herdr api snapshot`, Claude Code transcripts and Codex logs.
Other agents (opencode, pi, OpenRouter CLIs) show the last lines of their screen and no token data.
It is read-only: it does not change agents, hooks or herdr settings.

## Requirements

- herdr 0.9.5 or later, with its Claude Code / Codex integrations installed.
- `python3` 3.9 or later. No other packages. Linux or macOS (it uses curses).

## Install

```sh
herdr plugin install ferretorres/herdr-plugin-agent-monitor
herdr plugin action list            # shows "Agent monitor" and "Agent monitor (this workspace)"
```

Open it from the herdr action list, or from a shell:

```sh
herdr plugin pane open --plugin ferretorres.agent-monitor --entrypoint monitor
```

Update: `herdr plugin update ferretorres.agent-monitor`. Remove: `herdr plugin uninstall ferretorres.agent-monitor`.

From a clone: `herdr plugin link /path/to/herdr-plugin-agent-monitor` (and `herdr plugin unlink ferretorres.agent-monitor`).

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
AGENT_MONITOR_SCOPE=workspace python3 /path/to/herdr-plugin-agent-monitor/monitor.py
herdr plugin pane open --plugin ferretorres.agent-monitor --entrypoint monitor --target-pane w1:p2 --direction right \
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
`${XDG_STATE_HOME:-~/.local/state}/herdr/plugins/ferretorres.agent-monitor`.

If herdr fails (for example `server_not_running`), the bottom line shows the error in yellow and the last
rows stay on screen. `monitor.py --once` prints the error and exits with code 1.

## Workspace colours

With [herdr-plugin-space-colors](https://github.com/ferretorres/herdr-plugin-space-colors) installed, the PANE
column takes each workspace's colour, so the rows match the sidebar. That plugin puts a token `sc_<palette>`
on each pane, and the monitor reads it; the accent of each palette comes from that plugin's config (read with
Python 3.11+, else its default accents). Without it, PANE is not coloured.

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
it as an estimate. To price other models, create `prices.json` in the plugin's config folder,
`herdr plugin config-dir ferretorres.agent-monitor` (usually `~/.config/herdr/plugins/config/ferretorres.agent-monitor`),
which `herdr plugin update` leaves alone. Prices are USD per 1M tokens, and a key matches any model name that
contains it:

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

## License

MIT. See [LICENSE](LICENSE).
