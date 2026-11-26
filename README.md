# herdr agent monitor

A herdr plugin that shows, in one pane, what every agent on your herdr server is doing and what it uses.

- **activity view**: status, goal (session title), last prompt, current action.
- **usage view** (`v`): model, context size and % of the window, input / cache / output tokens, estimated cost.

It reads `herdr agent list`, Claude Code transcripts (`~/.claude/projects`) and Codex logs (`~/.codex/sessions`).
Other agents (opencode, pi, OpenRouter CLIs) show the last lines of their screen and no token data.
It is read-only: it does not change agents, hooks or herdr settings.

## Requirements

- herdr (tested with 0.9.1), with its Claude Code / Codex integrations installed.
- `python3` (3.8 or later). No other packages.

## Install

```sh
herdr plugin link /path/to/herdr-agent-monitor
herdr plugin action list            # shows "Agent monitor"
```

Open it from the herdr action list, or run it in any pane:

```sh
python3 /path/to/herdr-agent-monitor/monitor.py
```

Remove: `herdr plugin unlink agent-monitor`.

## Use

| Input | Effect |
| --- | --- |
| click a row / `d` | full goal, prompt, action and token details (click again or `esc` to close) |
| drag a header `│` | resize a column (saved in `columns.json`) |
| `←` `→`, `+` `-`, `0`, space | pick a column, change its width, make it fill, hide or show it |
| `↑` `↓` / wheel | select |
| enter | jump to that agent's pane |
| `v` | switch between the activity and usage views |
| `q` | quit |

## Workspace colours

The PANE column uses the same colour per workspace as
[herdr-plugin-space-colors](https://github.com/ferretorres/herdr-plugin-space-colors).
If that plugin's config exists (`~/.config/herdr/plugins/config/ferretorres.space-colors/config.toml`, read with
Python 3.11+), its palettes, `[[workspaces]]` rules and `auto` setting apply. If not, its default palettes and its
auto hash apply (FNV-1a of the first pane's directory), so the colours stay the same if you install it later.
Workspaces that start in the same directory get the same colour. Per-pane `[[agents]]` rules are not applied.

## Costs

COST uses Anthropic first-party list prices for Claude models. Bedrock, Vertex or gateway billing differs, so treat
it as an estimate. To price other models, create `prices.json` next to `monitor.py` (USD per 1M tokens; the key
matches any model name that contains it):

```json
{"my-gateway-model": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "ctx": 258400}}
```

## Limits

- Codex sessions are matched by working directory, because herdr's Codex hook sends no session id.
  Two Codex agents in the same directory show the same session.
- Codex subagent tokens are not included; Claude subagent tokens are.

## Check

```sh
python3 test_monitor.py   # prints "ok"
```
