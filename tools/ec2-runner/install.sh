#!/bin/bash
# Make one already-configured EC2 Actions runner immutable to its job account.
set -euo pipefail

fail() { printf 'install: %s\n' "$1" >&2; exit 1; }
account=ghrunner
dry_run=0
tree=
while (($#)); do
  case "$1" in
    --account) (($# >= 2)) || fail "--account needs a value"; account=$2; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --root) (($# >= 2)) || fail "--root needs a value"; tree=$2; shift 2 ;;
    -*) fail "unknown option $1" ;;
    *) fail "unexpected argument $1" ;;
  esac
done
[[ "$account" =~ ^(ghrunner|ghrunner[2-9])$ ]] || fail "account must be ghrunner or ghrunner2..9"
fixture=0
if [[ -n "$tree" ]]; then
  (( dry_run )) || fail "--root is available only with --dry-run for offline fixtures"
  [[ "$tree" == /* && "$tree" != /home/* ]] || fail "--root must be an absolute offline fixture outside /home"
  fixture=1
else
  home=/home/$account
  tree=$home/actions-runner
fi

tree_real=
is_allowed_symlink() {
  local path=$1 rel=$2 resolved version kind ext_real base
  if [[ "$rel" =~ ^(bin|externals)$ ]]; then
    kind=${BASH_REMATCH[1]}
    resolved=$(realpath -e -- "$path") || return 1
    [[ -d "$resolved" && "$(dirname -- "$resolved")" == "$tree_real" ]] || return 1
    base=${resolved##*/}
    [[ "$base" =~ ^$kind\.([0-9]+(\.[0-9]+){2})$ ]]
    return
  fi
  if [[ "$rel" =~ ^externals\.([0-9]+(\.[0-9]+){2})/node[^/]*/.+$ ]]; then
    resolved=$(realpath -e -- "$path") || return 1
    ext_real="$tree_real/externals.${BASH_REMATCH[1]}"
    [[ -d "$ext_real" && "$resolved" == "$ext_real"/* ]]
    return
  fi
  return 1
}
check_link() {
  local path=$1 rel=${1#"$tree"/}
  [[ ! -L "$path" ]] || is_allowed_symlink "$path" "$rel" || fail "refusing symlink: $path"
}
check_no_links() {
  local path=$1
  [[ ! -L "$path" ]] || fail "refusing symlink path: $path"
}
check_tree() {
  local path
  [[ -d "$tree" ]] || fail "expected existing runner directory $tree"
  [[ ! -L "$tree" ]] || fail "refusing symlink path: $tree"
  tree_real=$(realpath -e -- "$tree") || fail "cannot resolve runner directory $tree"
  while IFS= read -r -d '' path; do check_link "$path"; done < <(find -P "$tree" -path "$tree/_work/_update" -prune -o -type l -print0)
  while IFS= read -r -d '' path; do
    [[ -d "$path" || -f "$path" ]] || fail "refusing special file: $path"
  done < <(find -P "$tree" -path "$tree/_work/_update" -prune -o ! -type d ! -type f ! -type l -print0)
  for directory in _work _diag; do
    [[ -d "$tree/$directory" && ! -L "$tree/$directory" ]] ||
      fail "missing runner directory $tree/$directory"
  done
  if [[ -e "$tree/_tmp" || -L "$tree/_tmp" ]]; then
    [[ -d "$tree/_tmp" && ! -L "$tree/_tmp" ]] ||
      fail "refusing non-directory runner path $tree/_tmp"
  fi
  for file in .runner .credentials .env .path; do
    [[ -f "$tree/$file" && ! -L "$tree/$file" ]] || fail "missing runner file $tree/$file"
  done
}

if (( ! fixture )); then
  check_no_links /home
  check_no_links "/home/$account"
  check_no_links "$tree"
  [[ "$(getent passwd "$account" | cut -d: -f6)" == "/home/$account" ]] ||
    fail "$account must have home directory /home/$account"
  getent group "$account" >/dev/null || fail "$account group does not exist"
  [[ "$(id -u)" == 0 ]] || fail "run with sudo"
  number=${account#ghrunner}
  suffix=${number:+-$number}
  service=actions.runner.davidcatcat-davidcattoolsproject.ec2-self-runner${suffix}.service
  if systemctl is-active --quiet "$service"; then
    fail "runner service $service is active; stop it before installing"
  else
    status=$?
    (( status == 3 )) || fail "could not verify runner service $service is inactive"
  fi
  if pgrep -u "$account" >/dev/null 2>&1; then
    fail "processes are still running as $account; stop them before installing"
  else
    status=$?
    (( status == 1 )) || fail "could not check processes for $account"
  fi
fi
check_tree

declare -a plan_owner=() plan_mode=() plan_path=()
while IFS= read -r -d '' path; do
  rel=${path#"$tree"/}
  if [[ "$rel" == _work/* || "$rel" == _tmp/* || "$rel" == _diag/* ]]; then
    continue
  fi
  if [[ "$rel" == _work || "$rel" == _tmp ]]; then
    owner=$account:$account
    if [[ -d "$path" ]]; then mode=0700; else mode=0600; fi
  elif [[ "$rel" == _diag ]]; then
    owner=$account:$account; mode=0750
  elif [[ "$rel" == .runner || "$rel" == .credentials || "$rel" == .credentials_rsaparams ]]; then
    owner=$account:$account; mode=0600
  else
    owner=root:root
    if [[ "$rel" == .env || "$rel" == .path ]]; then
      mode=0644
    elif [[ -d "$path" || -x "$path" ]]; then
      mode=0755
    else
      mode=0644
    fi
  fi
  plan_owner+=("$owner")
  plan_mode+=("$mode")
  plan_path+=("$path")
done < <(find -P "$tree" -path "$tree/_work/_update" -prune -o -print0 | sort -z)

tmp_path=$tree/_tmp
create_tmp=0
if [[ ! -e "$tmp_path" && ! -L "$tmp_path" ]]; then
  create_tmp=1
fi

for index in "${!plan_path[@]}"; do
  owner=${plan_owner[$index]}; mode=${plan_mode[$index]}; path=${plan_path[$index]}
  if [[ -L "$path" ]]; then continue; fi
  printf 'chown -h %s -- %q\nchmod %s -- %q\n' "$owner" "$path" "$mode" "$path"
done
if (( create_tmp )); then
  printf 'mkdir -m 0700 -- %q\nchown -h %s -- %q\nchmod 0700 -- %q\n' \
    "$tmp_path" "$account:$account" "$tmp_path" "$tmp_path"
fi
(( dry_run )) && exit 0

for index in "${!plan_path[@]}"; do
  owner=${plan_owner[$index]}; mode=${plan_mode[$index]}; path=${plan_path[$index]}
  if [[ -L "$path" ]]; then continue; fi
  chown -h "$owner" -- "$path"
  if [[ -L "$path" ]]; then fail "refusing symlink path: $path"; fi
  chmod "$mode" -- "$path"
done
if (( create_tmp )); then
  mkdir -m 0700 -- "$tmp_path" || fail "could not create runner directory $tmp_path"
  chown -h "$account:$account" -- "$tmp_path"
  [[ -d "$tmp_path" && ! -L "$tmp_path" ]] || fail "refusing symlink path: $tmp_path"
  chmod 0700 -- "$tmp_path"
fi
printf 'installed root-owned permissions for %s\n' "$tree"
