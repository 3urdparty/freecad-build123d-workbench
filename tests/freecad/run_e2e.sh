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
OUT="$(cd "$ROOT" && FC_CODE_ADDON_DIR="$ROOT" "$FCCMD" tests/freecad/e2e_test.py 2>&1)"
echo "$OUT"

# freecadcmd exit codes are unreliable across versions; trust the marker.
echo "$OUT" | grep -q "E2E RESULT: PASS"
