import json, tempfile
from monitor import PRICES, claude_file_usage, codex_usage, cost, fit, is_human, layout, parse_mouse, price_for

assert is_human("fix the bug")
assert not is_human("<system-reminder>x") and not is_human("Base directory for this skill: /x")
cols = [["A", 4], ["B", 0], ["C", -1]]          # fixed, fill, hidden
assert layout(cols, 40) == [(0, 0, 4), (1, 5, 34)]
assert fit("abcdef", 4) == "abc…" and fit("ab", 4) == "ab  "
assert parse_mouse("[<0;240;1M") == (0, 239, 0, True)   # press beyond col 223
assert parse_mouse("[<32;12;1M") == (32, 11, 0, True)   # drag
assert parse_mouse("[<0;5;3m")[3] is False              # release
assert parse_mouse("[A") is None


def jsonl(objs, partial=""):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write("".join(json.dumps(o) + "\n" for o in objs) + partial)
    f.close()
    return f.name


# Claude: one reply split over two lines (same id) counts once; a half-written line is skipped.
u = lambda i, cr, cw, o: {"input_tokens": i, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw, "output_tokens": o}
a = lambda mid, us: {"type": "assistant", "message": {"id": mid, "model": "claude-opus-5-5", "usage": us}}
p = jsonl([a("m1", u(10, 100, 50, 5)), a("m1", u(10, 100, 50, 7)), a("m2", u(2, 160, 0, 3))], partial='{"type": "assi')
tot, ctx, model = claude_file_usage(p)
assert tot == [12, 260, 50, 10] and ctx == 162 and model == "claude-opus-5-5", (tot, ctx)

# Codex: input_tokens includes cached + cache writes, so uncached input is the remainder.
info = {"total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 700, "cache_write_input_tokens": 100, "output_tokens": 50},
        "last_token_usage": {"input_tokens": 400}, "model_context_window": 1000}
x = codex_usage(jsonl([{"type": "turn_context", "payload": {"model": "gpt-x"}},
                       {"type": "event_msg", "payload": {"type": "token_count", "info": info}}]))
assert (x["in"], x["cr"], x["cw"], x["out"], x["ctx"], x["window"], x["model"]) == (200, 700, 100, 50, 400, 1000, "gpt-x")

# 1M uncached input + 1M output on Opus 5.5 = $4 + $20.
assert cost({"in": 1e6, "cr": 0, "cw": 0, "out": 1e6}, price_for("claude-opus-5-5", PRICES)) == 24.0
assert price_for("gpt-x", PRICES) is None
print("ok")

from monitor import nearest_256
assert nearest_256("#ff0000") == 196 and nearest_256("#000000") == 16
print("ok")

# --- herdr calls -------------------------------------------------------------------------------
import os, stat, sys, subprocess, monitor

FAKE_DIR = tempfile.mkdtemp()


