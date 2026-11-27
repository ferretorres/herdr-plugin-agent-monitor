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
assert tot == [12, 260, 50, 0, 10] and ctx == 162 and model == "claude-opus-5-5", (tot, ctx)

# 1h and 5m cache writes are kept apart; zeroed replies take their numbers from iterations[].
from monitor import claude_reply_usage
assert claude_reply_usage({"input_tokens": 5, "cache_creation_input_tokens": 30, "output_tokens": 2,
                           "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20}}) \
    == [5, 0, 10, 20, 2]
assert claude_reply_usage({"input_tokens": 1, "cache_creation_input_tokens": 7}) == [1, 0, 7, 0, 0]
assert claude_reply_usage({"input_tokens": 0, "output_tokens": 0, "iterations": [
    {"input_tokens": 3, "output_tokens": 4}, {"input_tokens": 1, "cache_read_input_tokens": 9}]}) == [4, 9, 0, 0, 4]

# Codex: input_tokens includes cached + cache writes, so uncached input is the remainder.
info = {"total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 700, "cache_write_input_tokens": 100, "output_tokens": 50},
        "last_token_usage": {"input_tokens": 400}, "model_context_window": 1000}
x = codex_usage(jsonl([{"type": "turn_context", "payload": {"model": "gpt-x"}},
                       {"type": "event_msg", "payload": {"type": "token_count", "info": info}}]))
assert (x["in"], x["cr"], x["cw"], x["out"], x["ctx"], x["window"], x["model"]) == (200, 700, 100, 50, 400, 1000, "gpt-x")

# 1M uncached input + 1M output on Opus 5.5 = $4 + $20.
assert cost({"in": 1e6, "cr": 0, "cw": 0, "out": 1e6}, price_for("claude-opus-5-5", PRICES)) == 24.0
# 1M of each: input 4 + cache read 0.20 + 5m write 5 + 1h write 8 + output 20.
assert round(cost({"in": 1e6, "cr": 1e6, "cw": 2e6, "cw5m": 1e6, "cw1h": 1e6, "out": 1e6},
                  price_for("claude-opus-5-5", PRICES)), 6) == 37.2
# A prices.json entry with only in/out gets the default cache multiples instead of failing.
assert round(cost({"in": 0, "cr": 1e6, "cw": 1e6, "out": 0}, {"in": 2, "out": 8}), 6) == 0.2 + 2.5
assert cost({"in": 1, "cr": 0, "cw": 0, "out": 1}, {"in": 2}) is None
# The longest matching key wins.
assert price_for("claude-opus-4-8", dict(PRICES, **{"opus-4": {"in": 99, "out": 99}}))["in"] == 5.0
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

# WORKSPACE, right after ST in both views: the workspace's label, coloured with its own palette.
assert all([c[0] for c in monitor.VIEWS[v]][:3] == ["ST", "WORKSPACE", "PANE"] for v in monitor.VIEWS)
assert rows["w1:p1"]["cells"]["WORKSPACE"] == "one" and rows["w2:p1"]["cells"]["WORKSPACE"] == "two"
CS = {"space": {"red": 101, "teal": 102, "pink": 103}}
assert monitor.cell_attr(CS, rows["w1:p1"], "PANE") == 102            # the agent's own colour
assert monitor.cell_attr(CS, rows["w1:p1"], "WORKSPACE") == 101       # its workspace's colour
assert monitor.cell_attr(CS, rows["w1:p2"], "WORKSPACE") == 101 and monitor.cell_attr(CS, rows["w2:p1"], "WORKSPACE") == 0
bare = {"agents": [agent("w5:p1", tokens={"sc_teal": "●"}), agent("w6:p1")], "workspaces": [{"workspace_id": "w5"}]}
cells = {r["pane"]: r for r in monitor.build_rows(bare, screens=False)}
assert cells["w5:p1"]["cells"]["WORKSPACE"] == "w5" and cells["w6:p1"]["cells"]["WORKSPACE"] == "w6"   # no label: the id
assert monitor.cell_attr(CS, cells["w5:p1"], "WORKSPACE") == 102      # a workspace without a palette: the row's
assert monitor.frame(list(rows.values()), monitor.VIEWS["activity"], 200)[0].startswith("ST WORKSPACE  PANE    AGENT")
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
assert scoped("all", here) is None and scoped("w7", here) == ["w7"]
assert scoped("workspace", here, ws_env="w1") == ["w2"]        # live workspace wins over the launch env
assert scoped("workspace", broken, ws_env="w1") == ["w1"]      # fallback: HERDR_WORKSPACE_ID
assert scoped("workspace", broken) is None                     # nothing known: all
rows = monitor.build_rows(SNAP, "w1")
assert sorted(r["pane"] for r in rows) == ["w1:p1", "w1:p2"]
assert monitor.scope_label("w1", SNAP) == "workspace one (w1)"
assert monitor.scope_label(None, SNAP) == "all workspaces" and "not found" in monitor.scope_label("w9", SNAP)
os.environ["AGENT_MONITOR_SCOPE"] = "workspace"
monitor.HERDR = fake_herdr({"pane current": (0, CURRENT), "agent read": (0, ""),
                            "api snapshot": (0, json.dumps({"result": {"snapshot": SNAP}}))})
rows, label = monitor.collect()[:2]
assert [r["pane"] for r in rows] == ["w2:p1"] and label == "workspace two (w2)", (rows, label)
os.environ.pop("AGENT_MONITOR_SCOPE")
print("ok")

# --- the workspace filter: ALL or an explicit set; without a choice, AGENT_MONITOR_SCOPE ----------
P = monitor.parse_scope
assert P(None) is None and P("") is None and P(" all ") is None and P(" workspace ") == "workspace"
assert P("w2") == ["w2"] and P(" w1 , w3 ,w1,") == ["w1", "w3"] and P(",") is None
assert scoped("w1,w9", here) == ["w1", "w9"]                   # an unknown id stays, for the title to flag
R = monitor.resolve_scope
assert R(None, SNAP, lambda: ["w2"]) == ["w2"]                 # no choice: AGENT_MONITOR_SCOPE
assert R("all", SNAP, lambda: ["w2"]) is None                  # ALL
assert R(["w2", "w1"], SNAP, lambda: None) == ["w1", "w2"]     # snapshot order
assert R(["w9", "w2"], SNAP, lambda: None) == ["w2"]           # a workspace that vanished is dropped
assert R(["w9"], SNAP, lambda: ["w2"]) == ["w2"]               # none left: AGENT_MONITOR_SCOPE again
assert R(["w9"], SNAP, lambda: None) is None
assert sorted(r["pane"] for r in monitor.build_rows(SNAP, ["w1", "w2"])) == ["w1:p1", "w1:p2", "w2:p1"]
assert monitor.build_rows(SNAP, ["w9"]) == [] and [r["pane"] for r in monitor.build_rows(SNAP, ["w2"])] == ["w2:p1"]
odd_ws = {"agents": [agent("w1:p1", workspace_id={"x": 1}), agent("w1:p2", workspace_id=None), agent("w1:p3")],
          "workspaces": [{"workspace_id": ["x"]}, "junk", {"workspace_id": "w1", "label": "one"}]}
assert [r["pane"] for r in monitor.build_rows(odd_ws, ["w1"], screens=False)] == ["w1:p3"]   # odd ids never raise
assert len(monitor.build_rows(odd_ws, screens=False)) == 3

# The refresh applies the picker's choice; a choice needs no `pane current`, a stale one falls back.
SNAP_HERDR = fake_herdr({"pane current": (0, CURRENT), "agent read": (0, ""),
                         "api snapshot": (0, json.dumps({"result": {"snapshot": SNAP}}))})
monitor.HERDR = SNAP_HERDR
os.environ["AGENT_MONITOR_SCOPE"] = "workspace"
calls = []
monitor.herdr = lambda *a, **k: (calls.append(a[:2]), real(*a, **k))[1]
rows, label, shown, spaces = monitor.collect(choice=["w1"])
assert sorted(r["pane"] for r in rows) == ["w1:p1", "w1:p2"] and shown == ["w1"] and label == "workspace one (w1)"
assert ("pane", "current") not in calls, calls
rows, label, shown, spaces = monitor.collect(choice=["w9"])
assert [r["pane"] for r in rows] == ["w2:p1"] and shown == ["w2"] and ("pane", "current") in calls
rows, label, shown, spaces = monitor.collect(choice="all")
assert len(rows) == 3 and (label, shown) == ("all workspaces", None)
# every workspace on the server, in sidebar order, with its live agent count: what the picker lists
assert spaces == [{"id": "w1", "label": "one", "agents": 2}, {"id": "w2", "label": "two", "agents": 1}], spaces
assert monitor.workspace_counts({"workspaces": [{"workspace_id": "w3"}, "junk", {"label": "no id"}],
                                 "agents": [agent("w3:p1"), "junk", {"workspace_id": "w3"}]}) \
    == [{"id": "w3", "label": "", "agents": 1}]
monitor.herdr = real
os.environ["AGENT_MONITOR_SCOPE"] = "w2,w9"                    # an env list, one id unknown
rows, label, shown, spaces = monitor.collect()
assert [r["pane"] for r in rows] == ["w2:p1"] and shown == ["w2", "w9"], (rows, shown)
assert label == "2 workspaces: two, w9 (not found)", label
os.environ.pop("AGENT_MONITOR_SCOPE")

# Title: one workspace as before, a set as "N workspaces: A, B" in sidebar order, ALL as before.
L = monitor.scope_label
assert L(["w1"], SNAP) == "workspace one (w1)" and L(None, SNAP) == "all workspaces"
assert L(["w2", "w1"], SNAP) == "2 workspaces: one, two" and L(["w9"], SNAP) == "workspace w9 (not found)"
assert L(["w1", "w5"], dict(SNAP, workspaces=SNAP["workspaces"] + [{"workspace_id": "w5"}])) == "2 workspaces: one, w5"
T = monitor.title_text
assert T("all workspaces", 3, 80) == "agent monitor · all workspaces · 3 agents"
t = T("3 workspaces: alpha-project, beta-project, gamma-project", 12, 50)
assert len(t) == 50 and t.startswith("agent monitor · 3 workspaces: al") and t.endswith("… · 12 agents"), t
assert T("workspace one (w1)", 1, 5).endswith(" · 1 agents") and T("", 0, 0)   # too narrow: cut by the caller
# Agents past 90% context: the alert ends the title, and the scope is cut first so it still shows.
assert monitor.ctx_alert([{"pct": 95.0}, {"pct": None}, {"pct": 89.9}, {"pct": 90}]) == " · 2 context ≥90%"
assert monitor.ctx_alert([{"pct": None}]) == "" and monitor.ctx_alert([]) == ""
alert = monitor.ctx_alert([{"pct": 95.0}])
t = T("4 workspaces: alpha-project, beta-project, gamma-project, delta-project", 12, 80, alert)
assert len(t) == 80 and t.endswith("… · 12 agents · 1 context ≥90%"), t
assert T("all workspaces", 3, 80, alert) == "agent monitor · all workspaces · 3 agents · 1 context ≥90%"
print("ok")

# --- the w picker: All workspaces, then each workspace; [x] = shown ---------------------------------
SPACES = monitor.workspace_counts(SNAP)
step, lines = monitor.picker_step, monitor.picker_lines
p = monitor.picker_open(["w1"], SPACES)                          # opens on what the monitor shows now
assert lines(p) == ["[ ] All workspaces", "[x] one  w1  2 agents", "[ ] two  w2  1 agent"], lines(p)
p, applied = step(p, "up")
assert p["at"] == 0 and applied is None                          # the first line is the top
for _ in range(5):
    p, _ = step(p, "down")
assert p["at"] == 2                                              # and the last one the bottom
p, _ = step(p, "toggle")
assert lines(p)[1:] == ["[x] one  w1  2 agents", "[x] two  w2  1 agent"]
assert step(p, "apply") == (None, ["w1", "w2"])                  # applied in sidebar order
p, _ = step(p, "toggle")
assert step(p, "apply") == (None, None)                          # back as it opened: nothing to apply
p, _ = step(p, "up")
p, _ = step(p, "toggle")                                         # one off: nothing selected
held, applied = step(p, "apply")
assert applied is None and held is not None and held["hint"]     # Enter keeps the picker open, with a hint
p, _ = step(held, "all")                                         # a: every workspace, and later ones
assert all(l.startswith("[x]") for l in lines(p)) and not p["hint"]
assert step(p, "apply") == (None, "all")
p = monitor.picker_open(["w2"], SPACES)
p, _ = step(p, "toggle")                                         # space on All workspaces: all on
assert p["all"] and lines(p)[0] == "[x] All workspaces"
p, _ = step(p, "toggle")                                         # and all off
assert lines(p) == ["[ ] All workspaces", "[ ] one  w1  2 agents", "[ ] two  w2  1 agent"]
assert step(p, "apply")[0]["hint"]
p = monitor.picker_open(None, SPACES)                            # opened on ALL
p, _ = step(p, "down")
p, _ = step(p, "toggle")                                         # unticking one keeps the others
assert lines(p)[:2] == ["[ ] All workspaces", "[ ] one  w1  2 agents"] and step(p, "apply") == (None, ["w2"])
assert step(monitor.picker_open(None, SPACES), "close") == (None, None)
assert step(monitor.picker_open(["w2", "w9"], SPACES), "apply") == (None, None)   # w9 is not listed
p = monitor.picker_open(["w9"], SPACES)                          # a workspace that vanished is no selection
assert step(p, "apply")[1] is None and step(p, "apply")[0]["hint"]
p, _ = step(dict(p, at=9), "toggle")                             # a cursor past the end stops there
assert p["at"] == 2 and step(p, "apply") == (None, ["w2"])
assert lines(monitor.picker_open(None, [])) == ["[x] All workspaces"]
assert lines(monitor.picker_open(None, [{"id": "w3", "label": "", "agents": 0}]))[1] == "[x]   w3  0 agents"
# It opens on the choice in force, which applies before a refresh shows it: ALL, or its ids that are
# listed; with none of them listed, on what is shown.
assert lines(monitor.picker_open(["w1"], SPACES, "all"))[0] == "[x] All workspaces"
assert lines(monitor.picker_open(["w1"], SPACES, ["w2", "w9"]))[1:] == ["[ ] one  w1  2 agents", "[x] two  w2  1 agent"]
assert step(monitor.picker_open(["w1"], SPACES, ["w2"]), "apply") == (None, None)   # Enter: no change
assert lines(monitor.picker_open(["w1"], SPACES, ["w9"]))[1] == "[x] one  w1  2 agents"
assert lines(monitor.picker_open(None, SPACES, ["w9"]))[0] == "[x] All workspaces"
# It lists the workspaces it opened with, whatever happens to the list it was given.
listed = list(SPACES)
p = monitor.picker_open(["w1"], listed)
listed.pop(0)
assert len(lines(p)) == 3 and step(step(p, "down")[0], "toggle")[0]["ids"] == set()
print("ok")

# --- unchanged around the filter: --once, the hook, what the open monitor publishes ------------------
monitor.HERDR = SNAP_HERDR
os.environ["HERDR_PLUGIN_STATE_DIR"] = tempfile.mkdtemp()
pane_env = os.environ.pop("HERDR_PANE_ID", None)
os.environ["HERDR_PANE_ID"] = "w1:p9"
assert monitor.save_choice("w1:p9", ["w2"])                     # a monitor in this pane chose w2
os.environ["AGENT_MONITOR_SCOPE"] = "w1,w2"
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert monitor.once(["--once"]) == 0
printed = out.getvalue().splitlines()
assert printed[0] == "agent monitor · 2 workspaces: one, two · 3 agents", printed[0]   # the env list, not the choice
assert printed[1].startswith("ST WORKSPACE  PANE") and [l.split()[1] for l in printed[2:]] == ["one", "one", "two"]
os.environ.pop("AGENT_MONITOR_SCOPE")
published = []
real_publish = monitor.publish
monitor.publish = lambda rows: published.append([r["pane"] for r in rows])
os.environ["HERDR_PLUGIN_EVENT_JSON"] = json.dumps({"data": {"pane_id": "w2:p1"}})
assert monitor.hook() == 0 and published == [["w2:p1"]], published   # the hook: only the pane that changed
del os.environ["HERDR_PLUGIN_EVENT_JSON"]
state = {"data": ([], ""), "choice": "all"}
monitor.refresh_once(state)                                     # the open monitor: the rows it shows
assert published[-1] == [r["pane"] for r in state["data"][0]] == ["w1:p1", "w1:p2", "w2:p1"], published
state["choice"] = ["w2"]
monitor.refresh_once(state)
assert published[-1] == ["w2:p1"] and (state["shown"], state["scope"]) == (["w2"], "workspace two (w2)")
# A saved id whose workspace is missing from two refreshes in a row is dropped and the rest saved, so a
# new workspace that herdr later gives that id is not shown by the old choice.
# The refresh thread only marks them; the UI thread, the one writer of the choice, takes them out.
state = {"data": ([], ""), "choice": ["w2", "w7"], "pane": "w1:p9", "env": None}
monitor.refresh_once(state)
assert not monitor.apply_drop(state) and state["choice"] == ["w2", "w7"]    # missing once: kept
monitor.refresh_once(state)
assert state["choice"] == ["w2", "w7"] and state["drop"] == {"w7"}           # marked, not changed
assert monitor.apply_drop(state) and state["choice"] == ["w2"] and monitor.load_choice("w1:p9") == ["w2"]
assert not monitor.apply_drop(state)                                         # taken once
state = {"data": ([], ""), "choice": ["w7"], "pane": "w1:p9", "env": None}
monitor.refresh_once(state)
monitor.refresh_once(state)
assert monitor.apply_drop(state) and state["choice"] is None and monitor.load_choice("w1:p9") is None
state = {"data": ([], ""), "choice": ["w7"], "pane": "w1:p9", "env": None}  # back before the second refresh
monitor.refresh_once(state)
state["choice"] = "all"                                                      # the user picked all meanwhile
monitor.refresh_once(state)
assert not monitor.apply_drop(state) and state["choice"] == "all"
monitor.publish = real_publish
# Two monitors that both show an agent past 90% context send its toast once: the claim is a file.
TWO = ("import sys, monitor\n"
       "monitor.herdr = lambda *a, **k: ''\n"
       "def show(*a):\n"
       "    open(sys.argv[1], 'a').write('toast\\n')\n"
       "    return {'shown': True}\n"
       "monitor.herdr_json = show\n"
       "monitor.publish([{'pane': 'w1:p1', 'agent': 'claude', 'session': '/t.jsonl', 'place': 'w1:p1',\n"
       "                  'cells': {'AGENT': 'lead'}, 'pct': 95.0, 'usd': None, 'tokens': {}, 'labels': {}}])\n")
toasts = os.path.join(os.environ["HERDR_PLUGIN_STATE_DIR"], "toasts.log")
two = [subprocess.Popen([sys.executable, "-c", TWO, toasts], cwd=os.path.dirname(os.path.abspath(__file__)))
       for _ in range(2)]
assert [p.wait(30) for p in two] == [0, 0]
with open(toasts) as f:
    assert f.read() == "toast\n"
os.environ.pop("HERDR_PANE_ID")
if pane_env:
    os.environ["HERDR_PANE_ID"] = pane_env
del os.environ["HERDR_PLUGIN_STATE_DIR"]
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
# w2:p1's lead is in w1: rows go by workspace, so it heads its own workspace's rows
assert order[:5] == [("w1:p2", 0), ("w1:p3", 1), ("w1:p4", 1), ("w1:p5", 2), ("w2:p1", 0)], order
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

# --- second mates: a lead whose crew_mate_of names a first mate's lead pane is listed under it ----------
def member(crew, role="worker", lead=None, mate_of=None):
    t = {"crew_name": crew, "crew_role": role, "crew_lead": lead, "crew_mate_of": mate_of}
    return {k: v for k, v in t.items() if v}


# alpha's lead w1:p2 is the first mate; beta (w2) and gamma (w3) are its second mates, each with a worker.
# Its whole tree is listed in w1's group and beta's blocked worker counts there, so that group comes first,
# though the sidebar has w2 first and w2:p3, outside any crew, is working; in the tree, beta's branch comes
# first. w2:p3 and w3:p3 stay in their own workspaces' groups.
MATES = {"workspaces": [{"workspace_id": "w2", "label": "web"}, {"workspace_id": "w1", "label": "api"},
                        {"workspace_id": "w3", "label": "docs"}],
         "agents": [agent("w1:p2", "claude", tokens=member("alpha", "lead")),
                    agent("w1:p3", tokens=member("alpha", lead="w1:p2")),
                    agent("w2:p1", "claude", tokens=member("beta", "lead", mate_of="w1:p2")),
                    agent("w2:p2", status="blocked", tokens=member("beta", lead="w2:p1")),
                    agent("w2:p3", status="working"),
                    agent("w3:p1", tokens=member("gamma", "lead", mate_of="w1:p2")),
                    agent("w3:p2", tokens=member("gamma", lead="w3:p1")), agent("w3:p3")]}
mated = lambda ws=None, snap=MATES: [(r["pane"], r["depth"]) for r in monitor.build_rows(snap, ws, screens=False)]
by_pane = lambda ws=None: {r["pane"]: r for r in monitor.build_rows(MATES, ws, screens=False)}
assert mated() == [("w1:p2", 0), ("w2:p1", 1), ("w2:p2", 2), ("w1:p3", 1), ("w3:p1", 1), ("w3:p2", 2),
                   ("w2:p3", 0), ("w3:p3", 0)], mated()
rows = by_pane()
assert rows["w2:p1"]["parent"] == rows["w3:p1"]["parent"] == "w1:p2" and rows["w1:p2"]["parent"] is None
assert [rows[p]["cells"]["WORKSPACE"] for p in ("w1:p2", "w2:p1", "w2:p2", "w3:p1")] == ["api", "web", "web", "docs"]
assert rows["w2:p1"]["cells"]["PANE"] == "└ w2:p1" and rows["w2:p2"]["cells"]["PANE"] == "  └ w2:p2"
assert mated(["w1", "w2"]) == [("w1:p2", 0), ("w2:p1", 1), ("w2:p2", 2), ("w1:p3", 1), ("w2:p3", 0)], mated(["w1", "w2"])
# The details panel's CREW line names the link; a first mate counts the second mates shown.
crew_line = lambda rows, pane: dict(rows[pane]["full"])["CREW"]
assert crew_line(rows, "w2:p1") == "beta · lead · second mate of w1:p2", crew_line(rows, "w2:p1")
assert crew_line(rows, "w1:p2") == "alpha · lead · first mate of 2 second mates", crew_line(rows, "w1:p2")
assert crew_line(rows, "w3:p2") == "gamma · worker · lead w3:p1"
assert crew_line(by_pane(["w1", "w2"]), "w1:p2") == "alpha · lead · first mate of 1 second mate"
assert crew_line(by_pane(["w1"]), "w1:p2") == "alpha · lead"
# Without its first mate, a second mate is a root of its own workspace, as before; its CREW line still names it.
# Its blocked worker puts its branch before the working agent w2:p3.
assert mated(["w2"]) == [("w2:p1", 0), ("w2:p2", 1), ("w2:p3", 0)], mated(["w2"])
assert mated(["w3", "w2"]) == [("w2:p1", 0), ("w2:p2", 1), ("w2:p3", 0), ("w3:p1", 0), ("w3:p2", 1), ("w3:p3", 0)]
assert crew_line(by_pane(["w2"]), "w2:p1") == "beta · lead · second mate of w1:p2"
# The crew kit's shape: first mate w1:p1 with four workers, second mates w2:p1..w6:p1 with three each; w2:p1 is
# working and w6's worker w6:p4 is blocked. In a first mate's tree the branch with the most urgent agent comes
# first among its siblings, so w6:p4 is the third row, not the 23rd, and a working second mate's crew does not
# push it down. The same rule holds in w7's crew, which has no mates: w7:p3's branch, with its blocked worker
# w7:p4, comes before w7:p2.
KIT = {"agents": [agent("w1:p1", tokens=member("alpha", "lead"))]
       + [agent(f"w1:p{i}", tokens=member("alpha", lead="w1:p1")) for i in range(2, 6)]
       + [agent(f"w{w}:p{i}", status={(2, 1): "working", (6, 4): "blocked"}.get((w, i), "idle"),
                tokens=member(f"c{w}", lead=f"w{w}:p1") if i > 1 else member(f"c{w}", "lead", mate_of="w1:p1"))
          for w in range(2, 7) for i in range(1, 5)]
       + [agent("w7:p1", tokens=member("solo", "lead")), agent("w7:p2", tokens=member("solo", lead="w7:p1")),
          agent("w7:p3", tokens=member("solo", lead="w7:p1")),
          agent("w7:p4", status="blocked", tokens=member("solo", lead="w7:p3"))]}
kit = mated(snap=KIT)
assert kit[:5] == [("w1:p1", 0), ("w6:p1", 1), ("w6:p4", 2), ("w6:p2", 2), ("w6:p3", 2)], kit
assert [p for p, d in kit[:25] if d == 1] == ["w6:p1", "w2:p1", "w1:p2", "w1:p3", "w1:p4", "w1:p5", "w3:p1",
                                               "w4:p1", "w5:p1"], kit
assert kit[25:] == [("w7:p1", 0), ("w7:p3", 1), ("w7:p4", 2), ("w7:p2", 1)], kit
# A crew_mate_of naming its own lead, a pane that is not a lead (an agent outside a crew, a worker) or a lead
# whose links come back around leaves that lead a root: every row is listed once and nothing loops.
ODD_MATES = {"agents": [agent("w5:p1", tokens=member("one", "lead", mate_of="w5:p1")),
                        agent("w5:p2", tokens=member("two", "lead", mate_of="w5:p3")), agent("w5:p3"),
                        agent("w5:p4", tokens=member("three", "lead", mate_of="w5:p5")),
                        agent("w5:p5", tokens=member("four", lead="w5:p6")), agent("w5:p6", tokens=member("four", "lead")),
                        agent("w6:p1", tokens=member("five", "lead", mate_of="w7:p1")),
                        agent("w7:p1", tokens=member("six", "lead", mate_of="w6:p1")),
                        agent("w7:p2", tokens=member("six", lead="w7:p1")),
                        agent("w8:p1", tokens=member("seven", "lead", mate_of="w6:p1"))]}
assert mated(snap=ODD_MATES) == [("w5:p1", 0), ("w5:p2", 0), ("w5:p3", 0), ("w5:p4", 0), ("w5:p6", 0), ("w5:p5", 1),
                                 ("w6:p1", 0), ("w7:p1", 0), ("w7:p2", 1), ("w8:p1", 0)], mated(snap=ODD_MATES)
# Their CREW lines name no link the table did not draw: to themselves, to a pane that is not a lead, into a loop.
odd_rows = {r["pane"]: r for r in monitor.build_rows(ODD_MATES, screens=False)}
odd_crew = [crew_line(odd_rows, p) for p in ("w5:p1", "w5:p2", "w5:p4", "w6:p1", "w7:p1", "w8:p1")]
assert odd_crew == [f"{c} · lead" for c in ("one", "two", "three", "five", "six", "seven")], odd_crew
# Every branch goes by its most urgent agent, top-level ones included, also where a first mate's tree is
# listed. Idle first mate w1:p5, whose second mate's worker w2:p2 is blocked, comes before the working agent
# w1:p1 and the working crew ops there; when ops' worker w1:p4 is the blocked one, ops comes first instead.
SHARED = {"agents": [agent("w1:p1", status="working"), agent("w1:p5", tokens=member("alpha", "lead")),
                     agent("w1:p2", status="working", tokens=member("ops", "lead")),
                     agent("w1:p3", status="working", tokens=member("ops", lead="w1:p2")),
                     agent("w1:p4", status="working", tokens=member("ops", lead="w1:p2")),
                     agent("w2:p1", tokens=member("beta", "lead", mate_of="w1:p5")),
                     agent("w2:p2", status="blocked", tokens=member("beta", lead="w2:p1"))]}
assert mated(snap=SHARED) == [("w1:p5", 0), ("w2:p1", 1), ("w2:p2", 2), ("w1:p1", 0), ("w1:p2", 0), ("w1:p3", 1),
                              ("w1:p4", 1)], mated(snap=SHARED)
OPS_BLOCKED = {"agents": [dict(a, agent_status={"w1:p4": "blocked", "w2:p2": "idle"}.get(a["pane_id"], a["agent_status"]))
                          for a in SHARED["agents"]]}
assert mated(snap=OPS_BLOCKED) == [("w1:p2", 0), ("w1:p4", 1), ("w1:p3", 1), ("w1:p1", 0), ("w1:p5", 0), ("w2:p1", 1),
                                   ("w2:p2", 2)], mated(snap=OPS_BLOCKED)
# A first mate's own worker in another workspace is listed under it as well, and a chain of links nests one level
# per link (the crew kit makes no such chain, but the table copes with one).
AWAY = {"agents": [agent("w1:p1", tokens=member("alpha", "lead")), agent("w4:p1", tokens=member("alpha", lead="w1:p1")),
                   agent("w2:p1", tokens=member("beta", "lead", mate_of="w1:p1")),
                   agent("w3:p1", tokens=member("gamma", "lead", mate_of="w2:p1"))]}
assert mated(snap=AWAY) == [("w1:p1", 0), ("w2:p1", 1), ("w3:p1", 2), ("w4:p1", 1)], mated(snap=AWAY)
assert mated(["w4"], snap=AWAY) == [("w4:p1", 0)], mated(["w4"], snap=AWAY)
# The hook still reads only the pane that changed: a second mate's row alone, a root.
saved_herdr, real_publish, published = monitor.HERDR, monitor.publish, []
monitor.HERDR = fake_herdr({"api snapshot": (0, json.dumps({"result": {"snapshot": MATES}}))})
monitor.publish = lambda rows: published.append([(r["pane"], r["depth"], r["parent"]) for r in rows])
os.environ["HERDR_PLUGIN_STATE_DIR"] = tempfile.mkdtemp()
os.environ["HERDR_PLUGIN_EVENT_JSON"] = json.dumps({"data": {"pane_id": "w2:p1"}})
assert monitor.hook() == 0 and published == [[("w2:p1", 0, None)]], published
monitor.HERDR, monitor.publish = saved_herdr, real_publish
del os.environ["HERDR_PLUGIN_EVENT_JSON"], os.environ["HERDR_PLUGIN_STATE_DIR"]
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
rows = monitor.build_rows({"agents": [agent("w1:p10"), agent("w1:p2"), agent("w1:p1", status="unknown")]})
assert [r["pane"] for r in rows] == ["w1:p2", "w1:p10", "w1:p1"]    # unknown sorts last
assert rows[-1]["cells"]["ST"] == "◌"
# Rows go by workspace, the one with the most urgent agent first (blocked, working, idle...), then in
# sidebar order (the ones the snapshot does not list after, by id); inside each workspace by status and
# the crew tree. So w1's blocked agent puts w1 ahead of w2, and blocked agents stay at the top.
ORDERED = {"workspaces": [{"workspace_id": "w2", "label": "two"}, {"workspace_id": "w1", "label": "one"}],
           "agents": [agent("w1:p1", status="blocked"), agent("w10:p1"), agent("w3:p1"),
                      agent("w2:p3", tokens={"crew_role": "worker", "crew_lead": "w2:p2"}),
                      agent("w2:p2", tokens={"crew_role": "lead"}), agent("w2:p1", status="working")]}
rows = monitor.build_rows(ORDERED, screens=False)
assert [(r["pane"], r["depth"]) for r in rows] == [("w1:p1", 0), ("w2:p1", 0), ("w2:p2", 0), ("w2:p3", 1),
                                                   ("w3:p1", 0), ("w10:p1", 0)], [(r["pane"], r["depth"]) for r in rows]
assert [r["pane"] for r in monitor.build_rows(ORDERED, ["w1", "w2"], screens=False)] == ["w1:p1", "w2:p1", "w2:p2", "w2:p3"]
assert [r["pane"] for r in monitor.build_rows(ORDERED, ["w2"], screens=False)] == ["w2:p1", "w2:p2", "w2:p3"]
CALM = dict(ORDERED, agents=[agent("w1:p1"), agent("w2:p1")])     # equally urgent: sidebar order
# A workspace label is free text: it is shown on one line.
ODD = {"workspaces": [{"workspace_id": "w1", "label": "two\nlines  here"}], "agents": [agent("w1:p1")]}
assert monitor.build_rows(ODD, screens=False)[0]["cells"]["WORKSPACE"] == "two lines here"
assert monitor.workspace_counts(ODD)[0]["label"] == "two lines here"
assert monitor.scope_label(["w1"], ODD) == "workspace two lines here (w1)"
assert [r["pane"] for r in monitor.build_rows(CALM, screens=False)] == ["w2:p1", "w1:p1"]
print("ok")

# --- caches: truncation, stat-keyed tails, pruning -------------------------------------------------
t = jsonl([a("m1", u(10, 0, 0, 5)), a("m2", u(20, 0, 0, 5))])
assert monitor.claude_file_usage(t)[0] == [30, 0, 0, 0, 10]
with open(t, "w") as f:                       # rewritten shorter: start over, don't stall at the old offset
    f.write(json.dumps(a("m9", u(1, 0, 0, 1))) + "\n")
assert monitor.claude_file_usage(t)[0] == [1, 0, 0, 0, 1]
with open(t, "a") as f:
    f.write(json.dumps(a("m10", u(2, 0, 0, 2))) + "\n")
assert monitor.claude_file_usage(t)[0] == [3, 0, 0, 0, 3]

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

# A columns.json from 0.4.0 gets WORKSPACE after ST and keeps every width it saved.
sd = tempfile.mkdtemp()
os.environ["HERDR_PLUGIN_STATE_DIR"] = sd
OLD_COLS = {"activity": [["ST", 0], ["PANE", 9], ["AGENT", 12], ["AGE", -1], ["GOAL", 30], ["LAST PROMPT", 40], ["NOW", 0]],
            "usage": [["ST", 2], ["PANE", 7], ["AGENT", 14], ["MODEL", 18], ["CTX", 7], ["CTX%", 5], ["IN", 7],
                      ["CACHE R", 8], ["CACHE W", 8], ["OUT", 7], ["COST", 8], ["AGE", 5], ["GOAL", 0]]}
with open(os.path.join(sd, "columns.json"), "w") as f:
    json.dump(OLD_COLS, f)
cols = monitor.load_cols()
WS_WIDTH = dict(monitor.VIEWS["activity"])["WORKSPACE"]
assert WS_WIDTH > 0 and dict(monitor.VIEWS["usage"])["WORKSPACE"] == WS_WIDTH
assert cols["activity"] == [["ST", 0], ["WORKSPACE", WS_WIDTH], ["PANE", 9], ["AGENT", 12], ["AGE", -1],
                            ["GOAL", 30], ["LAST PROMPT", 40], ["NOW", 0]], cols["activity"]
assert cols["usage"] == OLD_COLS["usage"][:1] + [["WORKSPACE", WS_WIDTH]] + OLD_COLS["usage"][1:], cols["usage"]
cols["activity"][1][1] = 14
assert monitor.save_cols(cols) and monitor.load_cols()["activity"][1] == ["WORKSPACE", 14]
# A malformed entry costs only its own width; a column no view has any more is dropped.
with open(os.path.join(sd, "columns.json"), "w") as f:
    json.dump({"activity": [["ST", 3], "junk", 7, ["PANE", "9"], ["AGE", True], ["GOAL", -5], ["NOW"], ["GONE", 4]],
               "usage": 5}, f)
cols = monitor.load_cols()
assert cols["activity"] == [["ST", 3]] + monitor.VIEWS["activity"][1:] and cols["usage"] == monitor.VIEWS["usage"], cols
cols["activity"][1][1] = 99
assert dict(monitor.VIEWS["activity"])["WORKSPACE"] == WS_WIDTH                # defaults are copied, never shared
del os.environ["HERDR_PLUGIN_STATE_DIR"]
print("ok")

# --- the workspace choice: saved per monitor pane in this server's scope file, atomically -----------
sd = tempfile.mkdtemp()
SCOPE = monitor.scope_file()
os.environ["HERDR_PLUGIN_STATE_DIR"] = sd
assert monitor.load_choice("w1:p3") is None                                      # nothing saved yet
assert monitor.save_choice("w1:p3", ["w1", "w2"]) and monitor.save_choice("w2:p1", "all")
assert monitor.load_choice("w1:p3") == ["w1", "w2"] and monitor.load_choice("w2:p1") == "all"
assert sorted(os.listdir(sd)) == [SCOPE, SCOPE[:-5] + ".lock"]                  # no temp file left
assert monitor.save_choice("w1:p3", ["w3"]) and monitor.load_choice("w2:p1") == "all"   # other panes kept
assert monitor.save_choice(None, "all") is False and monitor.save_choice("", ["w1"]) is False
assert monitor.load_choice(None) is None and monitor.load_choice("") is None     # no pane id: nothing
for bad in ("{", "[1, 2]", "null", '{"w1:p3": 5}', '{"w1:p3": []}', '{"w1:p3": "w1"}', '{"w1:p3": [7, ""]}'):
    with open(os.path.join(sd, SCOPE), "w") as f:
        f.write(bad)
    assert monitor.load_choice("w1:p3") is None, bad                             # unreadable: no choice
with open(os.path.join(sd, SCOPE), "w") as f:
    f.write('{"w1:p3": {"scope": null, "choice": ["w1", 7, "", "w1", "w2"]}}')
assert monitor.load_choice("w1:p3") == ["w1", "w2"]
with open(os.path.join(sd, SCOPE), "w") as f:
    f.write("[1, 2]")
assert monitor.save_choice("w1:p3", "all") and monitor.load_choice("w1:p3") == "all"   # a broken file is replaced
# A choice belongs to the AGENT_MONITOR_SCOPE (parse_scope) its monitor ran with: a monitor started with
# another one shows that scope. One entry per pane: the last choice made there.
assert monitor.load_choice("w1:p3", ["w2"]) is None and monitor.load_choice("w1:p3", "workspace") is None
assert monitor.load_choice("w1:p3", None) == "all"
assert monitor.save_choice("w1:p3", ["w1", "w2"], "workspace")                   # the crew's monitor
assert monitor.load_choice("w1:p3", "workspace") == ["w1", "w2"]
assert monitor.load_choice("w1:p3") is None
assert monitor.save_choice("w1:p3", "all", ["w2", "w3"]) and monitor.load_choice("w1:p3", ["w2", "w3"]) == "all"
for bad in ('{"w1:p3": ["w1"]}', '{"w1:p3": "all"}', '{"w1:p3": {"choice": "all"}}', '{"w1:p3": {"scope": null}}',
            '{"w1:p3": {"scope": "w1", "choice": "all"}}', '{"w1:p3": {"scope": null, "choice": 5}}',
            '{"w1:p3": {"scope": null, "choice": []}}', '{"w1:p3": {"scope": null, "choice": "w1"}}',
            '{"w1:p3": {"scope": null, "choice": [7, ""]}}'):
    with open(os.path.join(sd, SCOPE), "w") as f:
        f.write(bad)
    assert monitor.load_choice("w1:p3") is None, bad                             # foreign or partial entry
os.environ["HERDR_PLUGIN_STATE_DIR"] = os.path.join(sd, SCOPE, "x")                  # under a file
assert monitor.save_choice("w1:p3", "all") is False                                     # reported, not raised
del os.environ["HERDR_PLUGIN_STATE_DIR"]
print("ok")

# Every herdr server (named session) numbers its workspaces and panes the same way (w1, w1:p4), and all
# of them share the state dir: a choice is kept per server, so another server's w1:p4 never starts with it.
os.environ["HERDR_PLUGIN_STATE_DIR"] = tempfile.mkdtemp()
real_server = {k: os.environ.pop(k, None) for k in ("HERDR_SOCKET_PATH", "HERDR_SESSION")}
os.environ["HERDR_SOCKET_PATH"] = "/h/.config/herdr/sessions/alpha/herdr.sock"
assert monitor.save_choice("w1:p4", "all")
os.environ["HERDR_SOCKET_PATH"] = "/h/.config/herdr/sessions/beta/herdr.sock"
assert monitor.load_choice("w1:p4") is None                                 # beta's own w1:p4 chose nothing
assert monitor.save_choice("w1:p4", ["w2"]) and monitor.load_choice("w1:p4") == ["w2"]
os.environ["HERDR_SOCKET_PATH"] = "/h/.config/herdr/sessions/alpha/herdr.sock"
assert monitor.load_choice("w1:p4") == "all"                                # and alpha's is kept
del os.environ["HERDR_SOCKET_PATH"]                                         # no socket path: the session name
os.environ["HERDR_SESSION"] = "gamma"
assert monitor.load_choice("w1:p4") is None and monitor.save_choice("w1:p4", ["w3"])
os.environ["HERDR_SESSION"] = "demo"
assert monitor.load_choice("w1:p4") is None
os.environ["HERDR_SESSION"] = "gamma"
assert monitor.load_choice("w1:p4") == ["w3"]
# A closed workspace's id can come back with new panes w3:p1, w3:p2...: a save drops the entries of
# workspaces the server no longer has.
assert monitor.save_choice("w3:p7", ["w1"]) and monitor.save_choice("w2:p1", "all")
assert monitor.save_choice("w1:p4", ["w1", "w2"], live={"w1", "w2"})
assert monitor.load_choice("w3:p7") is None and monitor.load_choice("w2:p1") == "all"
assert monitor.load_choice("w1:p4") == ["w1", "w2"]
# Monitors of one server that save at the same moment keep each other's entries.
RACE = ("import sys, monitor\n"
        "for i in range(300):\n"
        "    assert monitor.save_choice('%s:p%d' % (sys.argv[1], i), ['w1'])\n")
racers = [subprocess.Popen([sys.executable, "-c", RACE, ws], cwd=os.path.dirname(os.path.abspath(__file__)))
          for ws in ("w5", "w6")]
assert [p.wait(60) for p in racers] == [0, 0]
saved = monitor.load_state(monitor.scope_file())
lost = [f"{ws}:p{i}" for ws in ("w5", "w6") for i in range(300) if f"{ws}:p{i}" not in saved]
assert not lost and monitor.load_choice("w2:p1") == "all", (len(lost), lost[:5])
assert not [n for n in os.listdir(os.environ["HERDR_PLUGIN_STATE_DIR"]) if n.endswith(".tmp")]
del os.environ["HERDR_SESSION"], os.environ["HERDR_PLUGIN_STATE_DIR"]
for k, v in real_server.items():
    if v is not None:
        os.environ[k] = v
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
import curses, threading


class FakeScreen:
    """Raises curses.error where real curses does: outside the window, or writing the last cell."""
    def __init__(self, h, w, keys):
        self.h, self.w, self.keys, self.quits = h, w, list(keys), 0

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
        if self.keys:
            return self.keys.pop(0)
        self.quits += 1  # then q until it quits: fail instead of hanging when q stops working
        assert self.quits < 20, "the TUI does not quit on q"
        return ord("q")

    keypad = timeout = nodelay = erase = refresh = lambda self, *a: None


C0 = {"status": {}, "agent": {}, "tool": 0, "says": 0, "screen": 0, "dim": 0, "fresh": 0, "ok": 0, "warn": 0,
      "bad": 0, "space": {}}
saved_fns = (monitor.init_colors, monitor.refresh_forever, curses.curs_set, getattr(curses, "ACS_HLINE", None))
monitor.init_colors = lambda: C0
curses.curs_set = lambda n: None
curses.ACS_HLINE = ord("-")
real_pane_id = os.environ.pop("HERDR_PANE_ID", None)    # the TUI keeps a choice per pane: tests set their own
os.environ["HERDR_PLUGIN_STATE_DIR"] = tempfile.mkdtemp()
crew_rows = monitor.build_rows(CREW)
landed = threading.Event()


class Landed(FakeScreen):
    """Reads no key before the refresh has landed: w opens the picker only once workspaces are listed."""
    def getch(self):
        landed.wait(5)
        return super().getch()


def tiny_refresh(state, every=2.0):
    state.update(data=(crew_rows, ""), scope="workspace one (w1)", shown=["w1"], spaces=SPACES)
    landed.set()


monitor.refresh_forever = tiny_refresh
real_draw_picker, pickers = monitor.draw_picker, []
monitor.draw_picker = lambda *a: (pickers.append(1), real_draw_picker(*a))
for h in range(1, 9):
    for w in (1, 2, 5, 11, 12, 20, 40):
        for keys in ([], [ord("d"), -1], [ord("v"), -1], [ord("v"), ord("d"), -1],
                     [ord("w"), -1], [ord("w"), ord(" "), 10, -1], [ord("v"), ord("w"), ord("j"), ord(" "), -1]):
            landed.clear()
            monitor.tui(Landed(h, w, keys))              # no curses.error escapes
monitor.draw_picker = real_draw_picker
assert pickers                                           # the picker was drawn in those panes too
print("ok")

# --- the w picker in the TUI: keys, the choice saved per monitor pane, restored on start -------------
class Screen(FakeScreen):
    """Waits for the first refresh and keeps the text of the frame on screen at each getch. A callable in
    keys is a step: it runs on the shared state (got["state"]) before the next key is read."""
    def __init__(self, h, w, keys, ready, got):
        super().__init__(h, w, keys)
        self.ready, self.got, self.cells, self.frames = ready, got, {}, []

    def erase(self):
        self.cells = {}

    def addnstr(self, y, x, text, n, attr=0):
        super().addnstr(y, x, text, n, attr)
        self.cells.update(((y, x + i), ch) for i, ch in enumerate(text[:n]))

    def addstr(self, y, x, text, attr=0):
        super().addstr(y, x, text, attr)
        self.cells.update(((y, x + i), ch) for i, ch in enumerate(text))

    def getch(self):
        self.ready.wait(5)
        self.frames.append(["".join(self.cells.get((y, x), " ") for x in range(self.w)).rstrip()
                            for y in range(self.h)])
        while self.keys and callable(self.keys[0]):
            self.keys.pop(0)(self.got["state"])
        return super().getch()


def landing(rows, shown, spaces=None):
    """A step for run_tui's keys: a refresh lands with rows of `shown` among SNAP's workspaces (or spaces)."""
    return lambda state: state.update(data=(rows, ""), scope=monitor.scope_label(shown, SNAP), shown=shown,
                                      spaces=SPACES if spaces is None else spaces)


def run_tui(keys, rows, shown, h=14, w=110):
    """tui() whose first refresh lands at once, showing rows of `shown` among SNAP's workspaces; with rows
    None, only landing() steps in keys bring data.
    -> (screen, {"start": the choice the refresh started with, "state": the shared state}).
    frames[1] is the first frame with the refresh's data, frames[n + 1] the one after the nth key."""
    ready, got = threading.Event(), {}

    def refresh(state, every=2.0):
        got.update(start=state.get("choice"), state=state)
        if rows is not None:
            landing(rows, shown)(state)
        ready.set()
    monitor.refresh_forever = refresh
    scr = Screen(h, w, [-1] + keys, ready, got)
    monitor.tui(scr)
    return scr, got


SD = tempfile.mkdtemp()
os.environ["HERDR_PLUGIN_STATE_DIR"] = SD
os.environ["HERDR_PANE_ID"] = "w1:p9"
real_socket = os.environ.get("HERDR_SOCKET_PATH")
os.environ["HERDR_SOCKET_PATH"] = ALPHA = "/h/.config/herdr/sessions/alpha/herdr.sock"
scope_file = os.path.join(SD, monitor.scope_file())
w1_rows = monitor.build_rows(SNAP, ["w1"], screens=False)

# w opens on what is shown; j j space: w2 ticked; Enter: applied, saved for this pane, refresh woken.
# The save drops the entry of a pane whose workspace (w7) the server no longer has.
assert monitor.save_choice("w7:p1", "all")
scr, got = run_tui([ord("w"), ord("j"), ord("j"), ord(" "), 10], w1_rows, ["w1"])
assert scr.frames[2][2:5] == [" [ ] All workspaces", " [x] one  w1  2 agents", " [ ] two  w2  1 agent"], scr.frames[2]
assert "space toggle" in scr.frames[2][-1] and "esc close" in scr.frames[2][-1]      # the picker's keys
with open(scope_file) as f:
    assert json.load(f) == {"w1:p9": {"scope": None, "choice": ["w1", "w2"]}}
assert got["state"]["choice"] == ["w1", "w2"] and got["state"]["wake"].is_set()
assert "w workspaces" in scr.frames[-1][-1] and "space toggle" not in scr.frames[-1][-1], scr.frames[-1]
# The same pane's monitor starts with it again; another pane has none of its own.
assert run_tui([], w1_rows, ["w1"])[1]["start"] == ["w1", "w2"]
os.environ["HERDR_PANE_ID"] = "w2:p4"
assert run_tui([], w1_rows, ["w1"])[1]["start"] is None
os.environ["HERDR_SOCKET_PATH"], os.environ["HERDR_PANE_ID"] = "/h/.config/herdr/sessions/beta/herdr.sock", "w1:p9"
assert run_tui([], w1_rows, ["w1"])[1]["start"] is None             # nor does another server's w1:p9
os.environ["HERDR_SOCKET_PATH"], os.environ["AGENT_MONITOR_SCOPE"] = ALPHA, "w2"
assert run_tui([], w1_rows, ["w1"])[1]["start"] is None             # nor one started with another scope,
scr, got = run_tui([ord("w"), ord("a"), 10], w1_rows, ["w1"])       # which keeps what it picks for that scope
assert run_tui([], w1_rows, ["w1"])[1]["start"] == "all"
with open(scope_file) as f:
    assert json.load(f)["w1:p9"] == {"scope": ["w2"], "choice": "all"}
del os.environ["AGENT_MONITOR_SCOPE"]
assert run_tui([], w1_rows, ["w1"])[1]["start"] is None
os.environ["HERDR_PANE_ID"] = "w2:p4"
with open(scope_file) as f:
    before = f.read()
# Esc, w and q close it without changing anything; the next q quits.
for close in ([27, -1], [ord("w")], [ord("q")]):
    scr, got = run_tui([ord("w"), ord("j"), ord(" ")] + close, w1_rows, ["w1"])
    after = scr.frames[4 + len(close)]                         # the frame right after the closing key
    assert got["state"]["choice"] is None and "space toggle" not in after[-1], (close, after)
    assert len(scr.frames) == 5 + len(close), close
# Enter with nothing selected keeps it open, with a hint.
scr, got = run_tui([ord("w"), ord("j"), ord(" "), 10], w1_rows, ["w1"])
assert "nothing selected" in scr.frames[5][-1] and " [ ] one  w1  2 agents" in scr.frames[5], scr.frames[5]
assert got["state"]["choice"] is None
# Mouse input never reaches the table under the picker; without the picker a click opens details.
click = [27] + [ord(c) for c in "[<0;5;3M"]                       # press on the first agent row
scr, got = run_tui([ord("w")] + click + [27, -1], w1_rows, ["w1"])
assert not any("esc close, ⏎ jump" in line for frame in scr.frames for line in frame)
scr, got = run_tui(click, w1_rows, ["w1"])
assert any("esc close, ⏎ jump" in line for line in scr.frames[-1]), scr.frames[-1]
with open(scope_file) as f:
    assert f.read() == before
# Dragging the WORKSPACE column's edge resizes it and saves the width.
spec = monitor.layout(monitor.fit_cols(monitor.load_cols()["activity"], w1_rows), 109)
x, cw = next((x, cw) for i, x, cw in spec if i == 1)
mouse = lambda b, mx, end: [27] + [ord(c) for c in f"[<{b};{mx + 1};2{end}"]
scr, got = run_tui(mouse(0, x + cw, "M") + mouse(32, x + cw + 4, "M") + mouse(0, x + cw + 4, "m"), w1_rows, ["w1"])
assert monitor.load_cols()["activity"][1] == ["WORKSPACE", cw + 4], monitor.load_cols()["activity"]
# The picker takes no mouse input, so a drag still going when w opens it ends there, as its release would:
# after esc, a click on a row opens its details instead of resizing the column.
x, cw = next((x, cw) for i, x, cw in monitor.layout(monitor.fit_cols(monitor.load_cols()["activity"], w1_rows), 109)
             if i == 1)
row_click = click + [27] + [ord(c) for c in "[<0;5;3m"]
scr, got = run_tui(mouse(0, x + cw, "M") + [ord("w")] + mouse(0, x + cw, "m") + [27, -1] + row_click, w1_rows, ["w1"])
assert monitor.load_cols()["activity"][1] == ["WORKSPACE", cw], monitor.load_cols()["activity"]
assert any("esc close, ⏎ jump" in line for line in scr.frames[-1]), scr.frames[-1]
# Reopened before the refresh it woke has landed, the picker shows the choice in force, not the rows still
# drawn: both workspaces ticked, so unticking one applies the other alone.
os.environ["HERDR_PANE_ID"] = "w1:p5"
apply_both = [ord("w"), ord("j"), ord("j"), ord(" "), 10]
scr, got = run_tui(apply_both + [ord("w"), 10], w1_rows, ["w1"])
assert scr.frames[7][2:5] == [" [ ] All workspaces", " [x] one  w1  2 agents", " [x] two  w2  1 agent"], scr.frames[7]
assert got["state"]["choice"] == ["w1", "w2"] and monitor.load_choice("w1:p5") == ["w1", "w2"]   # Enter: no change
scr, got = run_tui(apply_both[:1] + [ord("a"), 10, ord("w"), ord(" "), ord("j"), ord(" "), 10], w1_rows, ["w1"])
assert got["state"]["choice"] == ["w1"], got["state"]["choice"]                  # from ALL: all off, one on
# Before the first refresh lands nothing is known to list, so w opens nothing; once it lands, w opens on
# what is shown and an explicit ALL applies.
os.environ["HERDR_PANE_ID"] = "w1:p6"
scr, got = run_tui([ord("w"), landing(w1_rows, ["w1"]), -1, ord("w"), ord("a"), 10], None, None)
assert not any("workspaces to show" in line for line in scr.frames[2]), scr.frames[2]
assert scr.frames[4][1:4] == [" workspaces to show", " [ ] All workspaces", " [x] one  w1  2 agents"], scr.frames[4]
assert got["state"]["choice"] == "all" and monitor.load_choice("w1:p6") == "all"
# The picker keeps the workspaces it listed while it is open, so a refresh never moves a line under the
# cursor: w1 closes while the cursor is on two, and space still ticks two.
os.environ["HERDR_PANE_ID"] = "w1:p7"
THREE = SPACES + [{"id": "w3", "label": "three", "agents": 1}]
scr, got = run_tui([landing(w1_rows, ["w1"], THREE), -1, ord("w"), ord("j"), ord("j"),
                    landing(w1_rows, ["w1"], THREE[1:]), ord(" "), 10], None, None)
assert scr.frames[6][2:6] == [" [ ] All workspaces", " [x] one    w1  2 agents", " [x] two    w2  1 agent",
                              " [ ] three  w3  1 agent"], scr.frames[6]
assert got["state"]["choice"] == ["w1", "w2"], got["state"]["choice"]
# When a refresh brings other rows (a new choice, an agent that turned blocked), the selection and its
# details stay on the same agent while it is listed.
SEL = {"agents": [agent("w1:p1", status="blocked"), agent("w1:p3"), agent("w2:p1", status="blocked"), agent("w3:p2")],
       "workspaces": [{"workspace_id": "w1"}, {"workspace_id": "w2"}, {"workspace_id": "w3"}]}
every_row = monitor.build_rows(SEL, screens=False)
assert [r["pane"] for r in every_row] == ["w1:p1", "w1:p3", "w2:p1", "w3:p2"]
details = lambda frame: next((line.strip() for line in frame if "esc close, ⏎ jump" in line), None)
scr, got = run_tui([ord("j"), ord("j"), ord("d"), landing(monitor.build_rows(SEL, ["w2", "w3"], screens=False), None),
                    -1, ord("k"), landing(every_row, None), -1], every_row, None)
assert details(scr.frames[4]).startswith("w2:p1 · pi · blocked") and details(scr.frames[5]) == details(scr.frames[4])
assert details(scr.frames[6]).startswith("w2:p1") and details(scr.frames[7]).startswith("w2:p1"), scr.frames[7]
os.environ["HERDR_PANE_ID"] = "w2:p4"
# Title and empty state for a set of workspaces.
scr, got = run_tui([], [], ["w1", "w2"])
assert scr.frames[1][0] == " agent monitor · 2 workspaces: one, two · 0 agents", scr.frames[1][0]
assert scr.frames[1][2] == " no agents in 2 workspaces: one, two", scr.frames[1]
# A narrow pane cuts the scope, never the context alert; --once prints the alert once.
hot = [dict(r, pct=95.0) if r["pane"] == "w1:p1" else r for r in monitor.build_rows(SNAP, ["w1", "w2"], screens=False)]
scr, got = run_tui([], hot, ["w1", "w2"], w=50)
assert scr.frames[1][0] == " agent monitor · 2 w… · 3 agents · 1 context ≥90%", scr.frames[1][0]
real_collect = monitor.collect
monitor.collect = lambda wait=False, choice=None: (hot, "2 workspaces: one, two", ["w1", "w2"], SPACES)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert monitor.once(["--once"]) == 0
assert out.getvalue().splitlines()[0] == "agent monitor · 2 workspaces: one, two · 3 agents · 1 context ≥90%"
monitor.collect = real_collect
# Without HERDR_PANE_ID the choice applies to this run only.
os.environ.pop("HERDR_PANE_ID")
os.environ["HERDR_PLUGIN_STATE_DIR"] = SD2 = tempfile.mkdtemp()
scr, got = run_tui([ord("w"), ord("a"), 10], w1_rows, ["w1"])
assert got["state"]["choice"] == "all" and os.listdir(SD2) == []
del os.environ["HERDR_PLUGIN_STATE_DIR"], os.environ["HERDR_SOCKET_PATH"]
if real_pane_id is not None:
    os.environ["HERDR_PANE_ID"] = real_pane_id
if real_socket is not None:
    os.environ["HERDR_SOCKET_PATH"] = real_socket
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

# --- pi sessions -----------------------------------------------------------------------------------
pu = lambda i, cr, cw, o, usd=0.0: {"input": i, "cacheRead": cr, "cacheWrite": cw, "output": o,
                                     "totalTokens": i + cr + cw + o, "cost": {"total": usd}}
pi_file = jsonl([
    {"type": "session", "version": 3, "id": "s1", "cwd": "/w"},
    {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "document the API"}]}},
    {"type": "message", "message": {"role": "assistant", "model": "gpt-x", "provider": "prov", "stopReason": "toolUse",
                                    "usage": pu(100, 1000, 0, 50, 0.25),
                                    "content": [{"type": "toolCall", "name": "read", "arguments": {"path": "docs/api.md"}}]}},
    {"type": "message", "message": {"role": "toolResult", "toolName": "subagent", "usage": pu(10, 0, 0, 5, 0.05)}},
])
monitor._pu.clear()
x = monitor.pi_usage(pi_file)
assert (x["in"], x["cr"], x["out"], x["ctx"], x["model"]) == (110, 1000, 55, 1150, "gpt-x"), x
assert round(x["usd"], 6) == 0.30 and x["usd_src"] == "pi"
assert monitor.pi_activity(pi_file)[:2] == ("document the API", "read: docs/api.md")
with open(pi_file, "a") as f:  # after a compaction the context is unknown until the next reply
    f.write(json.dumps({"type": "compaction", "usage": pu(5, 0, 0, 5)}) + "\n")
