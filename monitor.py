#!/usr/bin/env python3
"""Agent monitor for herdr: what the agents on this herdr server are doing, and what they cost.

Data: one `herdr api snapshot` per refresh (agents, panes, workspaces) + per agent:
  claude -> transcript (agent_session path, or <id>.jsonl under $CLAUDE_CONFIG_DIR/projects and
            ~/.claude*/projects), plus its subagents/
  codex  -> rollout in $CODEX_HOME/sessions by session id, else the newest one with the same cwd
  pi     -> the session file herdr reports (or <id>.jsonl under pi's sessions dir)
  opencode -> its SQLite database, read-only, by herdr's session id or the pane's folder
  other  -> last lines of the pane's screen (`herdr agent read`, in the background); no token data
Writes: only its own am_* sidebar tokens and a context-90% toast (publish), from the open monitor and
from the pane.agent_status_changed hook (`monitor.py --hook`).
Scope: AGENT_MONITOR_SCOPE = all (default) | workspace (this pane's live workspace) | <workspace id>.
Crew tokens on panes order the rows: a lead (crew_role=lead), then its workers (crew_lead=<lead pane>).

Views (v to switch): activity (goal, last prompt, current action) and usage (context, tokens, cost).
Mouse: click a row for full details (click again to close), drag a header │ to resize,
       wheel to scroll. Keys: ↑↓ select · ⏎ jump to pane · v view · d details · esc close ·
       ←→ pick column · +/- width · 0 fill · space hide/show · q quit.
Column widths persist in columns.json under the plugin state dir; prices (USD per 1M tokens) in
prices.json in the plugin config folder (see config_dir) or next to this file. PANE is coloured with the sc_<palette> token of
ferretorres/herdr-plugin-space-colors when that plugin is installed.
Run `monitor.py --once [--usage]` for a single plain-text frame.
"""
import curses, glob, hashlib, json, os, re, sqlite3, subprocess, sys, textwrap, threading, time, urllib.parse

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ID = "ferretorres.agent-monitor"
GLYPH = {"working": "●", "blocked": "▲", "idle": "○", "done": "✓", "unknown": "◌"}
ORDER = {"blocked": 0, "working": 1, "idle": 2, "done": 3, "unknown": 4}
# per view: [name, width]; width 0 = share the rest, -1 = hidden
VIEWS = {
    "activity": [["ST", 2], ["PANE", 7], ["AGENT", 12], ["AGE", 5], ["GOAL", 34], ["LAST PROMPT", 40], ["NOW", 0]],
    "usage": [["ST", 2], ["PANE", 7], ["AGENT", 12], ["MODEL", 18], ["CTX", 7], ["CTX%", 5], ["IN", 7],
              ["CACHE R", 8], ["CACHE W", 8], ["OUT", 7], ["COST", 8], ["AGE", 5], ["GOAL", 0]],
}
# Anthropic first-party list prices, USD per 1M tokens (https://www.anthropic.com/pricing, 2026-11-26).
# cache_write is the 5-minute cache write, cache_write_1h the 1-hour one. Bedrock / gateway billing
# differs, so COST is an estimate. Add other models in prices.json; missing cache prices default to
# 0.1x (read), 1.25x (5m write) and 2x (1h write) the input price:
# {"my-gateway-model": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "ctx": 258400}}
PRICES = {
    "fable-5-2": {"in": 8.0, "out": 40.0, "cache_read": 0.20, "cache_write": 10.0, "cache_write_1h": 16.0,
                  "ctx": 1_000_000},
    "opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0, "cache_write_1h": 8.0,
                 "ctx": 1_000_000},
    "opus-4-8": {"in": 5.0, "out": 25.0, "cache_read": 0.50, "cache_write": 6.25, "cache_write_1h": 10.0,
                 "ctx": 1_000_000},
    "sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.10, "cache_write": 2.5, "cache_write_1h": 4.0,
                   "ctx": 1_000_000},
    "haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125, "cache_write_1h": 0.20,
                  "ctx": 1_000_000},
}
# Context % and cost bands: table colours, the sidebar row and the context toast all use these.
CTX_WARN, CTX_BAD, CTX_NOTIFY, CTX_REARM = 50, 80, 90, 50
COST_WARN, COST_BAD = 10, 50


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
            o = json.loads(line)
        except ValueError:
            continue  # first line is usually cut by the seek
        if isinstance(o, dict):
            out.append(o)
    return out


_new = {}  # path -> (inode, byte offset already read)
_tail = {}  # path -> ((mtime_ns, size), parsed result)
_touched = set()  # paths read during this refresh; caches of the others are dropped after it


def new_lines(path):
    """(JSON objects appended to path since the last call, whole lines only; rewound).
    rewound is True when the file was truncated or replaced, so callers restart their sums.
    The objects are streamed, so a first read of a large transcript never holds it in memory;
    the read position is saved once the caller has gone through them all."""
    _touched.add(path)
    st = os.stat(path)
    ino, off = _new.get(path, (st.st_ino, 0))
    rewound = ino != st.st_ino or st.st_size < off
    return _stream(path, st.st_ino, 0 if rewound else off), rewound


def _stream(path, ino, off):
    with open(path, "rb") as f:
        f.seek(off)
        for line in f:
            if not line.endswith(b"\n"):
                break  # a line still being written: read it next time
            off += len(line)
            _new[path] = (ino, off)  # per line: a reader that fails on one never re-counts the others
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                yield o
    _new[path] = (ino, off)


def cached_tail(path, parse):
    """parse(path), re-run only when the file's mtime or size changed."""
    _touched.add(path)
    st = os.stat(path)
    key = (st.st_mtime_ns, st.st_size)
    hit = _tail.get(path)
    if hit and hit[0] == key:
        return hit[1]
    result = parse(path)
    _tail[path] = (key, result)
    return result


def prune_caches():
    """Forget files no agent in view used this refresh (gone agents, rotated transcripts)."""
    for cache in (_new, _tail, _cu, _xu, _pu):
        for path in [p for p in cache if p not in _touched]:
            del cache[path]
    _touched.clear()


def is_human(text):
    return text and not text.lstrip().startswith(
        ("<", "Caveat:", "[Request interrupted", "Base directory for this skill"))


def describe_tool(name, inp):
    if not isinstance(inp, dict):
        return f"{name}: {one_line(str(inp), 120)}"
    for key in ("command", "description", "file_path", "path", "pattern", "prompt", "url", "query"):
        if key in inp:
            return f"{name}: {one_line(str(inp[key]), 120)}"
    return name


