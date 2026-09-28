#!/usr/bin/env bash
# Runtime smoke test for a built nlf binary.
#
# Usage: bash scripts/ci-smoke.sh <binary> <version>
#
# Builds a small fixture tree in a fresh temporary directory, runs the binary
# against it, and checks exit codes, exact output path sets, --explain,
# --confirm, -0 / --null with hostile file names piped into xargs -0, and
# that nothing in either fixture was modified. Portable across
# GNU/Linux and macOS (bash 3.2+). Temporary directories are left in place
# and printed at the end; nothing is deleted.
set -euo pipefail

usage() {
	printf 'usage: %s <binary> <version>\n' "$0" >&2
	exit 1
}

[ "$#" -eq 2 ] || usage
bin_arg=$1
version=$2

if [ -z "$version" ]; then
	printf 'ci-smoke: version is empty\n' >&2
	exit 1
fi
if [ ! -f "$bin_arg" ]; then
	printf 'ci-smoke: binary not found: %s\n' "$bin_arg" >&2
	exit 1
fi
if [ ! -x "$bin_arg" ]; then
	printf 'ci-smoke: binary is not executable: %s\n' "$bin_arg" >&2
	exit 1
fi
bin_dir=$(cd "$(dirname "$bin_arg")" && pwd -P)
bin="$bin_dir/$(basename "$bin_arg")"

case "$(uname -s)" in
Darwin) platform=macOS ;;
Linux) platform=GNU/Linux ;;
*) platform="unsupported OS $(uname -s)" ;;
esac

# --- fixture -----------------------------------------------------------------

base=$(mktemp -d "${TMPDIR:-/tmp}/nlf-smoke.XXXXXX")
fx=$(cd "$base" && pwd -P)
work=$(mktemp -d "${TMPDIR:-/tmp}/nlf-smoke-work.XXXXXX")
work=$(cd "$work" && pwd -P)
errf="$work/stderr"
inf="$work/stdin"

mkdir -p "$fx/vendor" "$fx/docs" "$fx/中文 目錄"
printf 'package main\n\n// TODO: finish\nfunc main() {}\n' >"$fx/main.go"
printf 'package main\n\nfunc util() {}\n' >"$fx/util.go"
printf 'package lib\n\n// TODO: vendored\n' >"$fx/vendor/lib.go"
printf '# readme\n' >"$fx/docs/readme.md"
printf '說明\n' >"$fx/中文 目錄/說明.md"
printf 'x%.0s' $(seq 1 3000) >"$fx/big.log"
printf 'package old\n' >"$fx/old.go"
touch -t 202001011200 "$fx/old.go"

snapshot() {
	(cd "$fx" && find . -type f -exec cksum {} + | LC_ALL=C sort)
}
before=$(snapshot)
sleep 1
marker="$work/marker"
printf 'marker\n' >"$marker"

# Runs from inside the fixture so a query without --dir cannot reach anything else.
cd "$fx"

# --- helpers -----------------------------------------------------------------

nfail=0
failures=""

pass() {
	printf 'PASS  %s\n' "$1"
}

fail() {
	printf 'FAIL  %s: %s\n' "$1" "$2"
	nfail=$((nfail + 1))
	failures="${failures}  - $1: $2
"
}

# run <stdin> <args...>: sets code, out, err.
run() {
	printf '%s' "$1" >"$inf"
	shift
	code=0
	out=$("$bin" "$@" <"$inf" 2>"$errf") || code=$?
	err=$(cat "$errf")
}

sorted() {
	printf '%s\n' "$1" | LC_ALL=C sort
}

contains() {
	case "$1" in
	*"$2"*) return 0 ;;
	*) return 1 ;;
	esac
}

# check_found <name> <expected newline-separated paths> <args...>
check_found() {
	local name=$1 want=$2
	shift 2
	run "" "$@"
	if [ "$code" -ne 0 ]; then
		fail "$name" "exit $code, want 0 (stderr: $err)"
	elif [ "$(sorted "$out")" != "$(sorted "$want")" ]; then
		fail "$name" "got [$(sorted "$out" | tr '\n' ' ')] want [$(sorted "$want" | tr '\n' ' ')]"
	else
		pass "$name"
	fi
}

# check_exit_empty <name> <want code> <args...>
check_exit_empty() {
	local name=$1 want=$2
	shift 2
	run "" "$@"
	if [ "$code" -ne "$want" ]; then
		fail "$name" "exit $code, want $want (stderr: $err)"
	elif [ -n "$out" ]; then
		fail "$name" "stdout not empty: $out"
	else
		pass "$name"
	fi
}

