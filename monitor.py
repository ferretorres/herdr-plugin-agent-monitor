#!/usr/bin/env python3
"""Agent monitor for herdr: what every agent on this herdr server is doing, and what it costs.

Data: `herdr agent list` (all agents, all workspaces) + per agent:
  claude -> transcript (~/.claude/projects/*/<session>.jsonl, plus its subagents/)
  codex  -> newest rollout in ~/.codex/sessions with the same cwd
  other  -> last lines of the pane's screen (`herdr agent read`); no token data

Views (v to switch): activity (goal, last prompt, current action) and usage (context, tokens, cost).
Mouse: click a row for full details (click again to close), drag a header │ to resize,
       wheel to scroll. Keys: ↑↓ select · ⏎ jump to pane · v view · d details · esc close ·
       ←→ pick column · +/- width · 0 fill · space hide/show · q quit.
Column widths persist in columns.json; prices (USD per 1M tokens) in prices.json, both next to this file.
PANE is coloured per workspace like ferretorres/herdr-plugin-space-colors (its config if present).
Run `monitor.py --once [--usage]` for a single plain-text frame.
"""
import curses, glob, json, os, re, subprocess, sys, textwrap, threading, time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
GLYPH = {"working": "●", "blocked": "▲", "idle": "○", "done": "✓"}
# per view: [name, width]; width 0 = share the rest, -1 = hidden
VIEWS = {
    "activity": [["ST", 2], ["PANE", 7], ["AGENT", 8], ["AGE", 5], ["GOAL", 34], ["LAST PROMPT", 40], ["NOW", 0]],
    "usage": [["ST", 2], ["PANE", 7], ["AGENT", 8], ["MODEL", 18], ["CTX", 7], ["CTX%", 5], ["IN", 7],
              ["CACHE R", 8], ["CACHE W", 8], ["OUT", 7], ["COST", 8], ["AGE", 5], ["GOAL", 0]],
}
# Anthropic first-party list prices, USD per 1M tokens (claude-api skill, cached 2026-11-03).
# Bedrock / gateway billing differs, so COST is an estimate. Add other models in prices.json:
# {"my-gateway-model": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "ctx": 258400}}
PRICES = {
    "fable-5-2": {"in": 8.0, "out": 40.0, "cache_read": 0.20, "cache_write": 10.0, "ctx": 1_000_000},
    "opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0, "ctx": 1_000_000},
    "sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.10, "cache_write": 2.5, "ctx": 1_000_000},
    "haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125, "ctx": 1_000_000},
}


HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"  # plugin panes get it; GUI launches may lack PATH


class HerdrError(Exception):
    """herdr could not be run, exited non-zero, or answered with an error object."""


def error_text(out, err, rc):
    """'code: message' from herdr's JSON error (on stdout or stderr), else the raw text."""
    for text in (out, err):
        try:
            e = json.loads(text)["error"]
            return f"{e.get('code', 'error')}: {e.get('message', '')}".rstrip(": ")
        except (ValueError, KeyError, TypeError):
            pass
    return one_line(err or out, 160) or f"herdr exited with code {rc}"


def herdr(*args, timeout=5):
    """stdout of `herdr ARGS`; HerdrError when herdr fails, TimeoutExpired when it hangs."""
    try:
        r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=timeout)
    except OSError as e:
        raise HerdrError(f"cannot run {HERDR}: {e.strerror or e}")
    if r.returncode != 0:
        raise HerdrError(error_text(r.stdout, r.stderr, r.returncode))
    return r.stdout


def herdr_json(*args):
    """The `result` object of a herdr JSON command."""
    out = herdr(*args)
    try:
        obj = json.loads(out)
    except ValueError:
        raise HerdrError(f"herdr {' '.join(args[:2])}: unreadable answer {one_line(out, 60)!r}")
    if not isinstance(obj, dict) or "error" in obj:
        raise HerdrError(error_text(out, "", 0))
    return obj.get("result") or {}


def one_line(s, n=300):
    return re.sub(r"\s+", " ", s or "").strip()[:n]


def tail_lines(path, size=400_000):
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - size))
        data = f.read().decode("utf-8", "replace").splitlines()
    out = []
    for line in data:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass  # first line is usually cut by the seek
    return out