assert monitor.pi_usage(pi_file)["ctx"] == 0 and monitor.pi_usage(pi_file)["in"] == 115
with open(pi_file, "a") as f:
    f.write(json.dumps({"type": "message", "message": {"role": "assistant", "model": "gpt-x", "stopReason": "stop",
                                                       "usage": pu(1, 2, 0, 3)}}) + "\n")
assert monitor.pi_usage(pi_file)["ctx"] == 6
monitor._pu.clear()
assert monitor.pi_usage(jsonl([{"type": "message", "message": {"role": "assistant", "usage": pu(1, 0, 0, 1)}}]))["usd"] is None
d = tempfile.mkdtemp()
os.makedirs(os.path.join(d, "sessions", "--w--"))
open(os.path.join(d, "sessions", "--w--", "2026-01-01T00-00-00-000Z_abc.jsonl"), "w").close()
os.environ["PI_CODING_AGENT_DIR"] = d
assert monitor.pi_path("id", "abc").endswith("_abc.jsonl") and monitor.pi_path("path", "/nope.jsonl") is None
del os.environ["PI_CODING_AGENT_DIR"]
print("ok")

# --- opencode database ---------------------------------------------------------------------------
import sqlite3
oc_home = tempfile.mkdtemp()
os.makedirs(os.path.join(oc_home, "opencode"))
db = os.path.join(oc_home, "opencode", "opencode.db")
w = sqlite3.connect(db)
w.execute("pragma journal_mode=wal")
w.execute("create table session (id text primary key, parent_id text, directory text, cost real, tokens_input int,"
          " tokens_output int, tokens_reasoning int, tokens_cache_read int, tokens_cache_write int,"
          " time_created int, time_updated int, time_archived int)")
