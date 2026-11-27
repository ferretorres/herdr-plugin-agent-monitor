#!/bin/sh
# Validation gate for gated-ship. The plugin is one script and one plain-assert test file with no
# pyproject.toml, so the generic gate cannot detect it. The whole suite runs on every Python here
# that the CI matrix covers; --focus, --slice and --no-focus are accepted and need no subset.
cd "$(dirname "$0")/.." || { echo "GATE: FAIL (setup) — cannot cd to the repository"; exit 1; }
ran=0
for py in /usr/bin/python3 python3; do
  command -v "$py" >/dev/null 2>&1 || continue
  if ! "$py" test_monitor.py >/dev/null; then
    echo "GATE: FAIL (pass_to_pass) — test_monitor.py failed on $("$py" -V 2>&1)"
    exit 1
  fi
  ran=$((ran + 1))
done
[ "$ran" -gt 0 ] || { echo "GATE: FAIL (setup) — no python3 found"; exit 1; }
if ! command -v uvx >/dev/null 2>&1 || ! uvx --offline ruff --version >/dev/null 2>&1; then
  echo "GATE: FAIL (wall) — ruff is not available offline; run \"uvx ruff --version\" once online"
  exit 1
fi
uvx --offline ruff check --select F monitor.py test_monitor.py >/dev/null \
  || { echo "GATE: FAIL (wall) — ruff F checks failed"; exit 1; }
echo "GATE: PASS"
