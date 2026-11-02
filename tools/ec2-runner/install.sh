#!/bin/bash
# Make one already-configured EC2 Actions runner immutable to its job account.
set -euo pipefail

fail() { printf 'install: %s\n' "$1" >&2; exit 1; }
account=ghrunner
dry_run=0
while (($#)); do
  case "$1" in
    --account) (($# >= 2)) || fail "--account needs a value"; account=$2; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -*) fail "unknown option $1" ;;
    *) fail "unexpected argument $1" ;;
  esac
done
[[ "$account" =~ ^ghrunner([1-9][0-9]*)?$ ]] || fail "account must be ghrunner or ghrunnerN"
number=${account#ghrunner}
(( ${#number} <= 1 )) || fail "runner number must be 1..9"
home=/home/$account
dst=$home/actions-runner

# Check every fixed path component before touching the tree. This prevents a
# substituted home or install directory from redirecting privileged writes.
for path in /home "$home" "$dst"; do
  [[ ! -L "$path" ]] || fail "refusing symlink path: $path"
done
[[ -d "$home" && -d "$dst" ]] || fail "expected existing runner directory $dst"
[[ "$(getent passwd "$account" | cut -d: -f6)" == "$home" ]] ||
  fail "$account must have home directory $home"
getent group "$account" >/dev/null || fail "$account group does not exist"
if (( ! dry_run )); then
  [[ "$(id -u)" == 0 ]] || fail "run with sudo"
fi

# Reject links and special files before producing or applying any changes.
while IFS= read -r -d '' path; do
  fail "refusing symlink: $path"
done < <(find -P "$dst" -type l -print0)
while IFS= read -r -d '' path; do
  [[ -d "$path" || -f "$path" ]] || fail "refusing special file: $path"
done < <(find -P "$dst" ! -type d ! -type f -print0)
for directory in _work _tmp _diag; do
  [[ -d "$dst/$directory" && ! -L "$dst/$directory" ]] ||
    fail "missing runner directory $dst/$directory"
done
for file in .runner .credentials .env .path; do
  [[ -f "$dst/$file" && ! -L "$dst/$file" ]] || fail "missing runner file $dst/$file"
done

declare -a plan_owner=() plan_mode=() plan_path=()
while IFS= read -r -d '' path; do
  rel=${path#"$dst"/}
  # These directories are writable by the runner account. Their contents are
  # runner data and need no privileged recursion; touching them would create
  # a symlink race with an active job.
  if [[ "$rel" == _work/* || "$rel" == _tmp/* || "$rel" == _diag/* ]]; then
    continue
  fi
  if [[ "$rel" == _work || "$rel" == _work/* || "$rel" == _tmp || "$rel" == _tmp/* ]]; then
    owner=$account:$account
    if [[ -d "$path" ]]; then mode=0700; else mode=0600; fi
  elif [[ "$rel" == _diag || "$rel" == _diag/* ]]; then
    owner=$account:$account
    if [[ -d "$path" ]]; then mode=0750; else mode=0640; fi
  elif [[ "$rel" == .runner || "$rel" == .credentials || "$rel" == .credentials_rsaparams ]]; then
    owner=$account:$account; mode=0600
  else
    owner=root:root
    if [[ "$rel" == .env || "$rel" == .path ]]; then
      mode=0644
    elif [[ -d "$path" ]]; then
      mode=0755
    elif [[ -x "$path" ]]; then
      mode=0755
    else
      mode=0644
    fi
  fi
  plan_owner+=("$owner")
  plan_mode+=("$mode")
  plan_path+=("$path")
done < <(find -P "$dst" -print0 | sort -z)

for index in "${!plan_path[@]}"; do
  owner=${plan_owner[$index]}; mode=${plan_mode[$index]}; path=${plan_path[$index]}
  printf 'chown %s -- %q\nchmod %s -- %q\n' "$owner" "$path" "$mode" "$path"
done
(( dry_run )) && exit 0

for index in "${!plan_path[@]}"; do
  owner=${plan_owner[$index]}; mode=${plan_mode[$index]}; path=${plan_path[$index]}
  chown "$owner" -- "$path"
  chmod "$mode" -- "$path"
done
printf 'installed root-owned permissions for %s\n' "$dst"