_new = {}  # path -> byte offset already read


def new_lines(path):
    """JSON objects appended to path since the last call (whole lines only)."""
    off = _new.get(path, 0)
    with open(path, "rb") as f:
        f.seek(off)
        data = f.read()
    end = data.rfind(b"\n") + 1
    _new[path] = off + end
    out = []
    for line in data[:end].splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def is_human(text):
    return text and not text.lstrip().startswith(
        ("<", "Caveat:", "[Request interrupted", "Base directory for this skill"))


def describe_tool(name, inp):
    if not isinstance(inp, dict):
        return f"{name}: {one_line(str(inp), 120)}"
    for key in ("command", "description", "file_path", "pattern", "prompt", "url", "query"):
        if key in inp:
            return f"{name}: {one_line(str(inp[key]), 120)}"
    return name


def claude_path(session_id):
    paths = glob.glob(f"{HOME}/.claude/projects/*/{session_id}.jsonl")
    return paths[0] if paths else None


def claude_activity(path):
    prompt = now = ""
    for o in tail_lines(path):
        msg = o.get("message") or {}
        content = msg.get("content")
        if o.get("type") == "user":
            texts = [content] if isinstance(content, str) else [
                c.get("text", "") for c in content or [] if c.get("type") == "text"]
            texts = [t for t in texts if is_human(t)]
            if texts:
                prompt = texts[-1]
        elif o.get("type") == "assistant" and isinstance(content, list):
            for c in content:
                if c.get("type") == "tool_use":
                    now = describe_tool(c.get("name", "tool"), c.get("input"))
                elif c.get("type") == "text" and c.get("text", "").strip():
                    now = "says: " + c["text"]
    return prompt, now, os.path.getmtime(path)


_cu = {}  # transcript path -> running usage


def claude_file_usage(path):
    """Sum usage over a Claude transcript. One API reply is written as several lines with the
    same message id, so count each id once, using the last line seen for it."""
    st = _cu.setdefault(path, {"tot": [0, 0, 0, 0], "id": None, "cur": [0, 0, 0, 0], "ctx": 0, "model": ""})
    for o in new_lines(path):
        msg = o.get("message") or {}
        u = msg.get("usage")
        if o.get("type") != "assistant" or not u:
            continue
        if msg.get("id") != st["id"]:
            st["tot"] = [a + b for a, b in zip(st["tot"], st["cur"])]
            st["id"] = msg.get("id")
        st["cur"] = [u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0),
                     u.get("cache_creation_input_tokens", 0), u.get("output_tokens", 0)]
        st["ctx"] = sum(st["cur"][:3])
        st["model"] = msg.get("model") or st["model"]
    return [a + b for a, b in zip(st["tot"], st["cur"])], st["ctx"], st["model"]


def claude_usage(path):
    tot, ctx, model = claude_file_usage(path)
    subs = glob.glob(os.path.join(path[:-len(".jsonl")], "subagents", "*.jsonl"))
    for p in subs:  # subagents bill tokens too, but don't use this session's context
        tot = [a + b for a, b in zip(tot, claude_file_usage(p)[0])]
    return {"in": tot[0], "cr": tot[1], "cw": tot[2], "out": tot[3], "ctx": ctx, "model": model,
            "window": None, "subagents": len(subs)}


_codex_cwd = {}  # rollout path -> cwd (session_meta is the first line, never changes)


def codex_path(cwd):
    # ponytail: herdr's codex hook sends no session id, so match by cwd + newest file;
    # two codex agents in one directory would show the same session.
    best = None
    for p in glob.glob(f"{HOME}/.codex/sessions/*/*/*/rollout-*.jsonl"):
        if time.time() - os.path.getmtime(p) > 3 * 86400:
            continue
        if p not in _codex_cwd:
            with open(p) as f:
                try:
                    _codex_cwd[p] = json.loads(f.readline()).get("payload", {}).get("cwd")
                except ValueError:
                    _codex_cwd[p] = None
        if _codex_cwd[p] == cwd and (best is None or os.path.getmtime(p) > os.path.getmtime(best)):
            best = p
    return best


