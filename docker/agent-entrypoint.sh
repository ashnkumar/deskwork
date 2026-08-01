#!/usr/bin/env bash
# Bring up the virtual desktop, then hand over to the command.
set -euo pipefail

WIDTH="${DESKWORK_DISPLAY_WIDTH:-1024}"
HEIGHT="${DESKWORK_DISPLAY_HEIGHT:-768}"
DISPLAY_NUM="${DISPLAY:-:99}"
PORTAL_URL="${DESKWORK_PORTAL_URL:-http://portal:8000}"

# The Xvfb geometry and the size reported to the API are the same number on purpose:
# screenshots are then never rescaled and coordinates map 1:1. See SPEC.md section 6.2.
Xvfb "$DISPLAY_NUM" -screen 0 "${WIDTH}x${HEIGHT}x24" -nolisten tcp &
export DISPLAY="$DISPLAY_NUM"

for _ in $(seq 1 50); do
  xdpyinfo >/dev/null 2>&1 && break
  sleep 0.2
done
xdpyinfo >/dev/null 2>&1 || { echo "Xvfb failed to start on $DISPLAY_NUM" >&2; exit 1; }

fluxbox >/dev/null 2>&1 &

# Watch the agent work at http://localhost:6080/vnc.html
x11vnc -display "$DISPLAY_NUM" -forever -shared -nopw -quiet -rfbport 5900 >/dev/null 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &

echo "waiting for the portal at ${PORTAL_URL} ..."
for _ in $(seq 1 60); do
  if python -c "
import sys, urllib.request
try:
    urllib.request.urlopen('${PORTAL_URL}/healthz', timeout=2)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    break
  fi
  sleep 1
done

# Firefox opens straight at the portal, so the agent's first screenshot is the task rather
# than a blank browser it has to navigate out of.
firefox-esr --no-remote --kiosk "$PORTAL_URL" >/dev/null 2>&1 &

echo "desktop ready on $DISPLAY_NUM (${WIDTH}x${HEIGHT}); noVNC on :6080"
exec "$@"
