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

# Workspace colours follow herdr-plugin-space-colors: FNV-1a 64 test vector, rules before the hash.
from monitor import SPACE_ACCENTS, choose_palette, fnv1a, nearest_256
assert fnv1a("") == 0xcbf29ce484222325 and fnv1a("a") == 0xaf63dc4c8601ec8c
names = sorted(SPACE_ACCENTS)
assert choose_palette("x", "/a/b", SPACE_ACCENTS, [], True) == names[fnv1a("/a/b") % 8]
rules = [{"path": "/a", "palette": "red"}, {"path": "/a/b", "palette": "teal"}, {"label": "L", "palette": "pink"}]
assert choose_palette("x", "/a/b/c", SPACE_ACCENTS, rules, True) == "teal"     # longest path wins
assert choose_palette("L", "/a/b/c", SPACE_ACCENTS, rules, True) == "pink"     # label beats path
assert choose_palette("x", "/zzz", SPACE_ACCENTS, rules, False) is None        # auto off, no rule
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
                      capture_output=True, text=True).stdout.strip() == "/opt/x/herdr"
env.pop("HERDR_BIN_PATH")
assert subprocess.run([sys.executable, "-c", "import monitor; print(monitor.HERDR)"], env=env,
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
# no token anywhere: space-colors' hash of the first non-empty pane cwd in that workspace
assert rows["w2:p1"]["space"] == names[fnv1a("/b/c") % 8], rows["w2:p1"]["space"]
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

# A pane whose screen herdr can't read in time shows a note instead of crashing the monitor.
monitor.herdr = lambda *a: (_ for _ in ()).throw(subprocess.TimeoutExpired(["herdr", *a], 5))
assert monitor.screen_activity("w1:p1")[1] == "screen: (herdr did not answer in 5 s)"
print("ok")

# The background refresh keeps the last rows and sets a warning when herdr times out.
def boom():
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