w.execute("create table message (id text primary key, session_id text, time_created int, time_updated int, data text)")
w.execute("create table part (id text primary key, message_id text, session_id text, time_created int,"
          " time_updated int, data text)")
S = "insert into session values (?,?,?,?,?,?,?,?,?,?,?,null)"
w.execute(S, ("ses_A", None, "/x", 1.5, 100, 50, 10, 1000, 200, 1, 2000))
w.execute(S, ("ses_B", "ses_A", "/x", 0.5, 10, 5, 0, 100, 20, 1, 1500))
w.execute("insert into message values ('msg_1','ses_A',1,1,?)", (json.dumps({"role": "user"}),))
w.execute("insert into message values ('msg_2','ses_A',2,2,?)", (json.dumps(
    {"role": "assistant", "providerID": "anthropic", "modelID": "claude-opus-4-7",
     "tokens": {"input": 5, "output": 7, "reasoning": 1, "cache": {"read": 900, "write": 10}}}),))
w.execute("insert into part values ('prt_1','msg_1','ses_A',1,1,?)", (json.dumps({"type": "text", "text": "fix the bug"}),))
w.execute("insert into part values ('prt_0','msg_1','ses_A',1,1,?)",
          (json.dumps({"type": "text", "text": "<ctx>", "synthetic": True}),))
