#!/bin/bash
# Fails unless the runner runs as its own non-admin account, isolated from the
# Owner's: the Owner's home is closed to it, the runner folder (with this
# probe and the hook) is not writable, and the LAN gateway does not answer
# (pf drops its packets: no connection and no refusal). job-started.sh runs
# it before every job, so a runner started as the Owner (e.g. ./run.sh by
# hand) or without the pf block fails every job.
set -u
fail() { echo "::error::runner isolation check failed: $1"; exit 1; }
: "${OWNER_HOME:?OWNER_HOME is not set; run tools/mac-runner/install.sh}"
root=$(cd "$(dirname "$0")/.." && pwd -P) || fail "cannot find the runner root"

denied() { ! "$@" >/dev/null 2>&1; }
test -d "$OWNER_HOME" || fail "OWNER_HOME is not a folder"
test "$(id -u)" != "$(stat -f %u "$OWNER_HOME")" || fail "the runner runs as the Owner"
id -Gn | tr ' ' '\n' | grep -qxE 'admin|staff' && fail "the runner account is in admin or staff"
denied ls "$OWNER_HOME" || fail "the Owner's home is readable"
denied touch "$root/.isolation-probe" || fail "the runner folder is writable"
denied touch "$root/isolation/job-started.sh" || fail "the hook is writable"
test -w "$root/_work" || fail "the runner work folder is not writable"
# A dropped packet times out; a reachable host connects or refuses at once.
gateway=$(/sbin/route -n get default 2>/dev/null | awk '/gateway:/ {print $2}')
case "$gateway" in
  10.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|192.168.*)
    /usr/bin/python3 -I -c '
import socket, sys
for port in (53, 80, 443):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect((sys.argv[1], port)); sys.exit(1)
    except ConnectionRefusedError:
        sys.exit(1)
    except OSError:
        pass
    finally:
        s.close()
' "$gateway" || fail "the private network ($gateway) is reachable: pf block missing" ;;
  *) ;;   # no private gateway on this network
esac
echo "runner isolation: dedicated account $(id -un) (Owner's home closed, runner folder read-only)"