# --- assertions --------------------------------------------------------------

run "" --version
if [ "$code" -eq 0 ] && [ "$out" = "nlf $version" ]; then
	pass "--version"
else
	fail "--version" "exit $code, stdout [$out], want [nlf $version]"
fi

run "" --help
if [ "$code" -eq 0 ] && contains "$out" "Usage:"; then
	pass "--help"
else
	fail "--help" "exit $code, stdout lacks Usage:"
fi

nl='
'
check_found "go files excluding vendor" \
	"$fx/main.go$nl$fx/util.go$nl$fx/old.go" \
	--dir "$fx" "go files excluding vendor"
check_found "找 go 檔" \
	"$fx/main.go$nl$fx/util.go$nl$fx/old.go$nl$fx/vendor/lib.go" \
	--dir "$fx" "找 go 檔"
check_found 'files containing "TODO"' \
	"$fx/main.go$nl$fx/vendor/lib.go" \
	--dir "$fx" 'files containing "TODO"'
check_found "larger than 2KB" \
	"$fx/big.log" \
	--dir "$fx" "larger than 2KB"
check_found "go files modified in the last 7 days" \
	"$fx/main.go$nl$fx/util.go$nl$fx/vendor/lib.go" \
	--dir "$fx" "go files modified in the last 7 days"
check_found "UTF-8 --dir with a space: md files" \
	"$fx/中文 目錄/說明.md" \
	--dir "$fx/中文 目錄" "md files"

check_exit_empty "pem files (no match)" 1 --dir "$fx" "pem files"

check_exit_empty "files without TODO (invalid)" 2 --dir "$fx" "files without TODO"
check_exit_empty "--bogus go files (invalid flag)" 2 --bogus go files
check_exit_empty "--dir missing (invalid)" 2 --dir "$fx/does-not-exist" "go files"
check_exit_empty 'log files !containing "TODO" (invalid)' 2 --dir "$fx" 'log files !containing "TODO"'

check_found "--max-depth 1 go files" \
	"$fx/main.go$nl$fx/util.go$nl$fx/old.go" \
	--max-depth 1 --dir "$fx" "go files"
check_found "--min-depth=2 go files" \
	"$fx/vendor/lib.go" \
	--min-depth=2 --dir "$fx" "go files"
check_found "--min-depth 2 md files excluding vendor" \
	"$fx/docs/readme.md$nl$fx/中文 目錄/說明.md" \
	--min-depth 2 --dir "$fx" "md files excluding vendor"
check_exit_empty "--max-depth 0 on a directory (no match)" 1 --max-depth 0 --dir "$fx" "go files"
check_exit_empty "--max-depth -1 (invalid)" 2 --max-depth -1 --dir "$fx" "go files"
check_exit_empty "--min-depth 2 --max-depth 1 (invalid)" 2 --min-depth 2 --max-depth 1 --dir "$fx" "go files"
check_exit_empty "--help --max-depth 1 (conflict)" 2 --help --max-depth 1

# The --explain command for a minimum depth with excludes uses a -path test
# (not -mindepth) and finds the same files as nlf when run read-only.
run "" --min-depth 2 --dir "$fx" "md files excluding vendor"
depth_nlf=$out
depth_nlf_code=$code
run "" --explain --min-depth 2 --dir "$fx" "md files excluding vendor"
depth_cmd=$(printf '%s\n' "$out" | tail -n 1 | sed 's/^ *//')
if [ "$depth_nlf_code" -ne 0 ]; then
	fail "--explain --min-depth 2 with excludes" "nlf search exit $depth_nlf_code, want 0"
elif [ "$code" -ne 0 ]; then
	fail "--explain --min-depth 2 with excludes" "exit $code, want 0 (stderr: $err)"
elif ! contains "$depth_cmd" " -path " || ! contains "$depth_cmd" "/*/*'" || contains "$depth_cmd" "-mindepth"; then
	fail "--explain --min-depth 2 with excludes" "command lacks -path or uses -mindepth: $depth_cmd"