w.execute("insert into part values ('prt_2','msg_2','ses_A',3,3,?)", (json.dumps(
    {"type": "tool", "tool": "bash", "state": {"status": "running", "input": {"command": "ls"}}}),))
w.commit()  # the writer stays open, as opencode's does
os.environ["XDG_DATA_HOME"] = oc_home
oc_agent = {"pane_id": "w1:p9", "agent": "opencode", "cwd": "/x", "agent_session": {"kind": "id", "value": "ses_A"}}
prompt, now, ts, u = monitor.opencode_read(oc_agent)
assert (prompt, now, ts) == ("fix the bug", "bash: ls (running)", 2.0), (prompt, now, ts)
assert (u["in"], u["out"], u["cr"], u["cw"], u["ctx"], u["subagents"], u["usd"]) == (110, 65, 1100, 220, 923, 1, 2.0), u
assert monitor.opencode_read(dict(oc_agent, agent_session={}))[3]["in"] == 110      # found by folder
assert monitor.opencode_read(dict(oc_agent, cwd="/y", agent_session={})) is None
w.execute("alter table session rename to session_v2")
w.commit()
assert monitor.opencode_read(oc_agent) is None                                       # unknown schema: screen
del os.environ["XDG_DATA_HOME"]
w.close()
print("ok")

