#!/bin/bash
# Runs an Actions runner under a dedicated, hidden, non-admin account as a
# system service, one account per runner.
#
#   sudo bash tools/mac-runner/install.sh [owner-runner-folder]
#       runner 1: moves the Owner's configured runner to ghrunner
#   sudo bash tools/mac-runner/install.sh --account ghrunner2 \
#       --runner davidproject-self-action-2 --register [folder-with-tarball]
#       a further runner: a new registration from the verified runner tarball
#   sudo bash tools/mac-runner/install.sh [--account A --runner R] --uninstall
#
# Run it from a checkout of the reviewed revision. It
#   - creates the account (own group, not staff/admin, no login, no password),
#   - closes the Owner's home to other accounts (chmod o-rwx: macOS default),
#   - copies the runner (registration included), or registers a new one, in
#     /Users/<account>/actions-runner, owned by root except its work, log and
#     temp folders and its credential,
#   - installs probe.sh and job-started.sh as the runner's job-started hook,
#   - blocks ghrunner's TCP/UDP to private networks (VPN/company/LAN) with a
#     pf anchor that matches only that account,
#   - replaces the Owner's runner LaunchAgent with a LaunchDaemon for ghrunner.
# Nothing else on the Mac is changed (no VPN, route, DNS or other service).
# Re-running it updates the install and keeps an existing registration.
# --uninstall stops the service and removes the pf rules; it keeps the
# account, folders and registration (the runner then shows offline).
set -euo pipefail
fail() { echo "install: $1" >&2; exit 1; }
test "$(id -u)" = 0 || fail "run with sudo"
repo=davidcatcat/davidcattoolsproject
tarball_name=actions-runner-osx-x64-2.338.0.tar.gz
tarball_sha256=dea7a58796ce215fc424a8b27c0fdd9b813fa5fc03d109a4a1d38131d6bfa1a3   # GitHub release
account=ghrunner runner=davidproject-self-action mode=copy uninstall="" src_arg=""
while test $# -gt 0; do
  case "$1" in
    --account) account=${2:?}; shift 2 ;;
    --runner) runner=${2:?}; shift 2 ;;
    --register) mode=register; shift ;;
    --uninstall) uninstall=1; shift ;;
    -*) fail "unknown option $1" ;;
    *) src_arg=$1; shift ;;
  esac