def codex_activity(path):
    prompt = now = ""
    for o in tail_lines(path):
        p = o.get("payload") or {}
        kind = p.get("type")
        if o.get("type") != "response_item":
            continue
        if kind == "message":
            text = " ".join(c.get("text", "") for c in p.get("content") or [])
            if p.get("role") == "user" and is_human(text):
                prompt = text
            elif p.get("role") == "assistant" and text.strip():
                now = "says: " + text
        elif kind in ("function_call", "custom_tool_call"):
            args = p.get("arguments") or p.get("input") or ""
            try:
                args = json.loads(args)
            except (TypeError, ValueError):
                pass
            now = describe_tool(p.get("name", "tool"), args)
    return prompt, now, os.path.getmtime(path)


_xu = {}  # rollout path -> latest token_count info + model


def codex_usage(path):
    st = _xu.setdefault(path, {"info": None, "model": ""})
    for o in new_lines(path):
        p = o.get("payload") or {}
        if p.get("type") == "token_count" and p.get("info"):
            st["info"] = p["info"]
        elif o.get("type") == "turn_context":
            st["model"] = p.get("model") or st["model"]
    info = st["info"] or {}
    t, last = info.get("total_token_usage") or {}, info.get("last_token_usage") or {}
    cached, cw = t.get("cached_input_tokens", 0), t.get("cache_write_input_tokens", 0)
    return {"in": max(0, t.get("input_tokens", 0) - cached - cw),  # codex input includes cached
            "cr": cached, "cw": cw, "out": t.get("output_tokens", 0),
            "ctx": last.get("input_tokens", 0), "model": st["model"],
            "window": info.get("model_context_window"), "subagents": 0}


def screen_activity(pane_id):
    try:
        lines = herdr("agent", "read", pane_id, "--source", "recent", "--lines", "40").splitlines()
    except subprocess.TimeoutExpired:  # herdr can be slow to snapshot a busy pane; skip it this round
        return "", "screen: (herdr did not answer in 5 s)", None
    except HerdrError as e:
        return "", f"screen: ({e})", None
    lines = [l.strip() for l in lines if sum(ch.isalnum() for ch in l) > 5]  # drop borders
    return "", "screen: " + " ⏎ ".join(lines[-2:]), None


def load_prices():
    prices = dict(PRICES)
    try:
        prices.update(json.load(open(os.path.join(HERE, "prices.json"))))
    except (OSError, ValueError):
        pass
    return prices


def price_for(model, prices):
    return next((p for key, p in prices.items() if key in (model or "")), None)


def cost(u, price):
    if not price:
        return None
    return (u["in"] * price["in"] + u["cr"] * price["cache_read"] + u["cw"] * price["cache_write"]
            + u["out"] * price["out"]) / 1e6


def num(n):
    if not n:
        return ""
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)