# --- sidebar row ---------------------------------------------------------------------------------
from monitor import sidebar_band, sidebar_update, metadata_args, PAD
assert sidebar_band({"pct": 49, "usd": 9}) == "am_ok" and sidebar_band({"pct": None, "usd": None}) is None
assert sidebar_band({"pct": 50, "usd": 0}) == "am_warn" and sidebar_band({"pct": 12, "usd": 55}) == "am_bad"
assert sidebar_band({"pct": None, "usd": 17}) == "am_warn"
row = {"pane": "w1:p1", "pct": 91.2, "usd": 159.3, "tokens": {"crew_role": "lead"},
       "labels": {"idle": PAD * 2 + "○ ★ lead · claude"}}
args = sidebar_update(row, "warn")
assert args == ["pane", "report-metadata", "w1:p1", "--source", "ferretorres.agent-monitor",
                "--token", "am_bad=" + PAD * 4 + "91% $159"], args
assert sidebar_update(dict(row, tokens={"am_bad": PAD * 4 + "91% $159"}), "warn") is None    # unchanged: no write
moved = sidebar_update(dict(row, pct=55, usd=3.4, tokens={"am_bad": "x"}), "warn")
assert moved[-4:] == ["--token", "am_warn=" + PAD * 4 + "55% $3.4", "--clear-token", "am_bad"], moved
assert sidebar_update(dict(row, pct=10, usd=1, tokens={"am_warn": "x"}), "warn")[-2:] == ["--clear-token", "am_warn"]
assert sidebar_update(dict(row, pct=10, usd=1, tokens={}), "all")[-1] == "am_ok=" + PAD * 4 + "10% $1.0"
assert sidebar_update(dict(row, tokens={"am_bad": "x"}), "off")[-2:] == ["--clear-token", "am_bad"]
assert sidebar_update(dict(row, labels={}), "warn")[-1] == "am_bad=" + PAD * 2 + "91% $159"
for bad in ({"crew_role": "x"}, {"sc_blue": "x"}):
    try:
        metadata_args("w1:p1", bad, [])
        raise AssertionError("wrote a foreign key")
    except ValueError:
        pass