def claude_roots():
    """Transcript roots: $CLAUDE_CONFIG_DIR/projects, then every ~/.claude*/projects
    (agents started with another CLAUDE_CONFIG_DIR, e.g. ~/.claude-alt, write there)."""
    roots = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        roots.append(os.path.join(os.path.expanduser(os.environ["CLAUDE_CONFIG_DIR"]), "projects"))
    roots += sorted(glob.glob(os.path.join(glob.escape(HOME), ".claude*", "projects")))
    return [r for r in dict.fromkeys(roots) if os.path.isdir(r)]


def session_file(kind, value):
    """agent_session of kind "path" names the transcript itself."""
    if kind == "path" and value:
        path = os.path.expanduser(value)
        return path if os.path.isfile(path) else None
    return None


def claude_path(kind, value):
    if not value:
        return None
    if kind == "path":
        return session_file(kind, value)
    for root in claude_roots():
        paths = glob.glob(os.path.join(glob.escape(root), "*", glob.escape(value) + ".jsonl"))
        if paths:
            return paths[0]
    return None


def claude_activity(path):
    return cached_tail(path, _claude_activity)


def _claude_activity(path):
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
    fresh = lambda: {"tot": [0] * 5, "id": None, "cur": [0] * 5, "ctx": 0, "model": "", "prompt": ""}
    st = _cu.setdefault(path, fresh())
    objs, rewound = new_lines(path)
    if rewound:
        st = _cu[path] = fresh()
    for o in objs:
        msg = _obj(o.get("message"))
        if o.get("type") == "user" or o.get("type") == "last-prompt":
            text = o.get("lastPrompt") if o.get("type") == "last-prompt" else msg.get("content")
            texts = [text] if isinstance(text, str) else [
                c.get("text", "") for c in text or [] if isinstance(c, dict) and c.get("type") == "text"]
            texts = [t for t in texts if isinstance(t, str) and is_human(t)]
            if texts:
                st["prompt"] = texts[-1]
            continue
        u = msg.get("usage")
        if o.get("type") != "assistant" or not isinstance(u, dict):
            continue
        if msg.get("id") != st["id"]:
            st["tot"] = [a + b for a, b in zip(st["tot"], st["cur"])]
            st["id"] = msg.get("id")
        st["cur"] = claude_reply_usage(u)
        st["ctx"] = sum(st["cur"][:4])
        st["model"] = msg.get("model") or st["model"]
    return [a + b for a, b in zip(st["tot"], st["cur"])], st["ctx"], st["model"]


def _obj(v):
    return v if isinstance(v, dict) else {}


def claude_reply_usage(u):
    """[input, cache read, 5m cache write, 1h cache write, output] of one Claude reply. Lines without
    the cache_creation split count every write as 5m. A few replies carry zeros at the top level and
    the real numbers in iterations[]."""
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
    parts = [u]
    if not any(u.get(k) for k in keys) and isinstance(u.get("iterations"), list):
        parts = [i for i in u["iterations"] if isinstance(i, dict)]
    out = [0] * 5
    for p in parts:
        cc = p.get("cache_creation") or {}
        w1h = cc.get("ephemeral_1h_input_tokens") or 0
        w5m = cc.get("ephemeral_5m_input_tokens", (p.get("cache_creation_input_tokens") or 0) - w1h) or 0
        vals = [p.get("input_tokens") or 0, p.get("cache_read_input_tokens") or 0, max(0, w5m), w1h,
                p.get("output_tokens") or 0]
        out = [a + b for a, b in zip(out, vals)]
    return out


def claude_usage(path):
    tot, ctx, model = claude_file_usage(path)
    subs = glob.glob(os.path.join(path[:-len(".jsonl")], "subagents", "*.jsonl"))
    for p in subs:  # subagents bill tokens too, but don't use this session's context
        tot = [a + b for a, b in zip(tot, claude_file_usage(p)[0])]
    return {"in": tot[0], "cr": tot[1], "cw": tot[2] + tot[3], "cw5m": tot[2], "cw1h": tot[3],
            "out": tot[4], "ctx": ctx, "model": model, "window": None, "subagents": len(subs),
            "prompt": _cu.get(path, {}).get("prompt", "")}


_codex_cwd = {}  # rollout path -> cwd (session_meta is the first line, never changes)


