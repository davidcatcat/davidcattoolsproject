#!/bin/bash
# Verify the runner's ownership and permission layout without changing it.
set -euo pipefail
fail() { printf 'selftest: %s\n' "$1" >&2; exit 1; }
account=ghrunner
account_owner=
root_owner=root
account_group=
root_group=root
tree=
test_overrides=0
while (($#)); do
  case "$1" in
    --account) (($# >= 2)) || fail "--account needs a value"; account=$2; shift 2 ;;
    --account-owner) (($# >= 2)) || fail "--account-owner needs a value"; account_owner=$2; test_overrides=1; shift 2 ;;
    --root-owner) (($# >= 2)) || fail "--root-owner needs a value"; root_owner=$2; test_overrides=1; shift 2 ;;
    --account-group) (($# >= 2)) || fail "--account-group needs a value"; account_group=$2; test_overrides=1; shift 2 ;;
    --root-group) (($# >= 2)) || fail "--root-group needs a value"; root_group=$2; test_overrides=1; shift 2 ;;
    --root) (($# >= 2)) || fail "--root needs a value"; tree=$2; shift 2 ;;
    -*) fail "unknown option $1" ;;
    *) fail "unexpected argument $1" ;;
  esac
done
[[ "$account" =~ ^(ghrunner|ghrunner[2-9])$ ]] || fail "invalid runner account"
[[ -n "$account_owner" ]] || account_owner=$account
[[ -n "$account_group" ]] || account_group=$account
if [[ -z "$tree" ]]; then
  (( ! test_overrides )) || fail "identity overrides require an offline fixture root"
  tree=/home/$account/actions-runner
  [[ "$(id -u)" == 0 ]] || fail "run with sudo for the installed runner"
else
  [[ "$tree" == /* ]] || fail "test root must be absolute"
  [[ "$tree" != /home/* ]] || fail "--root is only for an offline fixture"
fi
[[ -d "$tree" && ! -L "$tree" ]] || fail "missing or symlink runner directory: $tree"
while IFS= read -r -d '' path; do fail "refusing symlink: $path"; done < <(find -P "$tree" -type l -print0)
while IFS= read -r -d '' path; do
  [[ -d "$path" || -f "$path" ]] || fail "unexpected special file: $path"
done < <(find -P "$tree" ! -type d ! -type f -print0)
for directory in _work _tmp _diag; do
  [[ -d "$tree/$directory" && ! -L "$tree/$directory" ]] || fail "missing runner directory $directory"
done
for file in .runner .credentials .env .path; do
  [[ -f "$tree/$file" && ! -L "$tree/$file" ]] || fail "missing required file $file"
done

check() {
  local path=$1 expected_owner=$2 expected_mode=$3 owner mode
  owner=$(stat -c '%U:%G' -- "$path") || fail "cannot stat $path"
  mode=$(stat -c '%a' -- "$path") || fail "cannot stat $path"
  [[ "$owner" == "$expected_owner" ]] || fail "$path owner is $owner; expected $expected_owner"
  [[ "$mode" == "$expected_mode" ]] || fail "$path mode is $mode; expected $expected_mode"
}
while IFS= read -r -d '' path; do
  rel=${path#"$tree"/}
  if [[ "$rel" == "$tree" ]]; then rel=.; fi
  # Only the three writable directory roots have fixed metadata. Their
  # contents are runner data and can change while a job is running.
  if [[ "$rel" == _work/* || "$rel" == _tmp/* || "$rel" == _diag/* ]]; then
    continue
  fi
  if [[ "$rel" == _work || "$rel" == _work/* || "$rel" == _tmp || "$rel" == _tmp/* ]]; then
    expected_owner=$account_owner:$account_group
    if [[ -d "$path" ]]; then expected_mode=700; else expected_mode=600; fi
  elif [[ "$rel" == _diag || "$rel" == _diag/* ]]; then
    expected_owner=$account_owner:$account_group
    if [[ -d "$path" ]]; then expected_mode=750; else expected_mode=640; fi
  elif [[ "$rel" == .runner || "$rel" == .credentials || "$rel" == .credentials_rsaparams ]]; then
    expected_owner=$account_owner:$account_group; expected_mode=600
  else
    expected_owner=$root_owner:$root_group
    if [[ "$rel" == .env || "$rel" == .path ]]; then
      expected_mode=644
    elif [[ -d "$path" ]]; then
      expected_mode=755
    elif [[ -x "$path" ]]; then
      expected_mode=755
    else
      expected_mode=644
    fi
  fi
  check "$path" "$expected_owner" "$expected_mode"
done < <(find -P "$tree" -print0 | sort -z)
printf 'EC2 runner ownership and mode selftest passed: %s\n' "$tree"