assert not any(f in sidebar_update(row, "warn") for f in ("--state-label", "--title", "--display-agent"))
print("ok")

# --- context toast ---------------------------------------------------------------------------------
os.environ["HERDR_PLUGIN_STATE_DIR"] = tempfile.mkdtemp()
sent = []
monitor.herdr_json = lambda *a: (sent.append(a), {"shown": True})[1]
r = {"pane": "w1:p1", "agent": "claude", "session": "/t.jsonl", "place": "billing · main · w1:p1",
     "cells": {"AGENT": "lead"}, "pct": 91.0, "usd": None, "tokens": {}, "labels": {}}
monitor.herdr = lambda *a, **k: ""
monitor.publish([r]); monitor.publish([dict(r, pct=93.0)])
assert len(sent) == 1 and sent[0][2] == "lead context 91%" and "billing · main · w1:p1" in sent[0], sent
monitor.publish([dict(r, pct=40.0)]); monitor.publish([dict(r, pct=95.0)])           # re-armed below 50
assert len(sent) == 2
monitor.herdr_json = lambda *a: (sent.append(a), {"shown": False, "reason": "rate_limited"})[1]
monitor.publish([dict(r, session="/u.jsonl")]); monitor.publish([dict(r, session="/u.jsonl")])
assert len(sent) == 4                                                                   # retried after rate limit
os.environ["AGENT_MONITOR_NOTIFY"] = "0"
monitor.publish([dict(r, session="/v.jsonl")])
assert len(sent) == 4
del os.environ["AGENT_MONITOR_NOTIFY"], os.environ["HERDR_PLUGIN_STATE_DIR"]
print("ok")