def ago(ts):
    if not ts:
        return ""
    s = int(time.time() - ts)
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h" if s < 86400 else f"{s // 86400}d"


# Workspace colours, the same as ferretorres/herdr-plugin-space-colors (v0.3.3, main.rs choose_palette):
# its config rules first, else FNV-1a of the workspace's first pane cwd onto the palettes sorted by name.
# Without that plugin's config, its default palettes and auto hash apply, so colours match once installed.
SPACE_CONFIG = f"{HOME}/.config/herdr/plugins/config/ferretorres.space-colors/config.toml"
SPACE_ACCENTS = {"blue": "#89b4fa", "green": "#a6e3a1", "mauve": "#cba6f7", "peach": "#fab387",
                 "pink": "#f5c2e7", "red": "#f38ba8", "teal": "#94e2d5", "yellow": "#f9e2af"}


def load_space_colors():
    """(accent hex by palette name, workspace rules, auto) from the space-colors config, else defaults."""
    try:
        import tomllib  # Python 3.11+
        with open(SPACE_CONFIG, "rb") as f:
            cfg = tomllib.load(f)
    except (ImportError, OSError, ValueError):
        return dict(SPACE_ACCENTS), [], True
    accents = {name: p["accent"] for name, p in cfg.get("palettes", {}).items() if p.get("accent")}
    return accents or dict(SPACE_ACCENTS), cfg.get("workspaces", []), cfg.get("auto", True)


def fnv1a(text):
    h = 0xcbf29ce484222325
    for b in text.encode():
        h = ((h ^ b) * 0x100000001b3) % 2**64
    return h


def choose_palette(label, cwd, accents, rules, auto):
    best, score = None, -1
    for r in rules:
        if r.get("palette") not in accents:
            continue
        if r.get("label") and r["label"] == label:
            return r["palette"]
        path = os.path.expanduser(r.get("path", "")).rstrip("/")
        if path and (cwd == path or cwd.startswith(path + "/")) and len(path) > score:
            best, score = r["palette"], len(path)
    if best or not auto:
        return best
    names = sorted(accents)
    return names[fnv1a(cwd or label) % len(names)]
    # ponytail: the plugin's per-pane [[agents]] overrides are not applied; add if someone uses them


def workspace_palettes(panes, spaces, accents, rules, auto):
    """Fallback when space-colors is not installed: its hash, from the first non-empty pane cwd."""
    first_cwd = {}
    for p in panes:
        if p.get("cwd"):
            first_cwd.setdefault(p.get("workspace_id"), p["cwd"])
    return {w["workspace_id"]: choose_palette(w.get("label", ""), first_cwd.get(w["workspace_id"], ""),
                                              accents, rules, auto) for w in spaces}


def sc_palette(*token_maps):
    """Palette name from the space-colors token (sc_<palette> = "●"), first map that has one."""
    for tokens in token_maps:
        name = next((k[3:] for k in sorted(tokens or {}) if k.startswith("sc_") and len(k) > 3), None)
        if name:
            return name
    return None


def snapshot():
    """agents, panes, workspaces and tabs in one herdr call."""
    return herdr_json("api", "snapshot").get("snapshot") or {}


def scope_workspace():
    """None = every workspace, else the workspace id to show. AGENT_MONITOR_SCOPE is
    all (default) | workspace | <workspace id>. `workspace` asks herdr where this pane is now,
    so a monitor pane moved to another workspace follows it; HERDR_WORKSPACE_ID is the fallback."""
    scope = os.environ.get("AGENT_MONITOR_SCOPE", "").strip() or "all"
    if scope == "all":
        return None
    if scope != "workspace":
        return scope
    try:
        ws = (herdr_json("pane", "current", "--current").get("pane") or {}).get("workspace_id")
    except (HerdrError, subprocess.TimeoutExpired):
        ws = None
    return ws or os.environ.get("HERDR_WORKSPACE_ID") or None


def scope_label(ws, snap):
    if ws is None:
        return "all workspaces"
    w = next((w for w in snap.get("workspaces") or [] if w.get("workspace_id") == ws), None)
    if w is None:
        return f"workspace {ws} (not found)"
    return f"workspace {w.get('label') or ws} ({ws})" if w.get("label") not in (None, "", ws) else f"workspace {ws}"


def collect():
    """(rows, scope label) for one refresh."""
    ws = scope_workspace()
    snap = snapshot()
    return build_rows(snap, ws), scope_label(ws, snap)


def build_rows(snap, ws=None):
    agents = [a for a in snap.get("agents") or [] if ws is None or a.get("workspace_id") == ws]
    panes = {p.get("pane_id"): p for p in snap.get("panes") or []}
    spaces = {w.get("workspace_id"): w for w in snap.get("workspaces") or []}
    hashed = None  # space-colors' own choice is on every pane as a token; hash only without it
    prices = load_prices()
    rows = []
    for a in agents:
        sess = (a.get("agent_session") or {}).get("value")
        path = usage = None
        if a["agent"] == "claude" and sess and (path := claude_path(sess)):
            prompt, now, ts = claude_activity(path)
            usage = claude_usage(path)
        elif a["agent"] == "codex" and (path := codex_path(a.get("cwd"))):
            prompt, now, ts = codex_activity(path)
            usage = codex_usage(path)
        else:
            prompt, now, ts = screen_activity(a["pane_id"])
        price = price_for(usage and usage["model"], prices)
        window = (usage or {}).get("window") or (price or {}).get("ctx")
        usd = cost(usage, price) if usage else None
        pct = 100 * usage["ctx"] / window if usage and window and usage["ctx"] else None
        u = usage or {}
        goal = a.get("terminal_title_stripped") or ""
        palette = sc_palette(a.get("tokens"), (panes.get(a["pane_id"]) or {}).get("tokens"),
                             (spaces.get(a.get("workspace_id")) or {}).get("tokens"))
        if not palette:
            if hashed is None:
                hashed = workspace_palettes(panes.values(), spaces.values(), *load_space_colors())
            palette = hashed.get(a.get("workspace_id"))
        rows.append({
            "pane": a["pane_id"], "agent": a["agent"], "status": a["agent_status"], "ts": ts,
            "space": palette,
            "pct": pct, "usd": usd, "usage": usage,
            "cells": {
                "ST": GLYPH.get(a["agent_status"], "?"), "PANE": a["pane_id"], "AGENT": a["agent"],
                "AGE": ago(ts), "GOAL": one_line(goal), "LAST PROMPT": one_line(prompt), "NOW": one_line(now),
                "MODEL": u.get("model", ""), "CTX": num(u.get("ctx")),
                "CTX%": f"{pct:.0f}%" if pct is not None else "",
                "IN": num(u.get("in")), "CACHE R": num(u.get("cr")), "CACHE W": num(u.get("cw")),
                "OUT": num(u.get("out")),
                "COST": f"${usd:.2f}" if usd is not None else ("?" if usage else ""),
            },
            "full": [("GOAL", goal), ("LAST PROMPT", prompt or ""), ("NOW", now or ""),
                     ("USAGE", usage_text(usage, window, usd)),
                     ("WHERE", f'{a["pane_id"]} · {a.get("cwd", "")}' + (f" · {path}" if path else ""))],
        })
    order = {"blocked": 0, "working": 1, "idle": 2, "done": 3}
    rows.sort(key=lambda r: (order.get(r["status"], 4), r["pane"]))
    return rows


def usage_text(u, window, usd):
    if not u:
        return "no token data for this agent (only Claude and Codex logs are read)"
    ctx = f"context {u['ctx']:,}" + (f" of {window:,} ({100 * u['ctx'] / window:.0f}%)" if window else "")
    money = f"≈ ${usd:.2f} at list price" if usd is not None else f"no price for {u['model']!r} (add it to prices.json)"
    subs = f" · includes {u['subagents']} subagent transcripts" if u["subagents"] else ""
    return (f"{u['model']} · {ctx}\ninput {u['in']:,} · cache read {u['cr']:,} · cache write {u['cw']:,}"
            f" · output {u['out']:,}{subs}\n{money}")


def totals(rows):
    us = [r["usage"] for r in rows if r["usage"]]
    t = {k: sum(u[k] for u in us) for k in ("in", "cr", "cw", "out")}
    usd = sum(r["usd"] for r in rows if r["usd"] is not None)
    unpriced = sum(1 for r in rows if r["usage"] and r["usd"] is None)
    return (f"Σ in {num(t['in']) or 0} · cache r {num(t['cr']) or 0} · cache w {num(t['cw']) or 0}"
            f" · out {num(t['out']) or 0} · ≈ ${usd:.2f} list price" + (f" (+{unpriced} unpriced)" if unpriced else ""))


def fit(text, w):
    return text[:w - 1] + "…" if len(text) > w else text.ljust(w)


def layout(cols, width):
    """[(col index, x, width)] for shown columns; width 0 = share the rest, -1 = hidden."""
    shown = [i for i, c in enumerate(cols) if c[1] != -1]
    fixed = sum(cols[i][1] + 1 for i in shown if cols[i][1] > 0)
    fill = [i for i in shown if cols[i][1] == 0]
    rest = max(8, (width - fixed) // max(1, len(fill)) - 1)
    out, x = [], 0
    for i in shown:
        w = cols[i][1] if cols[i][1] > 0 else rest
        out.append((i, x, w))
        x += w + 1
    return out


def frame(rows, cols, width):
    spec = layout(cols, width)
    lines = [" ".join(fit(cols[i][0], w) for i, _, w in spec)]
    for r in rows:
        lines.append(" ".join(fit(r["cells"][cols[i][0]], w) for i, _, w in spec))
    return [l[:width] for l in lines]


SETTINGS = os.path.join(HERE, "columns.json")


def load_cols():
    try:
        saved = json.load(open(SETTINGS))
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}  # older single-view format
    return {v: saved[v] if [c[0] for c in saved.get(v, [])] == [c[0] for c in d] else [c[:] for c in d]
            for v, d in VIEWS.items()}


def save_cols(colsets):
    with open(SETTINGS, "w") as f:
        json.dump(colsets, f)


def parse_mouse(seq):
    """SGR mouse report '[<b;x;yM' -> (button, x, y, pressed) with 0-based x, y."""
    m = re.fullmatch(r"\[<(\d+);(\d+);(\d+)([Mm])", seq)
    if not m:
        return None
    return int(m[1]), int(m[2]) - 1, int(m[3]) - 1, m[4] == "M"


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    big = curses.COLORS >= 256
    for n, (c256, c8) in enumerate([(41, curses.COLOR_GREEN), (196, curses.COLOR_RED),
                                    (33, curses.COLOR_BLUE), (208, curses.COLOR_YELLOW),
                                    (45, curses.COLOR_CYAN), (171, curses.COLOR_MAGENTA),
                                    (75, curses.COLOR_BLUE), (244, curses.COLOR_WHITE),
                                    (220, curses.COLOR_YELLOW)], 1):
        curses.init_pair(n, c256 if big else c8, -1)
    P = curses.color_pair
    return {
        "status": {"working": P(1) | curses.A_BOLD, "blocked": P(2) | curses.A_BOLD,
                   "done": P(3), "idle": P(8)},
        "agent": {"claude": P(4), "codex": P(5), "opencode": P(6), "pi": P(7)},
        "tool": P(5), "says": 0, "screen": P(8), "dim": P(8), "fresh": P(1),
        "ok": P(1), "warn": P(9), "bad": P(2) | curses.A_BOLD,
        "space": space_pairs(big),
    }


def nearest_256(hex_colour):
    """xterm-256 index closest to #rrggbb (6x6x6 cube or grey ramp)."""
    rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    steps = [0, 95, 135, 175, 215, 255]
    cube = [min(range(6), key=lambda i: abs(steps[i] - c)) for c in rgb]
    grey = min(range(24), key=lambda i: abs(8 + 10 * i - sum(rgb) / 3))
    d = lambda a: sum((x - y) ** 2 for x, y in zip(a, rgb))
    if d([8 + 10 * grey] * 3) < d([steps[i] for i in cube]):
        return 232 + grey
    return 16 + 36 * cube[0] + 6 * cube[1] + cube[2]


def space_pairs(big):
    if not big:
        return {}
    pairs = {}
    for n, (name, accent) in enumerate(sorted(load_space_colors()[0].items()), 20):
        curses.init_pair(n, nearest_256(accent), -1)
        pairs[name] = curses.color_pair(n) | curses.A_BOLD
    return pairs


def cell_attr(C, row, name):
    if name == "PANE":  # workspace colour, as in the herdr sidebar with space-colors
        return C["space"].get(row["space"], 0)
    if name == "ST":
        return C["status"].get(row["status"], 0)
    if name == "AGENT":
        return C["agent"].get(row["agent"], 0)
    if name == "AGE" and row["ts"]:
        age = time.time() - row["ts"]
        return C["fresh"] if age < 60 else C["dim"] if age > 3600 else 0
    if name == "NOW":
        text = row["cells"][name]
        return C["says"] if text.startswith("says:") else C["screen"] if text.startswith("screen:") else C["tool"]
    if name in ("CTX", "CTX%") and row["pct"] is not None:  # how close to a full context window
        return C["bad"] if row["pct"] >= 80 else C["warn"] if row["pct"] >= 50 else C["ok"]
    if name == "COST" and row["usd"] is not None:
        return C["bad"] if row["usd"] >= 50 else C["warn"] if row["usd"] >= 10 else 0
    if row["status"] == "idle" and name != "GOAL":
        return C["dim"]
    return 0


def draw_detail(scr, row, top, h, w, C):
    scr.hline(top, 0, curses.ACS_HLINE, w - 1)
    scr.addnstr(top, 2, f" {row['pane']} · {row['agent']} · {row['status']} — esc close, ⏎ jump ", w - 4, curses.A_BOLD)
    y = top + 1
    for label, text in row["full"]:
        if y >= h - 1:
            break
        scr.addnstr(y, 0, label, w - 1, curses.A_BOLD | C["agent"].get(row["agent"], 0))
        y += 1
        for para in (text or "—").splitlines() or ["—"]:
            for line in textwrap.wrap(para, w - 3) or [""]:
                if y >= h - 1:
                    return
                scr.addnstr(y, 2, line, w - 3)
                y += 1


def refresh_forever(state, every=2.0):
    """Background thread: collect() off the UI thread, so a slow herdr never freezes keys or mouse.
    The UI only reads state["data"]; one assignment of a tuple, so no lock is needed."""
    while True:
        refresh_once(state)
        time.sleep(every)


def refresh_once(state):
    try:
        rows, state["scope"] = collect()
        state["data"] = (rows, "")
    except subprocess.TimeoutExpired as e:  # keep the last rows until herdr answers again
        state["data"] = (state["data"][0], f" ⚠ herdr did not answer: {' '.join(e.cmd[1:3])} ·")
    except HerdrError as e:  # e.g. server_not_running: say so instead of showing 0 agents
        state["data"] = (state["data"][0], f" ⚠ herdr: {e} ·")


def tui(scr):
    curses.curs_set(0)
    C = init_colors()
    scr.keypad(True)
    scr.timeout(250)  # redraw soon after the background refresh lands
    colsets = load_cols()
    view = "activity"
    saved = {}  # (view, column) -> width before hiding
    sel = picked = 0
    detail = False
    drag = None  # column index being resized with the mouse
    state = {"data": ([], " loading… ·"), "scope": "…"}
    threading.Thread(target=refresh_forever, args=(state,), daemon=True).start()
    while True:
        cols = colsets[view]
        rows, warn = state["data"]
        h, w = scr.getmaxyx()
        sel = min(sel, max(0, len(rows) - 1))
        picked = min(picked, len(cols) - 1)
        # rows: 0 title (scope), 1 column header, 2.. agents, then [usage totals], status line
        body = h - 4 if view == "usage" else h - 3
        list_h = body // 2 if detail and rows else body
        scr.erase()
        scr.addnstr(0, 0, f" agent monitor · {state['scope']} · {len(rows)} agents", w - 1, curses.A_BOLD)
        spec = layout(cols, w - 1)
        for i, x, cw in spec:  # header: picked column underlined, edges are drag handles
            if x < w - 1:
                attr = curses.A_BOLD | (curses.A_UNDERLINE if i == picked else 0)
                scr.addnstr(1, x, fit(cols[i][0], cw), w - 1 - x, attr)
                if x + cw < w - 1:
                    scr.addstr(1, x + cw, "│", C["dim"])
        first = max(0, sel - list_h + 1)
        for n, r in enumerate(rows[first:first + list_h]):
            y, rev = n + 2, curses.A_REVERSE if first + n == sel else 0
            for i, x, cw in spec:
                if x < w - 1:
                    name = cols[i][0]
                    scr.addnstr(y, x, fit(r["cells"][name], cw) + " ", w - 1 - x, cell_attr(C, r, name) | rev)
        if not rows and not warn and list_h > 0:
            scr.addnstr(2, 1, f"no agents in {state['scope']}", w - 2, C["dim"])
        if detail and rows:
            draw_detail(scr, rows[sel], list_h + 2, body + 3, w, C)
        if view == "usage":
            scr.addnstr(h - 2, 0, totals(rows), w - 1, curses.A_BOLD)
        hidden = ",".join(c[0] for c in cols if c[1] == -1)
        help_ = (f" [{view}] v: switch view · click row: details · drag │: width"
                 f" · ⏎ jump · ←→ +/- space: column · q quit")
        scr.addnstr(h - 1, 0, warn + help_ + (f" · hidden: {hidden}" if hidden else ""), w - 1,
                    C["warn"] if warn else C["dim"])
        k = scr.getch()
        if k == 27:  # Esc alone, or the start of a mouse report
            scr.nodelay(True)
            seq = ""
            while (c := scr.getch()) != -1:
                seq += chr(c)
                if chr(c) in "Mm" and seq.startswith("[<"):
                    break
            scr.timeout(250)  # redraw soon after the background refresh lands
            mouse = parse_mouse(seq) if seq else None
            if not seq:
                detail = False
            elif mouse:
                b, mx, my, down = mouse
                if b in (64, 65):  # wheel
                    sel = max(0, sel + (1 if b == 65 else -1))
                elif drag is not None and not down:  # release ends the resize
                    drag = None
                    save_cols(colsets)
                elif drag is not None and b in (0, 32):  # dragging a column edge
                    start = next(x for i, x, _ in spec if i == drag)
                    cols[drag][1] = max(2, mx - start)
                elif b == 0 and down and my == 1:
                    edge = [i for i, x, cw in spec if abs(mx - (x + cw)) <= 1]
                    if edge:
                        drag = picked = edge[0]
                        if cols[drag][1] == 0:  # fill column becomes fixed when dragged
                            cols[drag][1] = next(cw for i, _, cw in spec if i == drag)
                    else:
                        picked = next((i for i, x, cw in spec if x <= mx < x + cw), picked)
                elif b == 0 and down and 2 <= my < 2 + list_h and first + my - 2 < len(rows):
                    clicked = first + my - 2
                    detail = not (detail and clicked == sel)  # click again to close
                    sel = clicked
            continue
        if k == ord("q"):
            return
        if k == ord("v"):
            view = "usage" if view == "activity" else "activity"
        elif k in (curses.KEY_DOWN, ord("j")):
            sel += 1
        elif k in (curses.KEY_UP, ord("k")):
            sel = max(0, sel - 1)
        elif k in (ord("d"), 9):  # d or tab: toggle details
            detail = not detail
        elif k in (curses.KEY_RIGHT, curses.KEY_LEFT):
            picked = (picked + (1 if k == curses.KEY_RIGHT else -1)) % len(cols)
        elif k in (ord("+"), ord("=")) and cols[picked][1] >= 0:
            cols[picked][1] = (cols[picked][1] or 20) + 4
            save_cols(colsets)
        elif k == ord("-") and cols[picked][1] > 0:
            cols[picked][1] = max(2, cols[picked][1] - 4)
            save_cols(colsets)
        elif k == ord("0") and cols[picked][1] > 0:  # back to "fill the rest"
            cols[picked][1] = 0
            save_cols(colsets)
        elif k == ord(" "):
            if cols[picked][1] == -1:
                cols[picked][1] = saved.pop((view, picked), 10)
            else:
                saved[(view, picked)], cols[picked][1] = cols[picked][1], -1
            save_cols(colsets)
        elif k in (10, 13, curses.KEY_ENTER) and rows:
            try:
                herdr("agent", "focus", rows[sel]["pane"])
            except (HerdrError, subprocess.TimeoutExpired):
                pass  # the pane is gone; the next refresh drops its row


def once(argv):
    """Print one plain frame; exit code 1 with a warning when herdr fails or hangs."""
    width = os.get_terminal_size().columns if sys.stdout.isatty() else 200
    view = "usage" if "--usage" in argv else "activity"
    try:
        rows, scope = collect()
    except subprocess.TimeoutExpired as e:
        print(f"⚠ herdr did not answer: {' '.join(e.cmd[1:3])}", file=sys.stderr)
        return 1
    except HerdrError as e:
        print(f"⚠ herdr: {e}", file=sys.stderr)
        return 1
    print(f"agent monitor · {scope} · {len(rows)} agents"[:width])
    print("\n".join(frame(rows, VIEWS[view], width)))
    if view == "usage":
        print(totals(rows))
    return 0


if __name__ == "__main__":
    if "--once" in sys.argv:
        sys.exit(once(sys.argv))
    else:
        os.environ.setdefault("ESCDELAY", "25")  # Esc closes details without a 1 s lag
        sys.stdout.write("\033[?1002h\033[?1006h")  # mouse press/drag/release, SGR coordinates
        sys.stdout.flush()
        try:
            curses.wrapper(tui)
        finally:  # after curses has restored the terminal, so the shell never gets mouse codes
            sys.stdout.write("\033[?1006l\033[?1002l")
            sys.stdout.flush()