def codex_root():
    return os.path.join(os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex"), "sessions")


def codex_path(cwd, kind=None, value=None):
    """The agent's rollout: by session path or id when herdr has one, else the newest rollout
    of the last 3 days with the same cwd."""
    if kind == "path":
        return session_file(kind, value)
    pattern = os.path.join(glob.escape(codex_root()), "*", "*", "*", "rollout-*.jsonl")
    if value:
        hit = glob.glob(pattern[:-len(".jsonl")] + glob.escape(value) + ".jsonl")
        if hit:
            return hit[0]
    # ponytail: without a session id, match by cwd + newest file;
    # two codex agents in one directory would show the same session.
    best, best_mtime, now, seen = None, 0, time.time(), {}
    for p in glob.glob(pattern):
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        if now - mtime > 3 * 86400:
            continue
        if p not in _codex_cwd:
            try:
                with open(p) as f:
                    _codex_cwd[p] = json.loads(f.readline()).get("payload", {}).get("cwd")
            except (OSError, ValueError, AttributeError):
                _codex_cwd[p] = None
        seen[p] = _codex_cwd[p]
        if seen[p] == cwd and mtime > best_mtime:
            best, best_mtime = p, mtime
    _codex_cwd.clear()  # forget rollouts that aged out or were deleted
    _codex_cwd.update(seen)
    return best


def codex_activity(path):
    return cached_tail(path, _codex_activity)


def _codex_activity(path):
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
    objs, rewound = new_lines(path)
    if rewound:
        st = _xu[path] = {"info": None, "model": ""}
    for o in objs:
        p = _obj(o.get("payload"))
        if p.get("type") == "token_count" and isinstance(p.get("info"), dict):
            st["info"] = p["info"]
        elif o.get("type") == "turn_context":
            st["model"] = p.get("model") or st["model"]
    info = st["info"] or {}
    t, last = info.get("total_token_usage") or {}, info.get("last_token_usage") or {}
    cached, cw = t.get("cached_input_tokens", 0), t.get("cache_write_input_tokens", 0)
    return {"in": max(0, t.get("input_tokens", 0) - cached - cw),  # codex input includes cached
            "cr": cached, "cw": cw, "cw5m": cw, "cw1h": 0, "out": t.get("output_tokens", 0),
            "ctx": last.get("input_tokens", 0), "model": st["model"],
            "window": info.get("model_context_window"), "subagents": 0}


def pi_dir():
    return os.path.expanduser(os.environ.get("PI_CODING_AGENT_DIR") or "~/.pi/agent")


def pi_path(kind, value):
    """pi's session file. herdr's pi integration reports its path; with only an id, look for
    <sessions>/<cwd dir>/<timestamp>_<id>.jsonl. pi writes the file only after its first reply."""
    if kind == "path":
        return session_file(kind, value)
    if not value:
        return None
    for root in filter(None, (os.environ.get("PI_CODING_AGENT_SESSION_DIR"), os.path.join(pi_dir(), "sessions"))):
        hit = glob.glob(os.path.join(glob.escape(os.path.expanduser(root)), "*", "*_" + glob.escape(value) + ".jsonl"))
        if hit:
            return hit[0]
    return None


def pi_activity(path):
    return cached_tail(path, _pi_activity)


def _pi_activity(path):
    prompt = now = ""
    for o in tail_lines(path):
        m = o.get("message") or {}
        if o.get("type") != "message":
            continue
        content = m.get("content")
        if m.get("role") == "user":
            texts = [content] if isinstance(content, str) else [
                c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
            texts = [t for t in texts if is_human(t)]
            if texts:
                prompt = texts[-1]
        elif m.get("role") == "assistant" and isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "toolCall":
                    now = describe_tool(c.get("name", "tool"), c.get("arguments"))
                elif c.get("type") == "text" and (c.get("text") or "").strip():
                    now = "says: " + c["text"]
    return prompt, now, os.path.getmtime(path)


_pu = {}  # pi session path -> running usage


def _n(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
_pi_models = {"mtime": None, "windows": {}}


def pi_window(provider, model):
    """Context window from pi's models-store.json, reloaded when it changes."""
    path = os.path.join(pi_dir(), "models-store.json")
    try:
        mtime = os.stat(path).st_mtime_ns
        if mtime != _pi_models["mtime"]:
            with open(path) as f:
                data = json.load(f)
            _pi_models["windows"] = {(prov, m.get("id")): m.get("contextWindow")
                                     for prov, v in data.items() if isinstance(v, dict)
                                     for m in v.get("models") or [] if isinstance(m, dict)}
            _pi_models["mtime"] = mtime
    except (OSError, ValueError, AttributeError):
        return None
    return _pi_models["windows"].get((provider, model))


def pi_usage(path):
    """Totals the way pi's footer counts them: assistant replies, subagent tool results and
    compaction / branch summary / usage entries. Context is the last valid reply, and unknown
    after a compaction until the next reply. Cost is pi's own; 0 means it recorded none."""
    fresh = lambda: {"tot": [0] * 4, "usd": 0.0, "ctx": None, "model": "", "provider": ""}
    st = _pu.setdefault(path, fresh())
    objs, rewound = new_lines(path)
    if rewound:
        st = _pu[path] = fresh()
    for o in objs:
        kind, m = o.get("type"), _obj(o.get("message"))
        u = None
        if kind == "message" and m.get("role") in ("assistant", "toolResult"):
            u = m.get("usage")
        elif kind in ("compaction", "branch_summary", "usage"):
            u = o.get("usage")
        if kind == "compaction":
            st["ctx"] = None
        if not isinstance(u, dict):
            continue
        vals = [_n(u.get("input")), _n(u.get("cacheRead")), _n(u.get("cacheWrite")), _n(u.get("output"))]
        st["tot"] = [a + b for a, b in zip(st["tot"], vals)]
        usd = u["cost"].get("total") if isinstance(u.get("cost"), dict) else 0
        st["usd"] += usd if isinstance(usd, (int, float)) else 0
        if kind == "message" and m.get("role") == "assistant" and m.get("stopReason") not in ("aborted", "error"):
            ctx = u.get("totalTokens") or sum(vals)
            if ctx:
                st["ctx"], st["model"], st["provider"] = ctx, m.get("model") or st["model"], m.get("provider") or ""
    t = st["tot"]
    return {"in": t[0], "cr": t[1], "cw": t[2], "out": t[3], "ctx": st["ctx"] or 0, "model": st["model"],
            "window": pi_window(st["provider"], st["model"]), "subagents": 0,
            "usd": st["usd"] or None, "usd_src": "pi" if st["usd"] else None}


def opencode_db():
    data = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"), "opencode")
    p = os.environ.get("OPENCODE_DB")
    return os.path.join(data, os.path.expanduser(p)) if p else os.path.join(data, "opencode.db")


_oc_models = {"mtime": None, "windows": {}}


def opencode_window(provider, model):
    """Context window from opencode's models.dev cache, reloaded when it changes."""
    path = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "opencode", "models.json")
    try:
        mtime = os.stat(path).st_mtime_ns
        if mtime != _oc_models["mtime"]:
            with open(path) as f:
                data = json.load(f)
            _oc_models["windows"] = {(prov, mid): ((m.get("limit") or {}).get("context"))
                                     for prov, v in data.items() if isinstance(v, dict)
                                     for mid, m in (v.get("models") or {}).items() if isinstance(m, dict)}
            _oc_models["mtime"] = mtime
    except (OSError, ValueError, AttributeError):
        return None
    return _oc_models["windows"].get((provider, model))


OC_TREE = "with recursive t(id) as (select ? union all select s.id from session s join t on s.parent_id = t.id)"


def opencode_read(a):
    """(prompt, now, ts, usage) for an opencode agent from opencode's SQLite database, or None
    (no database, an unknown schema, no session): the row then falls back to the screen.
    Opened read-only and closed at once, so opencode's WAL checkpoints are never held up."""
    path = opencode_db()
    if not os.path.isfile(path):
        return None
    s = a.get("agent_session") or {}
    try:
        c = sqlite3.connect(f"file:{urllib.parse.quote(path)}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return None
    try:
        tables = {n for (n,) in c.execute("select name from sqlite_master where type = 'table'")}
        if not {"session", "message", "part"} <= tables:
            return None  # e.g. the session_v2 schema of newer opencode releases
        sid = s.get("value") if s.get("kind") == "id" and str(s.get("value", "")).startswith("ses_") else None
        if not sid and a.get("cwd"):
            # ponytail: without herdr's session id, the newest root session in this folder;
            # two opencode agents in one folder would show the same session.
            r = c.execute("select id from session where directory = ? and parent_id is null"
                          " and time_archived is null order by time_updated desc limit 1", (a["cwd"],)).fetchone()
            sid = r and r[0]
        if not sid:
            return None
        head = c.execute("select time_updated from session where id = ?", (sid,)).fetchone()
        if not head:
            return None
        n, usd, tin, tout, tcr, tcw = c.execute(
            OC_TREE + " select count(*) - 1, sum(cost), sum(tokens_input), sum(tokens_output + tokens_reasoning),"
            " sum(tokens_cache_read), sum(tokens_cache_write) from session where id in (select id from t)",
            (sid,)).fetchone()
        last = c.execute("select json_extract(data, '$.tokens'), json_extract(data, '$.providerID'),"
                         " json_extract(data, '$.modelID') from message where session_id = ?"
                         " and json_extract(data, '$.role') = 'assistant' and json_extract(data, '$.tokens.output') > 0"
                         " order by time_created desc limit 1", (sid,)).fetchone()
        prompt = c.execute("select json_extract(p.data, '$.text') from part p join message m on m.id = p.message_id"
                           " where m.session_id = ? and json_extract(m.data, '$.role') = 'user'"
                           " and json_extract(p.data, '$.type') = 'text'"
                           " and coalesce(json_extract(p.data, '$.synthetic'), 0) = 0"
                           " order by m.time_created desc, p.id desc limit 1", (sid,)).fetchone()
        part = c.execute("select data from part where session_id = ? and json_extract(data, '$.type') in ('tool', 'text')"
                         " order by time_created desc, id desc limit 1", (sid,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        c.close()
    ctx, provider, model = 0, "", ""
    if last:
        try:
            t = json.loads(last[0] or "{}")
            cache = t.get("cache") or {}
            ctx = sum(t.get(k) or 0 for k in ("input", "output", "reasoning")) + (cache.get("read") or 0) \
                + (cache.get("write") or 0)
        except (ValueError, AttributeError):
            pass
        provider, model = last[1] or "", last[2] or ""
    now = ""
    if part:
        try:
            p = json.loads(part[0])
        except ValueError:
            p = {}
        if p.get("type") == "tool":
            st = p.get("state") or {}
            now = describe_tool(p.get("tool", "tool"), st.get("input"))
            if st.get("status") in ("pending", "running"):
                now += f" ({st['status']})"
        elif (p.get("text") or "").strip():
            now = "says: " + p["text"]
    text = prompt[0] if prompt and is_human(prompt[0]) else ""
    usage = {"in": tin or 0, "cr": tcr or 0, "cw": tcw or 0, "out": tout or 0, "ctx": ctx, "model": model,
             "window": opencode_window(provider, model), "subagents": n or 0,
             "usd": usd or None, "usd_src": "opencode" if usd else None}
    return text, now, (head[0] or 0) / 1000 or None, usage


def screen_activity(pane_id):
    try:
        lines = herdr("agent", "read", pane_id, "--source", "recent", "--lines", "40").splitlines()
    except subprocess.TimeoutExpired:  # herdr can be slow to snapshot a busy pane; skip it this round
        return "", "screen: (herdr did not answer in 5 s)", None
    except HerdrError as e:
        return "", f"screen: ({e})", None
    lines = [l.strip() for l in lines if sum(ch.isalnum() for ch in l) > 5]  # drop borders
    return "", "screen: " + " ⏎ ".join(lines[-2:]), None


SCREEN_MAX_AGE = 10.0  # seconds: re-read an unchanged pane's screen at least this often
_screen = {}  # pane id -> {"key", "at", "value", "thread"}
_screen_lock = threading.Lock()


def screen_start(a):
    """Start a background `agent read` for an agent without a transcript, when its pane changed
    (revision / state_change_seq / status) or the last read is old. Never waits, so a pane
    herdr is slow to read only delays its own row; at most one read per pane is in flight."""
    pane = a["pane_id"]
    key = (a.get("revision"), a.get("state_change_seq"), a.get("agent_status"))
    with _screen_lock:
        e = _screen.setdefault(pane, {"key": None, "at": 0.0, "value": ("", "screen: (reading…)", None),
                                      "thread": None})
        if e["thread"] is None and (e["key"] != key or time.time() - e["at"] > SCREEN_MAX_AGE):
            e["key"] = key
            e["thread"] = threading.Thread(target=_read_screen, args=(pane, e), daemon=True)
            e["thread"].start()
    return e


def _read_screen(pane, e):
    try:
        value = screen_activity(pane)
    except Exception as ex:  # a background thread must not die silently with the entry locked busy
        value = ("", f"screen: ({ex})", None)
    with _screen_lock:
        e["value"], e["at"], e["thread"] = value, time.time(), None


def screen_wait(entries, timeout=6.0):
    """Block until these reads finish (for --once)."""
    end = time.time() + timeout
    for e in entries:
        t = e["thread"]
        if t is not None:
            t.join(max(0.0, end - time.time()))


def screen_prune(panes):
    with _screen_lock:
        for pane in [p for p, e in _screen.items() if p not in panes and e["thread"] is None]:
            del _screen[pane]


def config_dir():
    """HERDR_PLUGIN_CONFIG_DIR in a plugin pane, else herdr's plugin config folder for this plugin.
    It survives `herdr plugin update`, which replaces the folder this file is in."""
    if os.environ.get("HERDR_PLUGIN_CONFIG_DIR"):
        return os.environ["HERDR_PLUGIN_CONFIG_DIR"]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
    return os.path.join(base, "herdr", "plugins", "config", PLUGIN_ID)


def load_prices():
    """Built-in prices, then prices.json next to this file, then prices.json in the config folder."""
    prices = {k: dict(v) for k, v in PRICES.items()}
    for folder in (HERE, config_dir()):
        try:
            with open(os.path.join(folder, "prices.json")) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key, entry in data.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue  # a malformed entry is skipped, never a crash for every row
            nums = {f: v for f, v in entry.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            prices[key] = dict(prices.get(key, {}), **nums)  # keys left out keep their built-in values
    return prices


def price_for(model, prices):
    """The price whose key is the longest part of the model name (opus-4-8 over opus-4)."""
    key = max((k for k in prices if k and k in (model or "")), key=len, default=None)
    return prices[key] if key else None


def cost(u, price):
    """USD at list price. 5m and 1h cache writes are priced apart; a usage without the split
    (or a price without the cache keys) falls back to the multiples of the input price."""
    if not price or "in" not in price or "out" not in price:
        return None
    i = price["in"]
    read = price.get("cache_read", 0.1 * i)
    w5, w1 = price.get("cache_write", 1.25 * i), price.get("cache_write_1h", 2 * i)
    cw5m, cw1h = u.get("cw5m", u.get("cw", 0)), u.get("cw1h", 0)
    return (u["in"] * i + u["cr"] * read + cw5m * w5 + cw1h * w1 + u["out"] * price["out"]) / 1e6


def num(n):
    if not n:
        return ""
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)


def ago(ts):
    if not ts:
        return ""
    s = int(time.time() - ts)
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h" if s < 86400 else f"{s // 86400}d"


# Workspace colours come from ferretorres/herdr-plugin-space-colors, which puts an sc_<palette>
# token on each agent, pane and workspace. Its config names the accent of each palette; without
# that config (or on Python < 3.11, which has no tomllib) its default accents apply.
SPACE_CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"),
                            "herdr", "plugins", "config", "ferretorres.space-colors", "config.toml")
SPACE_ACCENTS = {"blue": "#89b4fa", "green": "#a6e3a1", "mauve": "#cba6f7", "peach": "#fab387",
                 "pink": "#f5c2e7", "red": "#f38ba8", "teal": "#94e2d5", "yellow": "#f9e2af"}


def load_space_colors():
    """Accent hex by palette name, from the space-colors config, else its defaults."""
    try:
        import tomllib  # Python 3.11+
        with open(SPACE_CONFIG, "rb") as f:
            cfg = tomllib.load(f)
    except (ImportError, OSError, ValueError):
        return dict(SPACE_ACCENTS)
    accents = {name: p["accent"] for name, p in cfg.get("palettes", {}).items() if p.get("accent")}
    return accents or dict(SPACE_ACCENTS)


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


def collect(wait=False):
    """(rows, scope label) for one refresh. wait: block for screen reads (one-shot output)."""
    ws = scope_workspace()
    snap = snapshot()
    return build_rows(snap, ws, wait), scope_label(ws, snap)


def transcript(a):
    """The agent's Claude transcript or Codex rollout, if it has one."""
    s = a.get("agent_session") or {}
    if a["agent"] == "claude":
        return claude_path(s.get("kind"), s.get("value"))
    if a["agent"] == "codex":
        return codex_path(a.get("cwd"), s.get("kind"), s.get("value"))
    if a["agent"] == "pi":
        return pi_path(s.get("kind"), s.get("value"))
    return None


def agent_entry(a):
    """The agent record with the fields the monitor needs, or None. herdr can list entries
    without an `agent` kind (e.g. a pane whose resume failed after a server restart)."""
    if not isinstance(a, dict) or not a.get("pane_id"):
        return None
    return dict(a, agent=a.get("agent") or a.get("display_agent") or "?",
                agent_status=a.get("agent_status") or "unknown")


def build_rows(snap, ws=None, wait=False, screens=True):
    agents = [e for e in map(agent_entry, snap.get("agents") or [])
              if e and (ws is None or e.get("workspace_id") == ws)]
    found = {a["pane_id"]: transcript(a) for a in agents}
    oc = {a["pane_id"]: opencode_read(a) for a in agents if a["agent"] == "opencode"}
    want_screen = [a for a in agents if not found[a["pane_id"]] and not oc.get(a["pane_id"])]
    if screens:
        screens = {a["pane_id"]: screen_start(a) for a in want_screen}
        if wait:
            screen_wait(screens.values())
        screen_prune(screens)
    else:  # the hook: no screen reads, these rows only feed the sidebar and the toast
        screens = {a["pane_id"]: {"value": ("", "", None)} for a in want_screen}
    panes = {p.get("pane_id"): p for p in snap.get("panes") or []}
    spaces = {w.get("workspace_id"): w for w in snap.get("workspaces") or []}
    tabs = {t.get("tab_id"): t for t in snap.get("tabs") or []}
    prices = load_prices()
    rows = []
    for a in agents:
        path, usage = found[a["pane_id"]], None
        if path and a["agent"] == "claude":
            prompt, now, ts = claude_activity(path)
            usage = claude_usage(path)
            prompt = prompt or usage.get("prompt", "")  # big attachments can push it out of the tail
        elif path and a["agent"] == "pi":
            prompt, now, ts = pi_activity(path)
            usage = pi_usage(path)
        elif path:
            prompt, now, ts = codex_activity(path)
            usage = codex_usage(path)
        elif oc.get(a["pane_id"]):
            prompt, now, ts, usage = oc[a["pane_id"]]
        else:
            prompt, now, ts = screens[a["pane_id"]]["value"]
        price = price_for(usage and usage["model"], prices)
        window = (usage or {}).get("window") or (price or {}).get("ctx")
        # Agents that record their own cost (pi, opencode) use it; the rest are list-priced here.
        usd = (usage.get("usd") or cost(usage, price)) if usage else None
        usd_src = (usage.get("usd_src") if usage.get("usd") else "list") if usage and usd is not None else None
        pct = 100 * usage["ctx"] / window if usage and window and usage["ctx"] else None
        u = usage or {}
        goal = a.get("title") or a.get("terminal_title_stripped") or ""  # title: report-metadata --title
        tokens = a.get("tokens") or {}
        role, lead, crew = tokens.get("crew_role"), tokens.get("crew_lead"), tokens.get("crew_name")
        palette = sc_palette(a.get("tokens"), (panes.get(a["pane_id"]) or {}).get("tokens"),
                             (spaces.get(a.get("workspace_id")) or {}).get("tokens"))
        rows.append({
            "pane": a["pane_id"], "agent": a["agent"], "status": a["agent_status"], "ts": ts,
            "space": palette, "parent": lead if role == "worker" else None,
            "pct": pct, "usd": usd, "usd_src": usd_src, "usage": usage,
            "tokens": tokens, "labels": a.get("state_labels") or {},
            "session": path or (a.get("agent_session") or {}).get("value") or "",
            "place": " · ".join(x for x in ((spaces.get(a.get("workspace_id")) or {}).get("label"),
                                            (tabs.get(a.get("tab_id")) or {}).get("label"), a["pane_id"]) if x),
            "cells": {
                "ST": GLYPH.get(a["agent_status"], "?"), "PANE": a["pane_id"], "AGENT": a.get("name") or a["agent"],
                "AGE": ago(ts), "GOAL": one_line(goal), "LAST PROMPT": one_line(prompt), "NOW": one_line(now),
                "MODEL": u.get("model", ""), "CTX": num(u.get("ctx")),
                "CTX%": f"{pct:.0f}%" if pct is not None else "",
                "IN": num(u.get("in")), "CACHE R": num(u.get("cr")), "CACHE W": num(u.get("cw")),
                "OUT": num(u.get("out")),
                "COST": f"${usd:.2f}" if usd is not None else ("?" if usage else ""),
            },
            "full": [("GOAL", goal), ("LAST PROMPT", prompt or ""), ("NOW", now or ""),
                     ("USAGE", usage_text(usage, window, usd, usd_src)),
                     ("WHERE", f'{a["pane_id"]} · {a.get("cwd", "")}' + (f" · {path}" if path else ""))]
                    + ([("CREW", " · ".join(x for x in (crew, role, lead and f"lead {lead}") if x))]
                       if role or crew else []),
        })
    prune_caches()
    rows.sort(key=lambda r: (ORDER.get(r["status"], len(ORDER)), pane_key(r["pane"])))
    return tree_order(rows)


def pane_key(pane_id):
    """w2:p10 -> ["w", 2, ":p", 10, ""], so w2 sorts before w10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", pane_id or "")]


def tree_order(rows):
    """Each lead followed by its workers (crew_lead token = the lead's pane id), depth-first,
    indented with └ in PANE. A worker whose lead is not shown is a root; cycles are cut."""
    shown = {r["pane"] for r in rows}
    kids = {}
    for r in rows:
        parent = r.get("parent") if r.get("parent") in shown and r.get("parent") != r["pane"] else None
        kids.setdefault(parent, []).append(r)
    out, seen = [], set()

    def walk(r, depth):
        if r["pane"] in seen:
            return
        seen.add(r["pane"])
        r["depth"] = depth
        r["cells"]["PANE"] = "  " * (depth - 1) + "└ " + r["pane"] if depth else r["pane"]
        out.append(r)
        for k in kids.get(r["pane"], []):
            walk(k, depth + 1)

    for r in kids.get(None, []):
        walk(r, 0)
    for r in rows:  # only reachable through a cycle: start it as a root
        walk(r, 0)
    return out


def fit_cols(cols, rows, cap=24):
    """cols with PANE widened to fit indented ids (fixed widths only, never narrower)."""
    need = max((len(r["cells"]["PANE"]) for r in rows), default=0)
    return [[n, min(max(wd, need), cap) if n == "PANE" and wd > 0 else wd] for n, wd in cols]


COST_SOURCE = {"list": "at list price", "pi": "as recorded by pi",
               "opencode": "as estimated by opencode (models.dev prices)"}


def usage_text(u, window, usd, src="list"):
    if not u:
        return "no token data for this agent (Claude Code, Codex, pi and opencode sessions are read)"
    ctx = f"context {u['ctx']:,}" + (f" of {window:,} ({100 * u['ctx'] / window:.0f}%)" if window else "")
    if usd is None:
        money = f"no price for {u['model']!r} (add it to prices.json)"
    else:
        money = f"≈ ${usd:.2f} {COST_SOURCE.get(src, 'at list price')}"
        if u.get("cw1h"):
            money += " (1h and 5m cache writes priced apart)"
    split = f" (1h {u['cw1h']:,} · 5m {u.get('cw5m', 0):,})" if u.get("cw1h") else ""
    subs = f" · includes {u['subagents']} subagent sessions" if u["subagents"] else ""
    return (f"{u['model']} · {ctx}\ninput {u['in']:,} · cache read {u['cr']:,} · cache write {u['cw']:,}{split}"
            f" · output {u['out']:,}{subs}\n{money}")


def totals(rows):
    us = [r["usage"] for r in rows if r["usage"]]
    t = {k: sum(u[k] for u in us) for k in ("in", "cr", "cw", "out")}
    usd = sum(r["usd"] for r in rows if r["usd"] is not None)
    unpriced = sum(1 for r in rows if r["usage"] and r["usd"] is None)
    srcs = {r.get("usd_src") for r in rows if r["usd"] is not None}
    label = "list price" if srcs <= {"list"} else "agent-reported" if "list" not in srcs else "list price + agent-reported"
    return (f"Σ in {num(t['in']) or 0} · cache r {num(t['cr']) or 0} · cache w {num(t['cw']) or 0}"
            f" · out {num(t['out']) or 0} · ≈ ${usd:.2f} {label}" + (f" (+{unpriced} unpriced)" if unpriced else ""))


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
    cols = fit_cols(cols, rows)
    spec = layout(cols, width)
    lines = [" ".join(fit(cols[i][0], w) for i, _, w in spec)]
    for r in rows:
        lines.append(" ".join(fit(r["cells"][cols[i][0]], w) for i, _, w in spec))
    return [l[:width] for l in lines]


def state_dir():
    """HERDR_PLUGIN_STATE_DIR in a plugin pane, else ${XDG_STATE_HOME:-~/.local/state}/herdr/plugins/ferretorres.agent-monitor.
    Not the plugin dir: an installed plugin's dir is replaced on update."""
    if os.environ.get("HERDR_PLUGIN_STATE_DIR"):
        return os.environ["HERDR_PLUGIN_STATE_DIR"]
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state")
    return os.path.join(base, "herdr", "plugins", PLUGIN_ID)


def load_cols():
    saved = {}
    for path in (os.path.join(state_dir(), "columns.json"), os.path.join(HERE, "columns.json")):  # 0.1 kept it in HERE
        try:
            with open(path) as f:
                saved = json.load(f)
            break
        except (OSError, ValueError):
            continue
    if not isinstance(saved, dict):
        saved = {}  # older single-view format
    return {v: saved[v] if [c[0] for c in saved.get(v, [])] == [c[0] for c in d] else [c[:] for c in d]
            for v, d in VIEWS.items()}


def save_cols(colsets):
    """Write via a temp file + rename, so monitors in other panes never read half a file.
    False when it could not be saved (read-only disk...); the widths still apply in this pane."""
    folder = state_dir()
    tmp = os.path.join(folder, f".columns.{os.getpid()}.tmp")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(colsets, f)
        os.replace(tmp, os.path.join(folder, "columns.json"))
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


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
                   "done": P(3), "idle": P(8), "unknown": P(8)},
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
    for n, (name, accent) in enumerate(sorted(load_space_colors().items()), 20):
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
        return C["bad"] if row["pct"] >= CTX_BAD else C["warn"] if row["pct"] >= CTX_WARN else C["ok"]
    if name == "COST" and row["usd"] is not None:
        return C["bad"] if row["usd"] >= COST_BAD else C["warn"] if row["usd"] >= COST_WARN else 0
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


# --- Sidebar row and context toast ---------------------------------------------------------------
# The monitor writes only its own am_* pane tokens (source ferretorres.agent-monitor), never state
# labels, titles or other plugins' keys: the crew plugin owns the labels and rewrites them.
SIDEBAR_KEYS = ("am_bad", "am_warn", "am_ok")
TOAST_RETRY_S = 60  # a toast herdr declined (toasts off, no client attached) is retried this often
PAD = "\u2800"  # herdr trims leading spaces in sidebar rows, not this blank


def sidebar_band(row):
    """am_bad / am_warn / am_ok for a row's context % and cost, or None without numbers."""
    pct, usd = row.get("pct"), row.get("usd")
    if pct is None and usd is None:
        return None
    if (pct or 0) >= CTX_BAD or (usd or 0) >= COST_BAD:
        return "am_bad"
    if (pct or 0) >= CTX_WARN or (usd or 0) >= COST_WARN:
        return "am_warn"
    return "am_ok"


def money_short(usd):
    return f"${usd:.1f}" if usd < 10 else f"${usd:.0f}"


def sidebar_value(row):
    """The numbers, indented 2 past the agent line's own indent (the crew plugin's labels start
    with U+2800 blanks), so the row sits under the agent's name."""
    label = next((v for v in (row.get("labels") or {}).values() if v), "")
    indent = len(label) - len(label.lstrip(PAD))
    parts = ([f"{row['pct']:.0f}%"] if row.get("pct") is not None else []) + \
            ([money_short(row["usd"])] if row.get("usd") is not None else [])
    return PAD * (indent + 2) + " ".join(parts)


def sidebar_want(row, mode):
    """{key: value} of am_* tokens this pane should carry: one key at most, none below warn."""
    if mode == "off":
        return {}
    band = sidebar_band(row)
    if band is None or (band == "am_ok" and mode != "all"):
        return {}
    return {band: sidebar_value(row)}


def metadata_args(pane, set_tokens, clear):
    """The report-metadata call. Refuses any key outside the monitor's own am_* prefix."""
    keys = list(set_tokens) + list(clear)
    if any(not k.startswith("am_") for k in keys):
        raise ValueError(f"refusing to write tokens outside am_*: {keys}")
    args = ["pane", "report-metadata", pane, "--source", PLUGIN_ID]
    for k, v in set_tokens.items():
        args += ["--token", f"{k}={v}"]
    for k in clear:
        args += ["--clear-token", k]
    return args


def sidebar_update(row, mode):
    """report-metadata args to bring the pane's live am_* tokens to what they should be, or None."""
    want = sidebar_want(row, mode)
    have = {k: v for k, v in (row.get("tokens") or {}).items() if k in SIDEBAR_KEYS}
    set_tokens = {k: v for k, v in want.items() if have.get(k) != v}
    clear = [k for k in have if k not in want]
    return metadata_args(row["pane"], set_tokens, clear) if set_tokens or clear else None


def toast_marker(row):
    key = hashlib.sha256(f"{row['pane']}\0{row.get('session', '')}".encode()).hexdigest()[:16]
    return os.path.join(state_dir(), "sent", f"{key}-ctx{CTX_NOTIFY}")


def toast_due(row):
    """Claim the context toast for this pane's session, once: the process that creates the
    marker sends it, so the open monitor and the hook never both do. Below CTX_REARM the claim
    is dropped and the next crossing toasts again. True = send now."""
    pct, marker = row.get("pct"), toast_marker(row)
    if pct is None or pct < CTX_REARM:
        try:
            os.unlink(marker)
        except OSError:
            pass
        return False
    if pct < CTX_NOTIFY:
        return False
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        # herdr declined it (toasts off, nobody attached): try again now and then, not every refresh
        try:
            with open(marker) as f:
                waiting = f.read().startswith("retry")
            if waiting and time.time() - os.path.getmtime(marker) > TOAST_RETRY_S:
                os.utime(marker)
                return True
        except OSError:
            pass
        return False
    except OSError:
        return False
    return True


def send_toast(row):
    name = row["cells"].get("AGENT") or row["agent"]
    try:
        out = herdr_json("notification", "show", f"{name} context {row['pct']:.0f}%", "--body", row.get("place") or row["pane"],
                         "--sound", "none")
    except (HerdrError, subprocess.TimeoutExpired):
        out = {"reason": "busy"}
    marker = toast_marker(row)
    try:
        if out.get("shown"):
            with open(marker, "w") as f:
                f.write("shown")
        elif out.get("reason") in ("rate_limited", "busy"):
            os.unlink(marker)  # try again on the next refresh or status change
        else:  # disabled, no_foreground_client...: retry after TOAST_RETRY_S
            with open(marker, "w") as f:
                f.write(f"retry {out.get('reason', '')}")
    except OSError:
        pass


def prune_markers(days=7):
    """Drop toast markers and hook read caches nobody touched for a week."""
    old = time.time() - days * 86400
    for folder in (os.path.join(state_dir(), "sent"), os.path.join(state_dir(), "reads")):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for n in names:
            p = os.path.join(folder, n)
            try:
                if os.path.getmtime(p) < old:
                    os.unlink(p)
            except OSError:
                pass


def publish(rows):
    """Write the sidebar row tokens that changed and send due context toasts. Called by the
    open monitor after each refresh and by the pane.agent_status_changed hook."""
    mode = (os.environ.get("AGENT_MONITOR_SIDEBAR") or "warn").strip()
    notify = (os.environ.get("AGENT_MONITOR_NOTIFY") or "1").strip() != "0"
    for row in rows:
        args = sidebar_update(row, mode)
        if args:
            try:
                herdr(*args)
            except (HerdrError, subprocess.TimeoutExpired):
                pass  # the pane may have closed; the next refresh retries
        if notify and toast_due(row):
            send_toast(row)
    prune_markers()


def hook():
    """pane.agent_status_changed: refresh that one pane's sidebar row and toast, with no monitor
    pane open. herdr runs one process per event."""
    try:
        ev = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    except ValueError:
        ev = {}
    data = ev.get("data") if isinstance(ev.get("data"), dict) else ev
    pane = data.get("pane_id") or (data.get("pane") or {}).get("pane_id") or os.environ.get("HERDR_PANE_ID")
    if not pane:
        return 0
    try:
        snap = snapshot()
    except (HerdrError, subprocess.TimeoutExpired):
        return 1
    agents = [a for a in snap.get("agents") or [] if isinstance(a, dict) and a.get("pane_id") == pane]
    if not agents:  # the agent exited: drop its sidebar line
        p = next((p for p in snap.get("panes") or [] if p.get("pane_id") == pane), None)
        stale = [k for k in ((p or {}).get("tokens") or {}) if k in SIDEBAR_KEYS]
        if stale:
            try:
                herdr(*metadata_args(pane, {}, stale))
            except (HerdrError, subprocess.TimeoutExpired):
                pass
        return 0
    cache = os.path.join(state_dir(), "reads", hashlib.sha256(pane.encode()).hexdigest()[:16] + ".json")
    load_reads(cache)
    publish(build_rows(dict(snap, agents=agents), wait=False, screens=False))
    save_reads(cache)
    return 0


READ_CACHES = {"new": _new, "cu": _cu, "xu": _xu, "pu": _pu}


def load_reads(path):
    """The hook runs as a new process per event: restore where the last run stopped reading this
    pane's session files, so it parses only what was added since."""
    try:
        with open(path) as f:
            data = json.load(f)
        for name, cache in READ_CACHES.items():
            for k, v in (data.get(name) or {}).items():
                cache[k] = tuple(v) if name == "new" else v
    except (OSError, ValueError, AttributeError, TypeError):
        pass


def save_reads(path):
    data = {name: {k: v for k, v in cache.items() if k in _new} for name, cache in READ_CACHES.items()}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        pass


def refresh_forever(state, every=2.0):
    """Background thread: collect() off the UI thread, so a slow herdr never freezes keys or mouse.
    The UI only reads state["data"]; one assignment of a tuple, so no lock is needed."""
    while True:
        try:
            refresh_once(state)
        except Exception as e:  # never let the thread die: the screen would freeze on old rows
            state["data"] = (state["data"][0], f" ⚠ monitor error: {type(e).__name__}: {e} ·")
        time.sleep(every)


def refresh_once(state):
    try:
        rows, state["scope"] = collect()
        state["data"] = (rows, "")
        publish(rows)
    except subprocess.TimeoutExpired as e:  # keep the last rows until herdr answers again
        state["data"] = (state["data"][0], f" ⚠ herdr did not answer: {' '.join(e.cmd[1:3])} ·")
    except HerdrError as e:  # e.g. server_not_running: say so instead of showing 0 agents
        state["data"] = (state["data"][0], f" ⚠ herdr: {e} ·")
    except Exception as e:  # an unexpected record or file: report it, keep the last rows, retry
        state["data"] = (state["data"][0], f" ⚠ monitor error: {type(e).__name__}: {one_line(str(e), 120)} ·")


def draw(scr, C, state, rows, warn, cols, spec, view, picked, sel, first, list_h, body, detail, h, w):
    title = f" agent monitor · {state['scope']} · {len(rows)} agents"
    scr.addnstr(0, 0, title, w - 1, curses.A_BOLD)
    full = sum(1 for r in rows if r["pct"] is not None and r["pct"] >= CTX_NOTIFY)
    if full and len(title) < w - 1:
        scr.addnstr(0, len(title), f" · {full} context ≥{CTX_NOTIFY}%", w - 1 - len(title), C["warn"] | curses.A_BOLD)
    for i, x, cw in spec:  # header: picked column underlined, edges are drag handles
        if x < w - 1:
            attr = curses.A_BOLD | (curses.A_UNDERLINE if i == picked else 0)
            scr.addnstr(1, x, fit(cols[i][0], cw), w - 1 - x, attr)
            if x + cw < w - 1:
                scr.addstr(1, x + cw, "│", C["dim"])
    for n, r in enumerate(rows[first:first + list_h]):
        y, rev = n + 2, curses.A_REVERSE if first + n == sel else 0
        for i, x, cw in spec:
            if x < w - 1:
                name = cols[i][0]
                scr.addnstr(y, x, fit(r["cells"][name], cw) + " ", w - 1 - x, cell_attr(C, r, name) | rev)
    if not rows and not warn and list_h > 0:
        scr.addnstr(2, 1, f"no agents in {state['scope']}", w - 2, C["dim"])
    if detail and rows and body - list_h >= 2:
        draw_detail(scr, rows[sel], list_h + 2, body + 3, w, C)
    if view == "usage" and h >= 4:
        scr.addnstr(h - 2, 0, totals(rows), w - 1, curses.A_BOLD)
    hidden = ",".join(c[0] for c in cols if c[1] == -1)
    help_ = (f" [{view}] v: switch view · click row: details · drag │: width"
             f" · ⏎ jump · ←→ +/- space: column · q quit")
    scr.addnstr(h - 1, 0, warn + help_ + (f" · hidden: {hidden}" if hidden else ""), w - 1,
                C["warn"] if warn else C["dim"])


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
        body = max(0, h - 4 if view == "usage" else h - 3)
        list_h = body // 2 if detail and rows else body
        first = max(0, sel - list_h + 1)
        spec = layout(fit_cols(cols, rows), max(1, w - 1))
        scr.erase()
        try:
            if h < 3 or w < 12:  # too small for a table: say so, keep handling keys
                scr.addnstr(0, 0, "agent monitor: pane too small", max(1, w - 1))
            else:
                draw(scr, C, state, rows, warn, cols, spec, view, picked, sel, first, list_h, body, detail, h, w)
        except curses.error:
            pass  # a resize landed mid-draw; the next frame redraws at the new size
        scr.refresh()
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


def open_workspace_args(env):
    """`plugin pane open` argv for a monitor scoped to the action's workspace. A split needs a pane
    to split: the focused pane when it is in that workspace, else the monitor opens as a new tab
    of that workspace."""
    try:
        ctx = json.loads(env.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    ctx = ctx if isinstance(ctx, dict) else {}
    ws = env.get("HERDR_WORKSPACE_ID") or ctx.get("workspace_id")
    pane = env.get("HERDR_PANE_ID") or ctx.get("focused_pane_id") or ctx.get("pane_id")
    args = ["plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", "monitor"]
    if pane and (not ws or pane.split(":")[0] == ws):  # herdr 0.9.5 rejects --workspace with --target-pane
        args += ["--target-pane", pane, "--direction", "right"]
    elif ws:
        args += ["--workspace", ws, "--placement", "tab"]
    return args + ["--env", "AGENT_MONITOR_SCOPE=workspace"]


def open_workspace():
    try:
        print(herdr(*open_workspace_args(os.environ)), end="")
        return 0
    except (HerdrError, subprocess.TimeoutExpired) as e:
        print(f"agent-monitor: {e}", file=sys.stderr)
        return 1


def once(argv):
    """Print one plain frame; exit code 1 with a warning when herdr fails or hangs."""
    width = os.get_terminal_size().columns if sys.stdout.isatty() else 200
    view = "usage" if "--usage" in argv else "activity"
    try:
        rows, scope = collect(wait=True)
    except subprocess.TimeoutExpired as e:
        print(f"⚠ herdr did not answer: {' '.join(e.cmd[1:3])}", file=sys.stderr)
        return 1
    except HerdrError as e:
        print(f"⚠ herdr: {e}", file=sys.stderr)
        return 1
    full = sum(1 for r in rows if r["pct"] is not None and r["pct"] >= CTX_NOTIFY)
    print((f"agent monitor · {scope} · {len(rows)} agents" + (f" · {full} context ≥{CTX_NOTIFY}%" if full else ""))[:width])
    print("\n".join(frame(rows, VIEWS[view], width)))
    if view == "usage":
        print(totals(rows))
    return 0


if __name__ == "__main__":
    if "--open-workspace" in sys.argv:  # the "Agent monitor (this workspace)" action
        sys.exit(open_workspace())
    if "--hook" in sys.argv:  # pane.agent_status_changed: sidebar row and context toast
        sys.exit(hook())
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