# --- cost wording ----------------------------------------------------------------------------------
base = {"usage": {"in": 1, "cr": 0, "cw": 0, "out": 1}}
assert monitor.totals([dict(base, usd=1.0, usd_src="list")]).endswith("≈ $1.00 list price")
assert "list price + agent-reported" in monitor.totals([dict(base, usd=1.0, usd_src="list"), dict(base, usd=2.0, usd_src="pi")])
u = {"in": 1, "cr": 2, "cw": 30, "cw5m": 10, "cw1h": 20, "out": 3, "ctx": 6, "model": "m", "subagents": 0}
assert "(1h 20 · 5m 10)" in monitor.usage_text(u, None, 1.0) and "priced apart" in monitor.usage_text(u, None, 1.0)
assert "as recorded by pi" in monitor.usage_text(dict(u, cw1h=0), None, 1.0, "pi")
print("ok")

# --- review fixes --------------------------------------------------------------------------------
# Malformed prices.json never breaks a refresh; a partial override keeps the built-in ctx.
cfg = tempfile.mkdtemp()
os.environ["HERDR_PLUGIN_CONFIG_DIR"] = cfg
for bad in ("null", "[1, 2]", '{"opus-5-5": 5}', '{"opus-5-5": {"in": "3", "out": null, "ctx": "1"}}'):
    with open(os.path.join(cfg, "prices.json"), "w") as f:
        f.write(bad)
    p = monitor.price_for("claude-opus-5-5", monitor.load_prices())
    assert p["in"] == 4.0 and p["ctx"] == 1_000_000, (bad, p)
