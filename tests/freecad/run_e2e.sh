#!/usr/bin/env bash
# Run the headless end-to-end test under FreeCADCmd.
# Usage: tests/freecad/run_e2e.sh   (or FREECADCMD=/path/to/freecadcmd tests/freecad/run_e2e.sh)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

FCCMD="${FREECADCMD:-}"
if [ -z "$FCCMD" ]; then
  CANDIDATES=(
    freecadcmd
    FreeCADCmd
    /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd
    /Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd
    /usr/lib/freecad/bin/FreeCADCmd
  )
  for c in "${CANDIDATES[@]}"; do
    if command -v "$c" >/dev/null 2>&1; then FCCMD="$(command -v "$c")"; break; fi
    if [ -x "$c" ]; then FCCMD="$c"; break; fi
  done
fi
if [ -z "$FCCMD" ]; then
  echo "freecadcmd not found — set FREECADCMD=/path/to/freecadcmd" >&2
  exit 2
fi

echo "using: $FCCMD"

# Stream through tee rather than capturing into a variable: freecadcmd's
# stdout is a pipe here, so Python block-buffers it, and a crash mid-run
# would otherwise discard every line the test had printed — which is how a
# segfault turns into a bare "exit code 1" with no output at all.
LOG="$(mktemp -t fc_code_e2e_log.XXXXXX)"
VERDICT="$(mktemp -t fc_code_e2e_verdict.XXXXXX)"
trap 'rm -f "$LOG" "$VERDICT"' EXIT
# FreeCAD's progress indicator writes carriage returns, which overwrite our
# result lines in log viewers that honour them (GitHub Actions does) — the
# lines are in the stream but unreadable. Fold CR to LF so nothing can hide.
(cd "$ROOT" && FC_CODE_ADDON_DIR="$ROOT" FC_CODE_E2E_LOG="$VERDICT" PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 \
    "$FCCMD" tests/freecad/e2e_test.py 2>&1) | tr '\r' '\n' | tee "$LOG"
fc_status=${PIPESTATUS[0]}

echo "freecadcmd exit status: $fc_status"
if [ "$fc_status" -ge 128 ]; then
  echo "freecadcmd died on signal $((fc_status - 128)) — output above may be truncated" >&2
fi

# Report both sources unconditionally. Duplicated output beats a failure you
# cannot read, and printing the verdict's line count is what distinguishes
# "the test said FAIL" from "the test never wrote its verdict".
echo "--- verdict file: $(wc -l < "$VERDICT" | tr -d ' ') line(s) ---"
cat "$VERDICT"
echo "--- marker lines in the stream ---"
grep -a "E2E RESULT" "$LOG" || echo "(none)"

# freecadcmd exit codes are unreliable across versions; trust the marker,
# from whichever source carried it.
grep -q "E2E RESULT: PASS" "$VERDICT" || grep -q "E2E RESULT: PASS" "$LOG"