else
	fcode=0
	depth_find=$(sh -c "$depth_cmd" 2>"$errf") || fcode=$?
	if [ "$fcode" -ne 0 ] || [ -s "$errf" ]; then
		fail "--explain --min-depth 2 with excludes" "find exit $fcode (stderr: $(cat "$errf"))"
	elif [ -z "$depth_nlf" ] || [ "$(sorted "$depth_find")" != "$(sorted "$depth_nlf")" ]; then
		fail "--explain --min-depth 2 with excludes" "find [$(sorted "$depth_find" | tr '\n' ' ')] nlf [$(sorted "$depth_nlf" | tr '\n' ' ')]"
	else
		pass "--explain --min-depth 2 with excludes matches nlf"
	fi
fi

run "" --explain --dir "$fx" "go files"
if [ "$code" -ne 0 ]; then
	fail "--explain" "exit $code, want 0 (stderr: $err)"
elif ! contains "$out" "find "; then
	fail "--explain" "stdout lacks 'find '"
elif ! contains "$out" "$platform"; then
	fail "--explain" "stdout lacks platform name '$platform'"
else
	pass "--explain ($platform)"
fi

run "" --dir "$fx" "go files"
direct=$out

run "n$nl" --confirm --dir "$fx" "go files"
if [ "$code" -eq 0 ] && [ -z "$out" ] && contains "$err" "Cancelled."; then
	pass "--confirm answered n"
else
	fail "--confirm answered n" "exit $code, stdout [$out], stderr lacks Cancelled.?"
fi

run "y$nl" --confirm --dir "$fx" "go files"
if [ "$code" -ne 0 ]; then
	fail "--confirm answered y" "exit $code, want 0 (stderr: $err)"
elif [ -z "$out" ] || [ "$out" != "$direct" ]; then
	fail "--confirm answered y" "stdout differs from the direct run"
elif ! contains "$err" "READ-ONLY"; then
	fail "--confirm answered y" "stderr lacks READ-ONLY"
else
	pass "--confirm answered y"
fi

run "" --confirm --dir "$fx" "go files"
if [ "$code" -eq 0 ] && [ -z "$out" ]; then
	pass "--confirm with EOF"
else
	fail "--confirm with EOF" "exit $code, stdout [$out]"
fi

after=$(snapshot)
if [ "$before" = "$after" ]; then
	pass "fixture checksums unchanged"
else
	fail "fixture checksums unchanged" "checksum snapshot differs"
fi

newer=$(find "$fx" -newer "$marker")
if [ -z "$newer" ]; then
	pass "nothing in fixture modified after marker"
else
	fail "nothing in fixture modified after marker" "$(printf '%s' "$newer" | tr '\n' ' ')"
fi

# --- -0 / --null with hostile file names -----------------------------------
#
# A separate directory of names that break newline-separated output or look
# like options. Each file holds one byte, "x". NUL-separated output is only
# ever written to files, never captured with $(...), which drops NUL bytes.

hx=$(mktemp -d "${TMPDIR:-/tmp}/nlf-smoke-hostile.XXXXXX")
hx=$(cd "$hx" && pwd -P)
tab=$(printf '\t')
for name in 'a b.txt' "a${tab}b.txt" "a${nl}b.txt" '-rf.txt' '--help.txt' '-n' ' lead.txt' 'trail.txt ' \
	"it's \"q\".txt" 'back\slash.txt' '中文 檔名.txt'; do
	printf 'x' >"$hx/$name"
done
hx_snapshot() {
	(cd "$hx" && find . -type f -exec cksum {} + | LC_ALL=C sort)
}
hx_before=$(hx_snapshot)

# nlf's default order: names sorted byte-wise, as filepath.WalkDir reads them.
want0="$work/hx-want"
printf '%s\0' ' lead.txt' './--help.txt' './-n' './-rf.txt' "a${tab}b.txt" "a${nl}b.txt" 'a b.txt' \
	'back\slash.txt' "it's \"q\".txt" 'trail.txt ' '中文 檔名.txt' >"$want0"

# run_file <stdin> <stdout file> <args...>: runs nlf inside $hx; sets code, err.
run_file() {
	printf '%s' "$1" >"$inf"
	local outf=$2
	shift 2
	code=0
	(cd "$hx" && "$bin" "$@" <"$inf" >"$outf" 2>"$errf") || code=$?
	err=$(cat "$errf")
}

out0="$work/hx-0.out"
run_file "" "$out0" -0 files
if [ "$code" -ne 0 ]; then
	fail "-0 files (hostile names)" "exit $code, want 0 (stderr: $err)"
elif ! cmp -s "$out0" "$want0"; then
	fail "-0 files (hostile names)" "output differs from the expected NUL-separated list"
else
	pass "-0 files (hostile names)"
fi