done
# One fixed pairing: ghrunner runs davidproject-self-action (copied from the
# Owner's runner); ghrunnerN runs davidproject-self-action-N (registered new).
[[ "$account" =~ ^ghrunner([2-9])?$ ]] || fail "account must be ghrunner or ghrunner2..9"
number=${account#ghrunner}
test "$runner" = "davidproject-self-action${number:+-$number}" ||
  fail "$account runs davidproject-self-action${number:+-$number}, not $runner"
if test -z "$uninstall"; then
  if test -n "$number" && test "$mode" != register; then fail "$account needs --register"; fi
  if test -z "$number" && test "$mode" = register; then fail "ghrunner is copied, not registered"; fi
fi
label=actions.runner.davidcatcat-davidcattoolsproject.$runner
daemon=/Library/LaunchDaemons/$label.plist
pf_label=local.$account.pf
pf_daemon=/Library/LaunchDaemons/$pf_label.plist
anchor=com.apple/$account
rules=/etc/pf.anchors/$account
home=/Users/$account
dst=$home/actions-runner

if test -n "$uninstall"; then
  launchctl bootout system "$daemon" 2>/dev/null || true
  launchctl bootout system "$pf_daemon" 2>/dev/null || true
  rm -f "$daemon" "$pf_daemon" "$rules"
  pfctl -a "$anchor" -F all 2>/dev/null || true
  echo "uninstalled the $account service and pf rules (pf itself stays on with macOS's"
  echo "default rules until reboot); the account, $dst and the registration are kept."
  exit 0
fi

owner=${SUDO_USER:?run with sudo from the Owner account}
owner_uid=$(id -u "$owner")
owner_home=$(dscl . -read "/Users/$owner" NFSHomeDirectory | awk '{print $2}')
src=$(cd "${src_arg:-$owner_home/git/actions-runner}" && pwd -P) || fail "no runner folder"
here=$(cd "$(dirname "$0")" && pwd -P)
if test "$mode" = copy; then
  test -f "$src/.runner" && test -f "$src/.credentials" || fail "$src is not a configured runner"
  test -f "$src/bin/runsvc.sh" || fail "$src/bin/runsvc.sh is missing"
else
  test "$(shasum -a 256 < "$src/$tarball_name" | awk '{print $1}')" = "$tarball_sha256" ||
    fail "$src/$tarball_name is missing or not the released runner"
fi
if pgrep -u "$owner" -f 'Runner.Listener' >/dev/null &&
    ! launchctl print "gui/$owner_uid/$label" >/dev/null 2>&1; then
  fail "a runner started by hand is running as $owner; stop it (Ctrl-C) first"
fi

# 1. The account: own group, hidden, no shell, no password, never admin.
if ! dscl . -read "/Users/$account" >/dev/null 2>&1; then
  id=450
  while dscl . -list /Users UniqueID | awk '{print $2}' | grep -qx "$id" ||
        dscl . -list /Groups PrimaryGroupID | awk '{print $2}' | grep -qx "$id"; do
    id=$((id + 1))
  done
  dscl . -create "/Groups/$account"
  dscl . -create "/Groups/$account" PrimaryGroupID "$id"
  dscl . -create "/Groups/$account" RealName "GitHub Actions runner"
  dscl . -create "/Users/$account"
  dscl . -create "/Users/$account" UniqueID "$id"
  dscl . -create "/Users/$account" PrimaryGroupID "$id"
  dscl . -create "/Users/$account" UserShell /usr/bin/false
  dscl . -create "/Users/$account" NFSHomeDirectory "$home"
  dscl . -create "/Users/$account" RealName "GitHub Actions runner"
  dscl . -create "/Users/$account" IsHidden 1
  dscl . -create "/Users/$account" Password '*'
fi
for group in admin staff; do
  dsmemberutil checkmembership -U "$account" -G "$group" | grep -q 'is not a member' ||
    fail "$account is (or may be) a member of $group"
done
# admin (the Owner) may enter, so the Owner and the monitor can read the
# runner's logs (_diag); _work, _tmp and the credential stay ghrunner-only.
install -d -m 0750 -o "$account" -g admin "$home"

# 2. The Owner's home: no access for other accounts (macOS default 750).
chmod o-rwx "$owner_home"
if sudo -u "$account" ls "$owner_home" >/dev/null 2>&1; then
  fail "$account can still read $owner_home"
fi

# 4. The runner: copied, or registered fresh; then root-owned, so jobs cannot
# change it or the hook. A registration is never shared between runners.
if test "$mode" = copy; then
  install -d -m 0755 -o root -g wheel "$dst"
  rsync -a --delete --exclude '/_work' --exclude '/_diag' --exclude '/_home' --exclude '/_tmp' \
    --exclude '/isolation' --exclude '/*.tar.gz' --exclude '/runsvc.sh' --exclude '/.env' \
    --exclude '/.path' --exclude '/.service' "$src/" "$dst/"
elif ! test -f "$dst/.runner"; then
  # Extracted and configured as the account (config.sh refuses root). The
  # one-hour registration token comes from the Owner's gh and reaches
  # config.sh only through the environment: no command line (any account can
  # list command lines) ever holds it. If GitHub accepted the name but the
  # run failed, remove the offline runner in GitHub before retrying.
  install -d -m 0700 -o "$account" -g "$account" "$dst"
  install -m 0600 -o "$account" -g "$account" "$src/$tarball_name" "$dst/$tarball_name"
  sudo -u "$account" /usr/bin/tar -xzf "$dst/$tarball_name" -C "$dst"
  rm -f "$dst/$tarball_name"
  ACTIONS_RUNNER_INPUT_TOKEN=$(sudo -u "$owner" gh api -X POST \
    "repos/$repo/actions/runners/registration-token" --jq .token) ||
    fail "the Owner's gh could not get a registration token"
  export ACTIONS_RUNNER_INPUT_TOKEN
  sudo --preserve-env=ACTIONS_RUNNER_INPUT_TOKEN -H -u "$account" /bin/bash -c \
    'cd "$1" && ./config.sh --unattended --url "https://github.com/$2" --name "$3" \
       --labels local-mac-ci --work _work --disableupdate' _ "$dst" "$repo" "$runner" ||
    fail "registration of $runner failed"
  # The pattern comes on stdin, not as an argument.
  if grep -rqF -f - "$dst/_diag" "$dst/.runner" 2>/dev/null <<< "$ACTIONS_RUNNER_INPUT_TOKEN"; then
    fail "the registration token was written to a log; remove $dst/_diag and check GitHub"
  fi
  unset ACTIONS_RUNNER_INPUT_TOKEN
fi
cp "$dst/bin/runsvc.sh" "$dst/runsvc.sh"   # the runner's own, unchanged template
# Root owns everything except ghrunner's own folders (kept on a re-run).
# rsync copied the Owner's modes (the source folder is 0700), so everything
# is made readable, and folders and programs usable, by ghrunner; only the
# credential files below stay private.
find "$dst" \( -path "$dst/_work" -o -path "$dst/_tmp" -o -path "$dst/_diag" -o -path "$dst/.runner" \
  -o -path "$dst/.credentials" -o -path "$dst/.credentials_rsaparams" \) -prune -o \
  -exec chown root:wheel {} + -exec chmod a+rX,go-w {} +
chmod 0755 "$dst/runsvc.sh"
for file in .runner .credentials .credentials_rsaparams; do
  if test -f "$dst/$file"; then chown "$account:$account" "$dst/$file"; chmod 0600 "$dst/$file"; fi
done
install -d -m 0700 -o "$account" -g "$account" "$dst/_work" "$dst/_tmp"
install -d -m 0750 -o "$account" -g admin "$dst/_diag"   # the Owner (admin) reads logs
chown -R "$account:$account" "$dst/_work" "$dst/_tmp"
chown -R "$account:admin" "$dst/_diag"
install -d -m 0755 -o root -g wheel "$dst/isolation"
install -m 0555 -o root -g wheel "$here/probe.sh" "$here/job-started.sh" "$dst/isolation/"
lang=$(sed -n 's/^LANG=//p' "$owner_home/git/actions-runner/.env" 2>/dev/null | head -n 1)
cat > "$dst/.env" <<EOF
LANG=${lang:-en_US.UTF-8}
HOME=$home
TMPDIR=$dst/_tmp/
OWNER_HOME=$owner_home
ACTIONS_RUNNER_HOOK_JOB_STARTED=$dst/isolation/job-started.sh
EOF
echo /usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin > "$dst/.path"
chmod 0644 "$dst/.env" "$dst/.path"

# 5. Network: no TCP/UDP from ghrunner to private ranges (company VPN, LAN).
# The rules match only sockets of $account; other traffic is not touched.
cat > "$rules" <<EOF
# Issue #99: the CI runner account must not reach private networks.
block drop out quick inet proto { tcp udp } from any to { 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 } user $account
block drop out quick inet6 proto { tcp udp } from any to { fc00::/7 fe80::/10 } user $account
EOF
chmod 0644 "$rules"
cat > "$pf_daemon" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$pf_label</string>
  <key>ProgramArguments</key><array>
    <string>/sbin/pfctl</string><string>-E</string>
    <string>-a</string><string>$anchor</string><string>-f</string><string>$rules</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
chmod 0644 "$pf_daemon"
launchctl bootout system "$pf_daemon" 2>/dev/null || true
launchctl bootstrap system "$pf_daemon"
# pfctl lists the account by uid ("user = 450").
uid=$(id -u "$account")
for _ in $(seq 1 10); do pfctl -a "$anchor" -s rules 2>/dev/null | grep -q "user = $uid\$" && break; sleep 1; done
pfctl -a "$anchor" -s rules 2>/dev/null | grep -q "user = $uid\$" || fail "pf rules not loaded"
pfctl -s info 2>/dev/null | grep -q 'Status: Enabled' || fail "pf is not enabled"
# Proof on a private address the Owner can reach: the LAN gateway.
gateway=$(route -n get default 2>/dev/null | awk '/gateway:/ {print $2}')
proved=""
for port in 53 80 443; do
  test -n "$gateway" || break
  if sudo -u "$owner" /usr/bin/nc -z -G 2 "$gateway" "$port" 2>/dev/null; then
    if sudo -u "$account" /usr/bin/nc -z -G 2 "$gateway" "$port" 2>/dev/null; then
      fail "$account can still reach $gateway:$port"
    fi
    proved="$gateway:$port"; break
  fi
done
if test -n "$proved"; then
  echo "private network: blocked for $account (checked $proved)"
else
  echo "private network: rules loaded; no private port the Owner reaches was found to prove the block"
fi
sudo -u "$account" env HOME="$home" /usr/bin/curl -fsS -o /dev/null https://api.github.com ||
  fail "$account cannot reach GitHub"

# 6. The isolation check, as the account, before any job.
sudo -u "$account" env OWNER_HOME="$owner_home" "$dst/isolation/probe.sh"

# 7. Stop the previous listener of this registration (the Owner's LaunchAgent
# for runner 1, or this daemon on a re-run), then start this account's one.
# Everything above is checked first, so a failure leaves it running. Other
# runners' listeners are not touched.
launchctl bootout "gui/$owner_uid/$label" 2>/dev/null || true
rm -f "$owner_home/Library/LaunchAgents/$label.plist"
launchctl bootout system "$daemon" 2>/dev/null || true
mine() {
  { test "$mode" = copy && pgrep -u "$owner" -f 'Runner.Listener'; } >/dev/null ||
    pgrep -u "$account" -f 'Runner.Listener' >/dev/null
}
for _ in $(seq 1 30); do mine || break; sleep 1; done
if mine; then fail "the previous listener of $runner is still running (a job may be in progress)"; fi
cat > "$daemon" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$dst/runsvc.sh</string></array>
  <key>UserName</key><string>$account</string>
  <key>GroupName</key><string>$account</string>
  <key>WorkingDirectory</key><string>$dst</string>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$dst/_diag/service.log</string>
  <key>StandardErrorPath</key><string>$dst/_diag/service.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>ACTIONS_RUNNER_SVC</key><string>1</string>
    <key>HOME</key><string>$home</string>
  </dict>
</dict></plist>
EOF
chmod 0644 "$daemon"
launchctl bootstrap system "$daemon"
for _ in $(seq 1 30); do pgrep -u "$account" -f 'Runner.Listener' >/dev/null && break; sleep 1; done
pgrep -u "$account" -f 'Runner.Listener' >/dev/null || fail "the runner did not start; see $dst/_diag/service.log"
echo "$runner now runs as $account from $dst"
echo "check: run the Mac Runner Smoke Test workflow and look for 'runner isolation: dedicated account'"
