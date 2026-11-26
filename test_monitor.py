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