with open(os.path.join(cfg, "prices.json"), "w") as f:
    f.write('{"opus-5-5": {"in": 3}}')
p = monitor.price_for("claude-opus-5-5", monitor.load_prices())
assert (p["in"], p["out"], p["ctx"]) == (3, 20.0, 1_000_000)
del os.environ["HERDR_PLUGIN_CONFIG_DIR"]

# A line the reader cannot use is skipped once; earlier replies are never counted twice.
t = jsonl([{"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 100}}}, [], "x",
           {"type": "assistant", "message": "not an object"},
           {"type": "assistant", "message": {"id": "m2", "usage": {"input_tokens": 100}}}])
monitor._cu.clear(); monitor._new.clear()
assert monitor.claude_file_usage(t)[0][0] == 200 and monitor.claude_file_usage(t)[0][0] == 200

# LAST PROMPT survives being pushed out of the tail by large attachments.
t = jsonl([{"type": "user", "message": {"content": "fix the login bug"}},
           {"type": "attachment", "blob": "x" * 500_000},
           {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 1},
                                              "content": [{"type": "text", "text": "ok"}]}}])
monitor._cu.clear()
assert monitor.claude_activity(t)[0] == "" and monitor.claude_usage(t)["prompt"] == "fix the login bug"

# A toast herdr declined (toasts off, nobody attached) is retried later, not lost.
os.environ["HERDR_PLUGIN_STATE_DIR"] = tempfile.mkdtemp()
sent = []
answers = [{"shown": False, "reason": "no_foreground_client"}, {"shown": True}]
monitor.herdr_json = lambda *a: (sent.append(a), answers.pop(0))[1]
r = dict(r, session="/declined.jsonl", pct=92.0)
monitor.publish([r]); monitor.publish([r])
assert len(sent) == 1                                     # not before TOAST_RETRY_S
monitor.TOAST_RETRY_S = -1
monitor.publish([r]); monitor.publish([r])
assert len(sent) == 2                                     # retried once, then shown: no more
monitor.TOAST_RETRY_S = 60

# The hook keeps its read position between runs.
t = jsonl([{"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 7}}}])
monitor._cu.clear(); monitor._new.clear()
monitor.claude_file_usage(t)
cache = os.path.join(os.environ["HERDR_PLUGIN_STATE_DIR"], "reads", "p.json")
monitor.save_reads(cache)
monitor._cu.clear(); monitor._new.clear()
monitor.load_reads(cache)
assert monitor._new[t][1] > 0 and monitor.claude_file_usage(t)[0][0] == 7
del os.environ["HERDR_PLUGIN_STATE_DIR"]
print("ok")

# --- second-round fixes ----------------------------------------------------------------------------
# last-prompt records never replace the real latest user line.
t = jsonl([{"type": "user", "message": {"content": "newest real prompt"}},
           {"type": "last-prompt", "lastPrompt": "an older, cut prompt"}])
monitor._cu.clear()
assert monitor.claude_usage(t)["prompt"] == "newest real prompt"
# Odd values are dropped before they reach the running state.
t = jsonl([{"type": "assistant", "message": {"id": "m1", "model": 7, "usage": {"input_tokens": "12"}}}])
monitor._cu.clear()
u = monitor.claude_usage(t)
assert u["model"] == "" and u["in"] == 0
t = jsonl([{"type": "event_msg", "payload": {"type": "token_count", "info": {
    "total_token_usage": {"input_tokens": "12"}, "last_token_usage": "x", "model_context_window": "9"}}}])
monitor._xu.clear()
assert monitor.codex_usage(t)["in"] == 0 and monitor.codex_usage(t)["window"] is None
assert monitor.claude_activity(jsonl([{"type": "user", "message": "x"},
                                      {"type": "assistant", "message": {"content": ["y"]}}]))[:2] == ("", "")
# A prices.json entry with no usable number leaves the built-in price alone.
cfg = tempfile.mkdtemp()
with open(os.path.join(cfg, "prices.json"), "w") as f:
    f.write('{"claude-opus-5-5": {"in": "4.0"}}')
os.environ["HERDR_PLUGIN_CONFIG_DIR"] = cfg
assert monitor.price_for("claude-opus-5-5", monitor.load_prices())["in"] == 4.0
del os.environ["HERDR_PLUGIN_CONFIG_DIR"]
# A reader that fails costs only its own row.
real_read = monitor.read_agent
monitor.read_agent = lambda *a: (_ for _ in ()).throw(TypeError("boom"))
rows = monitor.build_rows({"agents": [{"pane_id": "w1:p1", "agent": "zzz", "agent_status": "idle"}]}, screens=False)
assert rows[0]["cells"]["NOW"].startswith("monitor: could not read") and rows[0]["usage"] is None
monitor.read_agent = real_read
print("ok")