def fake_herdr(answers, sleep=None):
    """An executable that answers like herdr: answers maps 'arg0 arg1' -> (exit code, stdout)."""
    path = os.path.join(FAKE_DIR, f"herdr{len(os.listdir(FAKE_DIR))}")
    with open(path, "w") as f:
        f.write(f"#!{sys.executable}\nimport sys, time\nA = {answers!r}\nS = {sleep!r}\n"
                "key = ' '.join(sys.argv[1:3])\n"
                "if S and key in S: time.sleep(S[key])\n"
                "rc, out = A.get(key, (1, '{\"error\":{\"code\":\"unknown_command\",\"message\":\"' + key + '\"}}'))\n"
                "sys.stdout.write(out)\nsys.exit(rc)\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


NOT_RUNNING = '{"id":"x","error":{"code":"server_not_running","message":"no herdr server is running"}}'

# HERDR_BIN_PATH picks the binary; without it, "herdr" from PATH.
env = dict(os.environ, HERDR_BIN_PATH="/opt/x/herdr")
assert subprocess.run([sys.executable, "-c", "import monitor; print(monitor.HERDR)"], env=env,
                      cwd=os.path.dirname(os.path.abspath(__file__)),
                      capture_output=True, text=True).stdout.strip() == "/opt/x/herdr"
env.pop("HERDR_BIN_PATH")
assert subprocess.run([sys.executable, "-c", "import monitor; print(monitor.HERDR)"], env=env,
                      cwd=os.path.dirname(os.path.abspath(__file__)),
                      capture_output=True, text=True).stdout.strip() == "herdr"

# A non-zero exit with herdr's JSON error becomes HerdrError("code: message"), not an empty list.
monitor.HERDR = fake_herdr({"agent list": (1, NOT_RUNNING)})
try:
    monitor.herdr_json("agent", "list")
    raise AssertionError("expected HerdrError")
except monitor.HerdrError as e:
    assert str(e) == "server_not_running: no herdr server is running", e
monitor.HERDR = "/nonexistent/herdr"
try:
    monitor.herdr("agent", "list")
    raise AssertionError("expected HerdrError")
except monitor.HerdrError as e:
    assert "cannot run" in str(e)

# The refresh shows the server error as a warning and keeps the last rows.
monitor.HERDR = fake_herdr({"agent list": (1, NOT_RUNNING), "api snapshot": (1, NOT_RUNNING)})
state = {"data": (["row"], "")}
monitor.refresh_once(state)
assert state["data"][0] == ["row"] and "server_not_running" in state["data"][1], state

# --once prints the warning and exits 1 instead of a traceback or an empty table.
import contextlib, io
err = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
    assert monitor.once(["--once"]) == 1
assert "server_not_running" in err.getvalue()
print("ok")

# --- one snapshot per refresh, colours from space-colors tokens ----------------------------------
def agent(pane, kind="pi", status="idle", **kw):
    return dict({"pane_id": pane, "agent": kind, "agent_status": status, "workspace_id": pane.split(":")[0],
                 "cwd": "/tmp/" + pane}, **kw)


SNAP = {"agents": [agent("w1:p1", tokens={"sc_teal": "●"}), agent("w1:p2"), agent("w2:p1")],
        "panes": [{"pane_id": "w1:p2", "workspace_id": "w1", "cwd": "/a", "tokens": {"sc_pink": "●"}},
                  {"pane_id": "w2:p1", "workspace_id": "w2", "cwd": "", "tokens": {}},
                  {"pane_id": "w2:p9", "workspace_id": "w2", "cwd": "/b/c"}],
        "workspaces": [{"workspace_id": "w1", "label": "one", "tokens": {"sc_red": "●"}},
                       {"workspace_id": "w2", "label": "two"}], "tabs": []}
monitor.HERDR = fake_herdr({"api snapshot": (0, json.dumps({"id": "s", "result": {"type": "session_snapshot", "snapshot": SNAP}})),
                            "agent read": (0, "some screen output here\n")})
calls = []
real = monitor.herdr
monitor.herdr = lambda *a, **k: (calls.append(a[:2]), real(*a, **k))[1]
rows = {r["pane"]: r for r in monitor.collect()[0]}
assert sum(1 for c in calls if c != ("agent", "read")) == 1, calls          # only `api snapshot`
monitor.herdr = real
assert rows["w1:p1"]["space"] == "teal"     # the agent's own token
assert rows["w1:p2"]["space"] == "pink"     # else the pane's token
assert rows["w2:p1"]["space"] is None      # no token anywhere: no colour
assert monitor.sc_palette({"x": "1"}, {"sc_my-own": "●"}) == "my-own" and monitor.sc_palette({}) is None
print("ok")

# --- AGENT_MONITOR_SCOPE -------------------------------------------------------------------------
def scoped(value, herdr_path, ws_env=None):
    os.environ["AGENT_MONITOR_SCOPE"] = value
    os.environ.pop("HERDR_WORKSPACE_ID", None)
    if ws_env:
        os.environ["HERDR_WORKSPACE_ID"] = ws_env
    monitor.HERDR = herdr_path
    try:
        return monitor.scope_workspace()
    finally:
        os.environ.pop("AGENT_MONITOR_SCOPE")
        os.environ.pop("HERDR_WORKSPACE_ID", None)


CURRENT = json.dumps({"id": "c", "result": {"type": "pane_info", "pane": {"pane_id": "w2:p3", "workspace_id": "w2"}}})
here = fake_herdr({"pane current": (0, CURRENT)})
broken = fake_herdr({"pane current": (1, NOT_RUNNING)})
assert monitor.scope_workspace() is None                       # unset = all
assert scoped("all", here) is None and scoped("w7", here) == "w7"
assert scoped("workspace", here, ws_env="w1") == "w2"          # live workspace wins over the launch env
assert scoped("workspace", broken, ws_env="w1") == "w1"        # fallback: HERDR_WORKSPACE_ID
assert scoped("workspace", broken) is None                     # nothing known: all
rows = monitor.build_rows(SNAP, "w1")
assert sorted(r["pane"] for r in rows) == ["w1:p1", "w1:p2"]
assert monitor.scope_label("w1", SNAP) == "workspace one (w1)"
assert monitor.scope_label(None, SNAP) == "all workspaces" and "not found" in monitor.scope_label("w9", SNAP)
os.environ["AGENT_MONITOR_SCOPE"] = "workspace"
monitor.HERDR = fake_herdr({"pane current": (0, CURRENT), "agent read": (0, ""),
                            "api snapshot": (0, json.dumps({"result": {"snapshot": SNAP}}))})
rows, label = monitor.collect()
assert [r["pane"] for r in rows] == ["w2:p1"] and label == "workspace two (w2)", (rows, label)
os.environ.pop("AGENT_MONITOR_SCOPE")
print("ok")

# --- crew hierarchy, agent name, title ------------------------------------------------------------
def crew(role, lead=None):
    t = {"crew_role": role, "crew_name": "kit"}
    return dict(t, crew_lead=lead) if lead else t


CREW = {"agents": [agent("w1:p4", tokens=crew("worker", "w1:p2"), name="alpha-tests"),
                   agent("w1:p2", "claude", "working", tokens=crew("lead"), title="Ship the kit",
                         terminal_title_stripped="user@host:/x"),
                   agent("w1:p3", tokens=crew("worker", "w1:p2"), terminal_title_stripped="user@host:/y"),
                   agent("w1:p5", tokens=crew("worker", "w1:p4")),          # a worker's worker
                   agent("w2:p1", tokens=crew("worker", "w1:p2")),          # its lead is out of scope
                   agent("w3:p1", tokens=crew("worker", "w3:p2")), agent("w3:p2", tokens=crew("worker", "w3:p1"))],
        "panes": [], "workspaces": []}
rows = monitor.build_rows(CREW)
order = [(r["pane"], r["depth"]) for r in rows]
assert order[:5] == [("w1:p2", 0), ("w1:p3", 1), ("w1:p4", 1), ("w1:p5", 2), ("w2:p1", 1)], order
assert order[5:] == [("w3:p1", 0), ("w3:p2", 1)], order    # a lead cycle is cut, every row shown once
cells = {r["pane"]: r["cells"] for r in rows}
assert cells["w1:p3"]["PANE"] == "└ w1:p3" and cells["w1:p5"]["PANE"] == "  └ w1:p5"
assert cells["w1:p4"]["AGENT"] == "alpha-tests" and cells["w1:p3"]["AGENT"] == "pi"   # name, else kind
assert cells["w1:p2"]["GOAL"] == "Ship the kit" and cells["w1:p3"]["GOAL"] == "user@host:/y"
assert dict(next(r for r in rows if r["pane"] == "w1:p4")["full"])["CREW"] == "kit · worker · lead w1:p2"
# out of scope: the worker becomes a root
assert [(r["pane"], r["depth"]) for r in monitor.build_rows(CREW, "w2")] == [("w2:p1", 0)]
# PANE widens for the deepest indented id; the header row shows it whole
assert dict(monitor.fit_cols(monitor.VIEWS["activity"], rows))["PANE"] == len("  └ w1:p5")
assert "  └ w1:p5 " in "\n".join(monitor.frame(rows, monitor.VIEWS["activity"], 200))
print("ok")

# --- transcript lookup, sort, glyphs --------------------------------------------------------------
home = tempfile.mkdtemp()
for d in (".claude/projects/a", ".claude-alt/projects/b", "elsewhere/projects/c", ".codex/sessions/2026/11/26"):
    os.makedirs(os.path.join(home, d))
for f in (".claude-alt/projects/b/S1.jsonl", "elsewhere/projects/c/S2.jsonl",
          ".codex/sessions/2026/11/26/rollout-2026-11-26T10-00-00-XID.jsonl"):
    open(os.path.join(home, f), "w").write('{"payload": {"cwd": "/w"}}\n')
monitor.HOME, real_home = home, monitor.HOME
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.environ["CODEX_HOME"] = os.path.join(home, ".codex")
assert monitor.claude_path("id", "S1") == os.path.join(home, ".claude-alt/projects/b/S1.jsonl")   # ~/.claude*
assert monitor.claude_path("id", "S2") is None
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(home, "elsewhere")
assert monitor.claude_path("id", "S2") == os.path.join(home, "elsewhere/projects/c/S2.jsonl")      # CLAUDE_CONFIG_DIR
os.environ.pop("CLAUDE_CONFIG_DIR")
direct = os.path.join(home, "elsewhere/projects/c/S2.jsonl")
assert monitor.claude_path("path", direct) == direct and monitor.claude_path("path", direct + "x") is None
rollout = os.path.join(home, ".codex/sessions/2026/11/26/rollout-2026-11-26T10-00-00-XID.jsonl")
assert monitor.codex_path("/nope", "id", "XID") == rollout          # by session id
assert monitor.codex_path("/w") == rollout                          # by cwd
assert monitor.codex_path("/w", "path", rollout) == rollout
monitor.HOME = real_home
os.environ.pop("CODEX_HOME")

ids = ["w10:p1", "w2:p10", "w2:p9", "w1:p1"]
assert sorted(ids, key=monitor.pane_key) == ["w1:p1", "w2:p9", "w2:p10", "w10:p1"]
rows = monitor.build_rows({"agents": [agent("w10:p1"), agent("w2:p1"), agent("w1:p1", status="unknown")]})
assert [r["pane"] for r in rows] == ["w2:p1", "w10:p1", "w1:p1"]    # unknown sorts last
assert rows[-1]["cells"]["ST"] == "◌"
print("ok")

# --- caches: truncation, stat-keyed tails, pruning -------------------------------------------------
t = jsonl([a("m1", u(10, 0, 0, 5)), a("m2", u(20, 0, 0, 5))])
assert monitor.claude_file_usage(t)[0] == [30, 0, 0, 10]
with open(t, "w") as f:                       # rewritten shorter: start over, don't stall at the old offset
    f.write(json.dumps(a("m9", u(1, 0, 0, 1))) + "\n")
assert monitor.claude_file_usage(t)[0] == [1, 0, 0, 1]
with open(t, "a") as f:
    f.write(json.dumps(a("m10", u(2, 0, 0, 2))) + "\n")
assert monitor.claude_file_usage(t)[0] == [3, 0, 0, 3]

parsed = []
probe = lambda path: parsed.append(path) or len(parsed)
assert monitor.cached_tail(t, probe) == 1 and monitor.cached_tail(t, probe) == 1   # unchanged: no re-parse
with open(t, "a") as f:
    f.write("{}\n")
assert monitor.cached_tail(t, probe) == 2                                         # grew: parsed again

gone = jsonl([a("m1", u(1, 0, 0, 1))])
monitor._touched.clear()
monitor.claude_file_usage(gone)
monitor.claude_activity(gone)
monitor.prune_caches()                      # this refresh used it: kept
assert gone in monitor._new and gone in monitor._cu and gone in monitor._tail
monitor.claude_file_usage(t)
monitor.prune_caches()                      # next refresh did not: dropped from every cache
assert gone not in monitor._new and gone not in monitor._cu and gone not in monitor._tail and t in monitor._cu
print("ok")

# --- screen reads: background, one per pane, only when the pane changed ----------------------------
import time
monitor._screen.clear()
monitor.HERDR = fake_herdr({"agent read": (0, "slow pane finally answered\n")}, sleep={"agent read": 2})
snap = {"agents": [agent("w1:p1", revision=1), agent("w1:p2", revision=1)]}
t0 = time.time()
rows = monitor.build_rows(snap)
assert time.time() - t0 < 1.0, "a slow `agent read` must not stall the refresh"
assert rows[0]["cells"]["NOW"] == "screen: (reading…)"
rows = monitor.build_rows(snap, wait=True)                      # --once waits for the reads
assert rows[0]["cells"]["NOW"] == "screen: slow pane finally answered", rows[0]["cells"]["NOW"]

reads = []
real_screen = monitor.screen_activity
monitor.screen_activity = lambda pane: reads.append(pane) or ("", "screen: x", None)
monitor.build_rows(snap, wait=True)
assert reads == [], reads                                       # same revision, fresh: no read
snap["agents"][0]["revision"] = 2
monitor.build_rows(snap, wait=True)
assert reads == ["w1:p1"], reads                                # only the pane that changed
monitor._screen["w1:p2"]["at"] -= monitor.SCREEN_MAX_AGE + 1
monitor.build_rows(snap, wait=True)
assert reads == ["w1:p1", "w1:p2"], reads                       # and old reads are refreshed
monitor.build_rows({"agents": [agent("w1:p1", revision=2)]})
assert "w1:p2" not in monitor._screen                           # gone pane forgotten
monitor.screen_activity = real_screen
print("ok")

# --- column widths: plugin state dir, atomic write, no crash ---------------------------------------
sd = tempfile.mkdtemp()
os.environ["HERDR_PLUGIN_STATE_DIR"] = os.path.join(sd, "plugin")
assert monitor.state_dir() == os.path.join(sd, "plugin")
cols = monitor.load_cols()
cols["activity"][3][1] = 9
assert monitor.save_cols(cols)
assert os.listdir(os.path.join(sd, "plugin")) == ["columns.json"]          # no temp file left
assert monitor.load_cols()["activity"][3][1] == 9
os.environ.pop("HERDR_PLUGIN_STATE_DIR")
os.environ["XDG_STATE_HOME"] = os.path.join(sd, "xdg")
assert monitor.state_dir() == os.path.join(sd, "xdg", "herdr", "plugins", "ferretorres.agent-monitor")
open(os.path.join(sd, "blocker"), "w").close()
os.environ["XDG_STATE_HOME"] = os.path.join(sd, "blocker")                  # a file, not a dir
assert monitor.save_cols(cols) is False                                    # reported, not raised
os.environ.pop("XDG_STATE_HOME")
print("ok")

# --- odd records after a server restart never kill the refresh -------------------------------------
monitor.HERDR = fake_herdr({"agent read": (0, "")})
odd = {"agents": [{"pane_id": "w1:p7", "workspace_id": "w1", "agent_status": "idle"},   # no "agent"
                  {"pane_id": "w1:p8", "workspace_id": "w1"},                          # no kind, no status
                  {"workspace_id": "w1"}, "junk", agent("w1:p1")]}
rows = monitor.build_rows(odd)
assert sorted(r["pane"] for r in rows) == ["w1:p1", "w1:p7", "w1:p8"], [r["pane"] for r in rows]
assert next(r for r in rows if r["pane"] == "w1:p8")["cells"]["ST"] == "◌"
real_collect = monitor.collect
monitor.collect = lambda *a, **k: (_ for _ in ()).throw(KeyError("agent"))
state = {"data": (["row"], "")}
monitor.refresh_once(state)                                   # any exception: warning, rows kept
assert state["data"][0] == ["row"] and "KeyError" in state["data"][1], state
monitor.collect = real_collect
print("ok")

# --- tiny and narrow panes: the TUI keeps running ---------------------------------------------------
import curses


class FakeScreen:
    """Raises curses.error where real curses does: outside the window, or writing the last cell."""
    def __init__(self, h, w, keys):
        self.h, self.w, self.keys = h, w, list(keys)

    def getmaxyx(self):
        return self.h, self.w

    def _check(self, y, x, n):
        if not (0 <= y < self.h and 0 <= x < self.w) or n < 0 or (y == self.h - 1 and x + n >= self.w):
            raise curses.error("out of window")

    def addnstr(self, y, x, text, n, attr=0):
        self._check(y, x, min(len(text), n) if n >= 0 else len(text))

    def addstr(self, y, x, text, attr=0):
        self._check(y, x, len(text))

    def hline(self, y, x, ch, n):
        self._check(y, x, n)

    def getch(self):
        return self.keys.pop(0) if self.keys else ord("q")

    keypad = timeout = nodelay = erase = refresh = lambda self, *a: None


C0 = {"status": {}, "agent": {}, "tool": 0, "says": 0, "screen": 0, "dim": 0, "fresh": 0, "ok": 0, "warn": 0,
      "bad": 0, "space": {}}
saved_fns = (monitor.init_colors, monitor.refresh_forever, curses.curs_set, getattr(curses, "ACS_HLINE", None))
monitor.init_colors = lambda: C0
curses.curs_set = lambda n: None
curses.ACS_HLINE = ord("-")
crew_rows = monitor.build_rows(CREW)
monitor.refresh_forever = lambda state, every=2.0: state.update(data=(crew_rows, ""), scope="workspace one (w1)")
for h in range(1, 9):
    for w in (1, 2, 5, 11, 12, 20, 40):
        for keys in ([], [ord("d"), -1], [ord("v"), -1], [ord("v"), ord("d"), -1]):
            monitor.tui(FakeScreen(h, w, keys))          # no curses.error escapes
monitor.init_colors, monitor.refresh_forever, curses.curs_set = saved_fns[:3]
print("ok")

# --- "Agent monitor (this workspace)" action --------------------------------------------------------
ctx = json.dumps({"workspace_id": "w1", "focused_pane_id": "w1:p4"})
args = monitor.open_workspace_args({"HERDR_WORKSPACE_ID": "w1", "HERDR_PLUGIN_CONTEXT_JSON": ctx})
assert args[-2:] == ["--env", "AGENT_MONITOR_SCOPE=workspace"]
assert "--workspace" not in args and args[7:11] == ["--target-pane", "w1:p4", "--direction", "right"], args
other = json.dumps({"workspace_id": "w2", "focused_pane_id": "w1:p4"})            # focus is elsewhere
args = monitor.open_workspace_args({"HERDR_PLUGIN_CONTEXT_JSON": other})
assert args[7:11] == ["--workspace", "w2", "--placement", "tab"], args
assert monitor.open_workspace_args({"HERDR_PLUGIN_CONTEXT_JSON": "nope"})[7:] == ["--env", "AGENT_MONITOR_SCOPE=workspace"]
print("ok")

# A pane whose screen herdr can't read in time shows a note instead of crashing the monitor.
monitor.herdr = lambda *a: (_ for _ in ()).throw(subprocess.TimeoutExpired(["herdr", *a], 5))
assert monitor.screen_activity("w1:p1")[1] == "screen: (herdr did not answer in 5 s)"
print("ok")

# The background refresh keeps the last rows and sets a warning when herdr times out.
def boom(*a, **k):
    raise subprocess.TimeoutExpired(["herdr", "agent", "list"], 5)
monitor.collect = boom
state = {"data": (["row"], "")}
monitor.refresh_once(state)
assert state["data"][0] == ["row"] and "agent list" in state["data"][1]
print("ok")

# --once reports a hung herdr instead of a traceback.
err = io.StringIO()
with contextlib.redirect_stderr(err):
    assert monitor.once(["--once"]) == 1
assert "did not answer" in err.getvalue()
print("ok")

# prices.json in the plugin config folder adds models and overrides the built-in prices.
cfg = tempfile.mkdtemp()
with open(os.path.join(cfg, "prices.json"), "w") as f:
    json.dump({"my-gateway-model": {"in": 1, "out": 2, "cache_read": 0, "cache_write": 0}}, f)
os.environ["HERDR_PLUGIN_CONFIG_DIR"] = cfg
assert monitor.price_for("my-gateway-model-v2", monitor.load_prices())["out"] == 2
del os.environ["HERDR_PLUGIN_CONFIG_DIR"]
os.environ["XDG_CONFIG_HOME"] = os.path.join(cfg, "xdg")
assert monitor.config_dir() == os.path.join(cfg, "xdg", "herdr", "plugins", "config", "ferretorres.agent-monitor")
del os.environ["XDG_CONFIG_HOME"]
print("ok")
