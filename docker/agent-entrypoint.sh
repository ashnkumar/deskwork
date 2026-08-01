#!/usr/bin/env bash
# Bring up the virtual desktop, then hand over to the command.
#
# Every step is verified. An earlier version backgrounded Fluxbox, x11vnc, websockify and
# Firefox and then printed "desktop ready" unconditionally — `set -e` does not fire for a
# backgrounded process that exits, and the portal wait loop fell through after 60 failed
# attempts without stopping. A container with no browser at all therefore looked healthy,
# and the failure only surfaced later as an agent staring at a blank screen.
set -euo pipefail

WIDTH="${DESKWORK_DISPLAY_WIDTH:-1024}"
HEIGHT="${DESKWORK_DISPLAY_HEIGHT:-768}"
DISPLAY_NUM="${DISPLAY:-:99}"
PORTAL_URL="${DESKWORK_PORTAL_URL:-http://portal:8000}"

die() { echo "entrypoint: $*" >&2; exit 1; }

# Wait for a condition or fail loudly. $1 = attempts (~seconds), $2 = description.
wait_for() {
  local attempts=$1 what=$2; shift 2
  for _ in $(seq 1 "$attempts"); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  die "timed out waiting for ${what}"
}

alive() { kill -0 "$1" 2>/dev/null; }

# The Xvfb geometry and the size reported to the API are the same number on purpose:
# screenshots are then never rescaled and coordinates map 1:1. See SPEC.md section 6.2.
Xvfb "$DISPLAY_NUM" -screen 0 "${WIDTH}x${HEIGHT}x24" -nolisten tcp &
XVFB_PID=$!
export DISPLAY="$DISPLAY_NUM"
wait_for 15 "Xvfb on ${DISPLAY_NUM}" xdpyinfo
alive "$XVFB_PID" || die "Xvfb exited immediately"

fluxbox >/dev/null 2>&1 &
FLUXBOX_PID=$!

# Watch the agent work at http://localhost:6080/vnc.html
x11vnc -display "$DISPLAY_NUM" -forever -shared -nopw -quiet -rfbport 5900 >/dev/null 2>&1 &
X11VNC_PID=$!
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
WEBSOCKIFY_PID=$!

sleep 1
alive "$FLUXBOX_PID"    || die "fluxbox exited immediately"
alive "$X11VNC_PID"     || die "x11vnc exited immediately"
alive "$WEBSOCKIFY_PID" || die "websockify exited immediately (is /usr/share/novnc present?)"

echo "waiting for the portal at ${PORTAL_URL} ..."
portal_up() {
  python - "$PORTAL_URL" <<'PY'
import sys, urllib.request
urllib.request.urlopen(sys.argv[1] + "/healthz", timeout=2)
PY
}
wait_for 60 "the portal at ${PORTAL_URL}" portal_up

# Firefox opens straight at the portal, so the agent's first screenshot is the task rather
# than a blank browser it has to navigate out of.
firefox-esr --no-remote --kiosk "$PORTAL_URL" >/dev/null 2>&1 &
FIREFOX_PID=$!

# Wait for a mapped window, not just a live process. Without this the first screenshot can
# land before the fresh profile is built and the kiosk window painted, and the agent's
# opening move is to stare at an empty desktop and start guessing.
wait_for 45 "a Firefox window" xdotool search --onlyvisible --class '(?i)firefox'
alive "$FIREFOX_PID" || die "Firefox exited immediately"

echo "desktop ready on $DISPLAY_NUM (${WIDTH}x${HEIGHT}); noVNC on :6080"
exec "$@"
