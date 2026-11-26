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

- **In the sidebar**: an optional extra line under an agent once its context or session cost passes a
  threshold, and a toast when an agent's context passes 90%, both kept current even with no monitor pane open.

Each refresh (every 2 s) reads one `herdr api snapshot` and the agents' own session files: Claude Code
transcripts, Codex rollouts, pi sessions and opencode's database. Other agents (OpenRouter CLIs and the like)
show the last lines of their screen and no token data.

It only reads agents' data. The only things it writes are its own `am_*` sidebar tokens (source
`ferretorres.agent-monitor`) and the context toast. It never changes agents, state labels, hooks or herdr
settings.

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
- pi: the session file herdr's pi integration reports (or `<id>.jsonl` under `$PI_CODING_AGENT_SESSION_DIR`
  or `${PI_CODING_AGENT_DIR:-~/.pi/agent}/sessions`). Totals count what pi's footer counts, subagent runs
  included. The context window comes from pi's `models-store.json`, and the cost is the one pi recorded.
  pi writes the file after its first reply, so until then the row shows the screen.
- opencode: its SQLite database, `${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db` (or
  `$OPENCODE_DB`), opened read-only on each refresh and closed at once. The session is herdr's session id,
  else the newest session in the pane's folder. Totals include subagent sessions, OUT includes reasoning,
  and the cost is opencode's own estimate. Newer opencode database layouts are not read yet; those rows
  show the screen.
- Other agents: `herdr agent read` in the background, one read per pane at a time. A pane is read again
  when its state changes or every 10 s, so one slow pane never delays the others.

## Sidebar and toast

The monitor puts one short line under an agent in herdr's sidebar when its context or session cost passes
a threshold, and nothing otherwise:

```
billing
  ▲ ★ lead · claude
    91% $159          red: 80% context or $50 and up
  ● docs · pi
    54% $3.4          yellow: 50% context or $10 and up
  ○ review · opencode
```

It writes one of the tokens `am_bad`, `am_warn` (or `am_ok`, see below); add this row to
`[ui.sidebar.agents] rows` in herdr's `config.toml` to show them (the
[herdr-crew](https://github.com/ferretorres/herdr-crew) setup adds it for you):

```toml
[{ token = "$am_bad", fg = "#f38ba8", bold = true }, { token = "$am_warn", fg = "#f9e2af" }, { token = "$am_ok", fg = "#6c7086" }],
```

When an agent's context passes 90%, a toast says so once (`lead context 91%`, with its workspace, tab and
pane), and again only after the context drops below 50% and climbs back. Herdr shows toasts only with
`[ui.toast] delivery = "herdr"` (or `terminal` / `system`) in its config. A toast herdr declines (toasts
off, or no client attached) is tried again every minute while the context stays above 90%. Herdr already notifies you when an
agent finishes or needs input, so the monitor does not repeat those.

Both stay current without a monitor pane: a hook runs on every agent status change and updates that one
agent. An open monitor also updates every agent it shows on each refresh. Tokens do not survive a herdr
restart; they come back on each agent's next status change.

| Setting | Values |
| --- | --- |
| `AGENT_MONITOR_SIDEBAR` | `warn` (default): a line only from 50% / $10 · `all`: a dim line for every agent with numbers · `off`: clear the line and write nothing |
| `AGENT_MONITOR_NOTIFY` | `1` (default) · `0`: no context toast |

The hook only sees variables set in the herdr server's environment.

## Costs

COST uses Anthropic first-party list prices for Claude models, with 1-hour cache writes priced at 2× input and
5-minute ones at 1.25× (the details panel shows the split). pi and opencode rows use the cost those agents
recorded; when one recorded none ($0), the list price applies. Bedrock, Vertex or gateway billing differs, so treat
it as an estimate. To price other models, create `prices.json` in the plugin's config folder,
`herdr plugin config-dir ferretorres.agent-monitor` (usually `~/.config/herdr/plugins/config/ferretorres.agent-monitor`),
which `herdr plugin update` leaves alone. Prices are USD per 1M tokens, and a key matches any model name that
contains it:

```json
{"my-gateway-model": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cache_write_1h": 0, "ctx": 258400}}
```

The longest matching key wins. An entry for a built-in model only changes the keys it names; missing cache
prices default to 0.1× (read), 1.25× (5-minute write) and 2× (1-hour write) the input price. Values that are
not numbers are ignored.

## Limits

- Without a Codex session id, Codex sessions are matched by working directory, so two Codex agents in the
  same directory show the same session.
- Codex subagent tokens are not included; Claude, pi and opencode subagent tokens are.
- Costs are priced on session totals, so Claude's per-request long-context tier, fast mode and regional
  multipliers are not modelled. OpenAI models have no built-in prices; add them in `prices.json`.
- `crew_lead` holds a pane id, which changes when a pane moves to another workspace. The crew plugin
  rewrites the token after a move; other tools that set it have to do the same.

## Check

```sh
python3 test_monitor.py   # prints "ok" per group
```

## License

MIT. See [LICENSE](LICENSE).