outnull="$work/hx-null.out"
run_file "" "$outnull" --null files
if [ "$code" -eq 0 ] && cmp -s "$outnull" "$out0"; then
	pass "--null is identical to -0"
else
	fail "--null is identical to -0" "exit $code or bytes differ"
fi

last=$(tail -c 1 "$out0" | od -An -tx1 | tr -d ' \n')
if [ "$last" = "00" ]; then
	pass "-0 output ends with a NUL byte"
else
	fail "-0 output ends with a NUL byte" "last byte is [$last]"
fi

newlines=$(tr -cd '\n' <"$out0" | wc -c | tr -d ' ')
if [ "$newlines" = "1" ]; then
	pass "-0 output has only the newline from a file name"
else
	fail "-0 output has only the newline from a file name" "$newlines newline bytes, want 1"
fi

if [ -s "$out0" ]; then
	xcode=0
	(cd "$hx" && xargs -0 printf '%s\0' <"$out0" >"$work/hx-printf.out" 2>"$errf") || xcode=$?
	if [ "$xcode" -eq 0 ] && cmp -s "$work/hx-printf.out" "$want0"; then
		pass "xargs -0 printf round-trips every path"
	else
		fail "xargs -0 printf round-trips every path" "exit $xcode or bytes differ"
	fi

	xcode=0
	(cd "$hx" && xargs -0 cat <"$out0" >"$work/hx-cat.out" 2>"$errf") || xcode=$?
	catout=$(cat "$work/hx-cat.out")
	if [ "$xcode" -eq 0 ] && [ "$catout" = "xxxxxxxxxxx" ]; then
		pass "xargs -0 cat reads every file (no name taken as an option)"
	else
		fail "xargs -0 cat reads every file (no name taken as an option)" "exit $xcode, output [$catout] (stderr: $(cat "$errf"))"
	fi
else
	fail "xargs -0 checks" "-0 output is empty; not piping empty input into xargs"
fi

outnl="$work/hx-nl.out"
run_file "" "$outnl" files
if [ "$code" -eq 0 ] && grep -Fqx -- './-rf.txt' "$outnl" && ! grep -Fqx -- '-rf.txt' "$outnl"; then
	pass "newline output prints ./-rf.txt"
else
	fail "newline output prints ./-rf.txt" "exit $code or line missing"
fi

# check_file_empty <name> <want code> <args...>: runs in $hx, stdout must be empty.
check_file_empty() {
	local name=$1 want=$2
	shift 2
	run_file "" "$work/hx-empty.out" "$@"
	if [ "$code" -ne "$want" ]; then
		fail "$name" "exit $code, want $want (stderr: $err)"
	elif [ -s "$work/hx-empty.out" ]; then
		fail "$name" "stdout not empty"
	else
		pass "$name"
	fi
}
check_file_empty "-0 -l (conflict)" 2 -0 -l files
check_file_empty "-0 --explain (conflict)" 2 -0 --explain files
check_file_empty "-0 --help (conflict)" 2 -0 --help
check_file_empty "-0 --version (conflict)" 2 -0 --version
check_file_empty "-0 pem files (no match)" 1 -0 pem files

run_file "n$nl" "$work/hx-confirm-n.out" -0 --confirm files
if [ "$code" -eq 0 ] && [ ! -s "$work/hx-confirm-n.out" ] && contains "$err" "Cancelled."; then
	pass "-0 --confirm answered n"
else
	fail "-0 --confirm answered n" "exit $code, stdout not empty or stderr lacks Cancelled."
fi

run_file "y$nl" "$work/hx-confirm-y.out" -0 --confirm files
if [ "$code" -eq 0 ] && cmp -s "$work/hx-confirm-y.out" "$out0"; then
	pass "-0 --confirm answered y"
else
	fail "-0 --confirm answered y" "exit $code or stdout differs from the direct -0 run"
fi

if [ "$hx_before" = "$(hx_snapshot)" ]; then
	pass "hostile-name fixture checksums unchanged"
else
	fail "hostile-name fixture checksums unchanged" "checksum snapshot differs"
fi

# --- summary -----------------------------------------------------------------

printf 'fixture: %s\n' "$fx"
printf 'work:    %s\n' "$work"
printf 'hostile: %s\n' "$hx"
if [ "$nfail" -ne 0 ]; then
	printf '\n%d check(s) failed for %s:\n%s' "$nfail" "$bin" "$failures"
	exit 1
fi
printf 'all checks passed for %s\n' "$bin"
