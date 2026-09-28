package main

import (
	"bytes"
	"errors"
	"flag"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unicode"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/search"
)

var testNow = time.Date(2026, 9, 27, 12, 0, 0, 0, time.Local)

// fixture builds a small tree and returns its root.
func fixture(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	files := []struct {
		path string
		data string
		age  time.Duration
	}{
		{"main.go", "package main // TODO: tidy", 2 * time.Hour},
		{"util.go", "package main", 10 * 24 * time.Hour},
		{"docs/readme.md", "TODO write docs", 40 * 24 * time.Hour},
		{"vendor/lib.go", "// TODO vendored", time.Hour},
		{"big.log", strings.Repeat("x", 2<<20), 3 * 24 * time.Hour},
	}
	for _, f := range files {
		p := filepath.Join(dir, f.path)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(f.data), 0o644); err != nil {
			t.Fatal(err)
		}
		mt := testNow.Add(-f.age)
		if err := os.Chtimes(p, mt, mt); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// nlfInput runs nlf with the given text on stdin.
func nlfInput(t *testing.T, stdin string, args ...string) (stdout, stderr string, code int) {
	t.Helper()
	var out, errb bytes.Buffer
	code = run(args, strings.NewReader(stdin), &out, &errb, testNow)
	return out.String(), errb.String(), code
}

func nlf(t *testing.T, args ...string) (stdout, stderr string, code int) {
	t.Helper()
	var out, errb bytes.Buffer
	code = run(args, strings.NewReader(""), &out, &errb, testNow)
	return out.String(), errb.String(), code
}

func paths(dir, stdout string) []string {
	var got []string
	for _, l := range strings.Split(strings.TrimSpace(stdout), "\n") {
		if l != "" {
			rel, _ := filepath.Rel(dir, l)
			got = append(got, rel)
		}
	}
	return got
}

func TestEndToEnd(t *testing.T) {
	dir := fixture(t)
	tests := []struct {
		query string
		want  []string
	}{
		{"go files", []string{"main.go", "util.go", "vendor/lib.go"}},
		{"go files excluding vendor", []string{"main.go", "util.go"}},
		{"包含 TODO 的檔案，排除 vendor", []string{"docs/readme.md", "main.go"}},
		{"最近 7 天修改的 go 檔", []string{"main.go", "vendor/lib.go"}},
		{"older than 30 days", []string{"docs/readme.md"}},
		{"larger than 1MB", []string{"big.log"}},
		{"最大的 2 個檔案", []string{"big.log", "main.go"}},
		{"newest 1", []string{"vendor/lib.go"}},
		{`go files containing "todo" ignore case modified today, top 1`, []string{"main.go"}},
	}
	for _, tt := range tests {
		t.Run(tt.query, func(t *testing.T) {
			out, errOut, code := nlf(t, "--dir", dir, tt.query)
			if code != 0 {
				t.Fatalf("exit %d, stderr: %s", code, errOut)
			}
			got := paths(dir, out)
			if strings.Join(got, ",") != strings.Join(tt.want, ",") {
				t.Errorf("got %v, want %v", got, tt.want)
			}
		})
	}
}

func TestNoMatchesExitsOne(t *testing.T) {
	dir := fixture(t)
	out, errOut, code := nlf(t, "--dir", dir, "pdf files")
	if code != 1 || out != "" || !strings.Contains(errOut, "--explain") {
		t.Errorf("code=%d out=%q err=%q", code, out, errOut)
	}
}

func TestParseErrorExitCode(t *testing.T) {
	_, errOut, code := nlf(t, "大於 1MB 或 go 檔")
	if code != 2 || !strings.Contains(errOut, "目前 OR 只能連接同類條件") {
		t.Errorf("code=%d err=%q", code, errOut)
	}
	// "banana files" would now mean *.banana (any word + file noun is an
	// extension), so the unknown word is placed where it cannot be one.
	_, errOut, code = nlf(t, "find files banana")
	if code != 2 || !strings.Contains(errOut, `cannot understand: "banana"`) {
		t.Errorf("code=%d err=%q", code, errOut)
	}
}

func TestExplainDoesNotSearch(t *testing.T) {
	out, _, code := nlf(t, "--explain", "--dir", "/definitely/missing", "最近 3 天、大於 1MB 的 go 檔，排除 vendor，最多 5 個")
	if code != 0 {
		t.Fatalf("exit %d", code)
	}
	// after <= mtime is rendered as mtime > after-1ns: whole seconds for
	// BSD find, nanoseconds for GNU find.
	after := map[query.Platform]string{query.MacOS: "'2026-09-24 11:59:59'", query.Linux: "'2026-09-24 11:59:59.999999999'"}[query.PlatformFor(runtime.GOOS)]
	for _, want := range []string{"解析結果", ".go", "> 1 MB", "vendor", "5",
		"find /definitely/missing -type d \\( -iname vendor \\) -prune -o -type f -iname '?*.go' -size +1048576c -newermt " + after + " -print | head -n 5"} {
		if !strings.Contains(out, want) {
			t.Errorf("explain output missing %q:\n%s", want, out)
		}
	}
}

func TestLongOutput(t *testing.T) {
	dir := fixture(t)
	out, _, code := nlf(t, "-l", "--dir", dir, "larger than 1mb")
	if code != 0 || !strings.Contains(out, "2 MB  2026-09-24 12:00  "+filepath.Join(dir, "big.log")) {
		t.Errorf("code=%d out=%q", code, out)
	}
}

func TestPermissionDeniedMessage(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores permissions")
	}
	dir := fixture(t)
	locked := filepath.Join(dir, "docs")
	if err := os.Chmod(locked, 0); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(locked, 0o755) })

	out, errOut, code := nlf(t, "--dir", dir, "找 go 檔")
	if code != 0 || len(paths(dir, out)) != 3 {
		t.Errorf("code=%d out=%q", code, out)
	}
	for _, want := range []string{"有 1 個位置因權限不足而略過", "不會自動使用 sudo", locked, permissionHint(query.Chinese, runtime.GOOS)} {
		if !strings.Contains(errOut, want) {
			t.Errorf("stderr missing %q:\n%s", want, errOut)
		}
	}

	_, errOut, code = nlf(t, "--dir", locked, "go files")
	if code != 2 || !strings.Contains(errOut, "permission denied reading") {
		t.Errorf("unreadable root: code=%d err=%q", code, errOut)
	}
}

func TestArgs(t *testing.T) {
	if out, _, code := nlf(t, "--help"); code != 0 || !strings.Contains(out, "Usage:") {
		t.Errorf("--help: code=%d", code)
	}
	if out, _, code := nlf(t, "--version"); code != 0 || out != "nlf 0.4.0\n" {
		t.Errorf("--version: %q", out)
	}
	if _, errOut, code := nlf(t, "--bogus", "go files"); code != 2 || !strings.Contains(errOut, "unknown flag") {
		t.Errorf("unknown flag: code=%d", code)
	}
	if _, errOut, code := nlf(t); code != 2 || !strings.Contains(errOut, "describe the files") {
		t.Errorf("no query: code=%d", code)
	}
	if _, errOut, code := nlf(t, "--dir", "/tmp", "in ./src go files"); code != 2 || !strings.Contains(errOut, "--dir") {
		t.Errorf("dir conflict: code=%d err=%q", code, errOut)
	}
	// Unquoted words are joined into one query; flags may come last.
	dir := fixture(t)
	out, _, code := nlf(t, "go", "files", "excluding", "vendor", "--dir", dir)
	if code != 0 || len(paths(dir, out)) != 2 {
		t.Errorf("joined words: code=%d out=%q", code, out)
	}
}

// mkfiles creates empty (or given) files under dir.
func mkfiles(t *testing.T, dir string, files map[string]string) {
	t.Helper()
	for p, data := range files {
		p = filepath.Join(dir, p)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(data), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func TestChinesePathIsNeverCutShort(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	mkfiles(t, home, map[string]string{"文件/doc.go": "", "other/secret.go": "", "圖片/p.png": "", "other/o.png": "",
		"中文 目錄/a.md": "", "other/b.md": ""})

	tests := []struct {
		query string
		want  []string
	}{
		{"在 ~/文件 找 go 檔", []string{"文件/doc.go"}},
		{"在 ~/圖片", []string{"圖片/p.png"}}, // not "~" plus an image filter
		{`在 "~/中文 目錄" 找 md 檔`, []string{"中文 目錄/a.md"}},
	}
	for _, tt := range tests {
		out, errOut, code := nlf(t, tt.query)
		if got := paths(home, out); code != 0 || strings.Join(got, ",") != strings.Join(tt.want, ",") {
			t.Errorf("%s: code=%d got %v, want %v (stderr %q)", tt.query, code, got, tt.want, errOut)
		}
	}

	// A path glued to following words must fail, not fall back to a wider root.
	out, errOut, code := nlf(t, "在 ~/文件裡找 go 檔")
	if code != 2 || out != "" || !strings.Contains(errOut, "找不到搜尋位置") || !strings.Contains(errOut, "引號") {
		t.Errorf("glued path: code=%d out=%q err=%q", code, out, errOut)
	}
}

func TestPathWithSpaces(t *testing.T) {
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"my docs/a.txt": "", "b.txt": ""})
	out, _, code := nlf(t, `in "`+filepath.Join(dir, "my docs")+`" txt files`)
	if got := paths(dir, out); code != 0 || strings.Join(got, ",") != "my docs/a.txt" {
		t.Errorf("code=%d got %v", code, got)
	}
}

func TestExcludeWithExtensionEndToEnd(t *testing.T) {
	dir := fixture(t)
	for _, q := range []string{"exclude vendor and go files", "排除 vendor 和 go 檔"} {
		out, errOut, code := nlf(t, "--dir", dir, q)
		if got := paths(dir, out); code != 0 || strings.Join(got, ",") != "main.go,util.go" {
			t.Errorf("%s: code=%d got %v err=%q", q, code, got, errOut)
		}
	}
}

func TestExcludePathErrors(t *testing.T) {
	dir := fixture(t)
	for _, q := range []string{"exclude ../elsewhere", "排除 /definitely/elsewhere"} {
		if out, errOut, code := nlf(t, "--dir", dir, q); code != 2 || out != "" || !strings.Contains(errOut, "outside") && !strings.Contains(errOut, "之內") {
			t.Errorf("%s: code=%d out=%q err=%q", q, code, out, errOut)
		}
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	mkfiles(t, home, map[string]string{"proj/vendor/x.go": "", "proj/a.go": ""})
	out, errOut, code := nlf(t, "--dir", filepath.Join(home, "proj"), "go files exclude ~/proj/vendor")
	if got := paths(filepath.Join(home, "proj"), out); code != 0 || strings.Join(got, ",") != "a.go" {
		t.Errorf("~ exclude: code=%d got %v err=%q", code, got, errOut)
	}
}

func TestExplainSymlinkRootAndRelativeExclude(t *testing.T) {
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"real/a.txt": ""})
	link := filepath.Join(dir, "link")
	if err := os.Symlink(filepath.Join(dir, "real"), link); err != nil {
		t.Fatal(err)
	}
	out, _, code := nlf(t, "--explain", "--dir", link, "txt files exclude ./src//gen/ and vendor")
	want := "find " + link + "/ -type d \\( -path " + link + "/src/gen -o -iname vendor \\) -prune -o -type f -iname '?*.txt' -print"
	if code != 0 || !strings.Contains(out, want) || !strings.Contains(out, "Approximate") {
		t.Errorf("code=%d\n got %s\nwant %s", code, out, want)
	}
}

// TestExplainParity runs the printed find command (read-only) and checks it
// finds the same files as nlf for the cases --explain is meant to cover:
// literal glob characters in excludes, a root named like an excluded
// directory, size and time boundaries, and sorted output including ties
// and sub-second mtimes (compared in order).
func TestExplainParity(t *testing.T) {
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		t.Skip("--explain targets BSD tools on macOS and GNU tools on Linux")
	}
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"real/a.txt": "TODO", "real/big.txt": strings.Repeat("x", 2048),
		"real/vendor/v.txt": "TODO", "real/d/vendor": "TODO", "real/src/gen/g.txt": "", "real/src/keep/k.txt": "",
		"real/sp ace/s.txt": "TODO", "real/it's.txt": "",
		"real/.hid/h.txt": "", "real/.git/g.txt": "", "real/.txt": "", "real/zero.bin": "",
		"real/a[1]/b.txt": "", "real/a1/b.txt": "", "real/star*/b.txt": "", "real/starX/b.txt": "",
		"real/x/a[1]/c.txt": "", "real/x/a1/c.txt": "", "real/zh.txt": "發生錯誤",
		"real/k1023.bin": strings.Repeat("x", 1023), "real/k1024.bin": strings.Repeat("x", 1024),
		"real/k1025.bin": strings.Repeat("x", 1025),
		// Equal sizes: the tie is broken by path.
		"real/zz-big.bin": strings.Repeat("x", 4096), "real/aa-big.bin": strings.Repeat("x", 4096),
		"real/mm-big.bin": strings.Repeat("x", 4096), "real/vendor/sub/u.txt": "", "real/vendor/vendor/w.txt": ""})
	// Every mtime is fixed. The day 2026-09-28 is not a DST transition in
	// common time zones; CI runs in UTC.
	loc := time.Local
	day := time.Date(2026, 9, 28, 0, 0, 0, 0, loc)
	next := time.Date(2026, 9, 29, 0, 0, 0, 0, loc)
	top := time.Date(2026, 10, 2, 0, 0, 0, 0, loc)
	mtimes := map[string]time.Time{
		"t-before.txt": day.Add(-500 * time.Millisecond), "t-start.txt": day, "t-start5.txt": day.Add(500 * time.Millisecond),
		"t-end5.txt": next.Add(-500 * time.Millisecond), "t-end1ns.txt": next.Add(-time.Nanosecond), "t-next.txt": next,
		"t-next5.txt": next.Add(500 * time.Millisecond), "t-0930.txt": time.Date(2026, 9, 30, 12, 0, 0, 0, loc),
		"t-1001.txt": time.Date(2026, 10, 1, 0, 0, 0, 0, loc),
		// Equal mtimes (broken by path) and a later mtime in the same second.
		"tie-b.txt": top.Add(250 * time.Millisecond), "tie-a.txt": top.Add(250 * time.Millisecond),
		"zz-sub.txt": top.Add(750 * time.Millisecond), // after the ties by path, before them by mtime
	}
	for n := range mtimes {
		mkfiles(t, filepath.Join(dir, "real"), map[string]string{n: ""})
	}
	base := time.Date(2026, 9, 20, 12, 0, 0, 0, loc)
	if err := filepath.WalkDir(filepath.Join(dir, "real"), func(p string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		mt, ok := mtimes[d.Name()]
		if !ok {
			mt = base
		}
		return os.Chtimes(p, mt, mt)
	}); err != nil {
		t.Fatal(err)
	}
	for n, want := range mtimes {
		fi, err := os.Stat(filepath.Join(dir, "real", n))
		if err != nil {
			t.Fatal(err)
		}
		if !fi.ModTime().Equal(want) {
			t.Fatalf("%s: mtime %v, want %v; the file system must keep nanosecond mtimes", n, fi.ModTime(), want)
		}
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(filepath.Join(dir, "real"), link); err != nil {
		t.Fatal(err)
	}
	// A symlinked root whose own name is excluded.
	if err := os.MkdirAll(filepath.Join(dir, "sym"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(dir, "real", "vendor"), filepath.Join(dir, "sym", "vendor")); err != nil {
		t.Fatal(err)
	}
	list := func(root, s string, sorted bool) string {
		var ps []string
		for _, l := range strings.Split(strings.TrimSpace(s), "\n") {
			if l != "" {
				ps = append(ps, strings.TrimPrefix(strings.TrimPrefix(l, root), "/"))
			}
		}
		if !sorted {
			slices.Sort(ps)
		}
		return strings.Join(ps, ",")
	}
	// runFind runs the command --explain prints for flags and q, through
	// sh -c (read-only); find must not warn, e.g. about option placement.
	runFind := func(root, q string, flags []string) (cmd, out string) {
		t.Helper()
		ex, _, _ := nlf(t, append(append(append([]string{"--explain"}, flags...), "--dir", root), q)...)
		lines := strings.Split(strings.TrimSpace(ex), "\n")
		cmd = strings.TrimSpace(lines[len(lines)-1])
		var stderr bytes.Buffer
		c := exec.Command("sh", "-c", cmd)
		c.Stderr = &stderr
		findOut, err := c.Output()
		if err != nil {
			t.Fatalf("%s %v: %s: %v\n%s", q, flags, cmd, err, stderr.String())
		}
		if stderr.Len() > 0 {
			t.Errorf("%s %v: %s: find wrote to stderr:\n%s", q, flags, cmd, stderr.String())
		}
		return cmd, string(findOut)
	}
	// check compares nlf and find; nlf may exit 1 only when it found nothing.
	// With mustFind, nlf must find something, so the comparison is not vacuous.
	check := func(root, q string, sorted, mustFind bool, flags ...string) {
		t.Helper()
		out, errOut, code := nlf(t, append(append(append([]string{}, flags...), "--dir", root), q)...)
		if code != 0 && (code != 1 || out != "") {
			t.Errorf("%s %v in %s: nlf exit %d: %s", q, flags, root, code, errOut)
		}
		if mustFind && out == "" {
			t.Errorf("%s %v in %s: nlf found nothing", q, flags, root)
		}
		cmd, findOut := runFind(root, q, flags)
		if a, b := list(root, out, sorted), list(root, findOut, sorted); a != b {
			t.Errorf("%s %v in %s:\n cmd  %s\n nlf  %s\n find %s", q, flags, root, cmd, a, b)
		}
	}
	for _, q := range []string{"txt files", "exclude vendor", "exclude ./src/gen", "exclude src/gen/",
		"larger than 1KB", "at most 1KB", `containing "TODO"`,
		`exclude "a[1]"`, `exclude "star*"`, `exclude "x/a[1]"`, "at least 0 bytes", "smaller than 1KB", "at least 1KB",
		"modified on 2026-09-28", "2026-09-28 或 2026-09-30", `包含 "錯誤"`} {
		check(link, q, false, true)
	}
	for _, q := range []string{"largest 1", "largest 2", "newest 2", "newest 3", "largest txt files"} {
		check(link, q, true, true)
	}
	for _, root := range []string{filepath.Join(dir, "real", "vendor"), filepath.Join(dir, "real", "vendor") + "/",
		filepath.Join(dir, "sym", "vendor")} {
		check(root, "exclude vendor", false, true)
		check(root, "txt files exclude VENDOR", false, true)
	}

	// Depth: -mindepth / -maxdepth, or a -path test for the minimum when
	// directories are excluded.
	check(link, "txt files", false, false, "--max-depth", "0")
	for _, c := range []struct {
		q     string
		flags []string
	}{
		{"txt files", []string{"--max-depth", "1"}},
		{"exclude vendor", []string{"--max-depth", "2"}},
		{`containing "TODO"`, []string{"--max-depth", "1"}},
		{"txt files", []string{"--min-depth", "2"}},
		{"exclude vendor", []string{"--min-depth", "2"}},
		{"exclude vendor", []string{"--min-depth", "3"}},
		{`exclude "x/a[1]"`, []string{"--min-depth", "3", "--max-depth", "3"}},
		{"at least 1KB", []string{"--min-depth", "1", "--max-depth", "1"}},
	} {
		check(link, c.q, false, true, c.flags...)
	}
	check(link, "largest 2", true, true, "--max-depth", "1")
	check(link, "newest 3", true, true, "--max-depth", "1")
	check(link, "largest 2", true, true, "--min-depth", "2")
	for _, root := range []string{filepath.Join(dir, "real", "vendor"), filepath.Join(dir, "real", "vendor") + "/",
		filepath.Join(dir, "sym", "vendor")} {
		check(root, "exclude vendor", false, true, "--min-depth", "2")
		check(root, "exclude vendor", false, true, "--max-depth", "1")
	}
	// A file as the root is depth 0: --max-depth 0 keeps it, --min-depth 1 does not.
	file := filepath.Join(dir, "real", "a.txt")
	for _, c := range []struct {
		flags []string
		want  string
	}{{[]string{"--max-depth", "0"}, file}, {[]string{"--min-depth", "1"}, ""}} {
		out, errOut, code := nlf(t, append(append([]string{}, c.flags...), "--dir", file, "txt files")...)
		if strings.TrimSpace(out) != c.want || (c.want == "" && code != 1) || (c.want != "" && code != 0) {
			t.Errorf("file root %v: nlf code=%d out=%q err=%q, want %q", c.flags, code, out, errOut, c.want)
		}
		cmd, findOut := runFind(file, "txt files", c.flags)
		if strings.TrimSpace(findOut) != c.want {
			t.Errorf("file root %v: %s printed %q, want %q", c.flags, cmd, findOut, c.want)
		}
	}
}

// docExample is an "nlf [flags] <query>" line found in the help or README.
type docExample struct {
	flags []string
	query string
}

// docExamples extracts every nlf invocation with a double- or single-quoted query.
func docExamples(doc string) []docExample {
	re := regexp.MustCompile(`nlf((?: (?:--explain|--confirm|-l|--long|-0|--null|(?:-d|--dir) [^\s"']+|--(?:max|min)-depth[ =]\d+))*) (?:"((?:[^"\\]|\\.)*)"|'([^']*)')`)
	var out []docExample
	for _, m := range re.FindAllStringSubmatch(doc, -1) {
		q := strings.ReplaceAll(m[2], `\"`, `"`)
		if m[3] != "" {
			q = m[3]
		}
		out = append(out, docExample{strings.Fields(m[1]), q})
	}
	return out
}

// helpConditionFragments returns the example phrases listed under
// "Supported conditions" in the help: items are separated by two spaces or " / ".
func helpConditionFragments(t *testing.T) []string {
	t.Helper()
	_, block, ok := strings.Cut(usage, "Supported conditions (combined with AND):\n")
	if !ok {
		t.Fatal("help has no Supported conditions section")
	}
	block, _, _ = strings.Cut(block, "\n  Not supported:")
	var frags []string
	for _, line := range strings.Split(block, "\n") {
		items := regexp.MustCompile(`  +| / `).Split(strings.TrimSpace(line), -1)
		for _, it := range items[1:] { // items[0] is the row label
			if it != "" && !strings.HasPrefix(it, "(") {
				frags = append(frags, it)
			}
		}
	}
	return frags
}

// TestDocsMatchBehavior parses every example in the help text and in the
// README, and checks that the two documents agree on flags, --explain
// wording and exit codes.
func TestDocsMatchBehavior(t *testing.T) {
	readme, err := os.ReadFile("README.md")
	if err != nil {
		t.Fatal(err)
	}
	var examples []string
	for _, doc := range []string{usage, string(readme)} {
		for _, ex := range docExamples(doc) {
			examples = append(examples, ex.query)
		}
	}
	frags := helpConditionFragments(t)
	if len(frags) < 30 {
		t.Fatalf("found only %d help condition examples; did the help format change?", len(frags))
	}
	examples = append(examples, frags...)
	inTable := false
	cell := regexp.MustCompile("`([^`]+)`")
	for _, line := range strings.Split(string(readme), "\n") {
		switch {
		case strings.HasPrefix(line, "### 支援的條件"):
			inTable = true
		case inTable && strings.HasPrefix(line, "`--explain`"):
			inTable = false
		case inTable && strings.HasPrefix(line, "| ") && !strings.HasPrefix(line, "| 條件"):
			cols := strings.Split(line, "|")
			for _, m := range cell.FindAllStringSubmatch(cols[2], -1) {
				if !strings.HasPrefix(m[1], "--") {
					examples = append(examples, m[1])
				}
			}
		}
	}
	if len(examples) < 100 {
		t.Fatalf("found only %d examples; did the docs format change?", len(examples))
	}
	for _, ex := range examples {
		if _, err := query.Parse(ex, testNow); err != nil {
			t.Errorf("documented example %q does not parse: %v", ex, err)
		}
	}
	for name, doc := range map[string]string{"help": usage, "README": string(readme)} {
		if strings.Contains(doc, "等效") || strings.Contains(strings.ToLower(doc), "equivalent") {
			t.Errorf("%s still calls the --explain command equivalent", name)
		}
		for _, flag := range []string{"--explain", "--confirm", "--long", "--null", "--dir", "--help", "--version", "--max-depth", "--min-depth"} {
			if !strings.Contains(doc, flag) {
				t.Errorf("%s does not document %s", name, flag)
			}
		}
	}
	// Both documents list the same real-world examples.
	for _, ex := range docExamples(usage) {
		if len(ex.flags) == 0 && strings.HasPrefix(ex.query, "find ") || strings.Contains(ex.query, "TODO") ||
			strings.Contains(ex.query, "largest") || strings.Contains(ex.query, "newest") {
			continue // the English examples are help-only
		}
		line := "nlf " + strings.Join(append(ex.flags, ""), " ")
		if !strings.Contains(string(readme), line+`"`+ex.query+`"`) && !strings.Contains(string(readme), line+"'"+ex.query+"'") {
			t.Errorf("README is missing help example: %s%q", line, ex.query)
		}
	}
	// Exit codes: a cancelled --confirm is a success in both documents.
	if !strings.Contains(usage, "0  files found, or --confirm was cancelled") ||
		!strings.Contains(string(readme), "`0` 有找到，或 `--confirm` 被使用者取消") {
		t.Error("help and README disagree on the exit status of a cancelled --confirm")
	}
}

// TestDocExamplesRun runs every example from the help and README against a
// sandbox: each must be understood (exit 0 or 1, never 2).
func TestDocExamplesRun(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	mkfiles(t, home, map[string]string{"Downloads/vpn.ovpn": "", "Documents/a.md": "", "My Docs/b.txt": "",
		"work/main.go": "package main // TODO", "work/vendor/v.go": "", "work/ip.txt": "20.2.250.249",
		"work/node_modules/x.go": "", "work/src/s.go": ""})
	readme, err := os.ReadFile("README.md")
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for _, ex := range append(docExamples(usage), docExamples(string(readme))...) {
		key := strings.Join(ex.flags, " ") + "|" + ex.query
		if seen[key] {
			continue
		}
		seen[key] = true
		// Only examples rooted in the sandbox ("~/...", or no root at all) are
		// really run; any other root (/, /etc, /var/log, ./src) is only checked
		// with --explain, so tests never walk the real file system.
		args := append([]string{}, ex.flags...)
		root := ""
		if i := slices.Index(args, "--dir"); i >= 0 && i+1 < len(args) {
			root = args[i+1]
		} else if q, err := query.Parse(ex.query, testNow); err == nil {
			root = q.Root
		}
		switch {
		case root == "":
			args = append(args, "--dir", filepath.Join(home, "work"))
		case !strings.HasPrefix(root, "~"):
			args = slices.DeleteFunc(args, func(a string) bool {
				return a == "--explain" || a == "--confirm" || a == "-0" || a == "--null"
			})
			args = append([]string{"--explain"}, args...)
		}
		args = append(args, ex.query)
		_, errOut, code := nlfInput(t, "n\n", args...)
		if code != 0 && code != 1 {
			t.Errorf("nlf %v: exit %d\n%s", args, code, errOut)
		}
	}
	if len(seen) < 15 {
		t.Fatalf("ran only %d examples", len(seen))
	}
}

var updateGolden = flag.Bool("update", false, "rewrite testdata golden files")

// TestHelpSnapshot pins the full help text; run go test -run TestHelpSnapshot -update
// after an intended change.
func TestHelpSnapshot(t *testing.T) {
	out, _, code := nlf(t, "--help")
	if code != 0 {
		t.Fatalf("--help exit %d", code)
	}
	golden := filepath.Join("testdata", "help.golden")
	if *updateGolden {
		if err := os.WriteFile(golden, []byte(out), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	want, err := os.ReadFile(golden)
	if err != nil {
		t.Fatal(err)
	}
	if out != string(want) {
		t.Errorf("--help differs from %s (run with -update if intended):\n%s", golden, out)
	}
}

func TestHelpContent(t *testing.T) {
	out, _, _ := nlf(t, "--help")
	for _, want := range []string{
		// positioning and usage
		"Find files by describing them in Chinese or English", `Usage:
  nlf [flags] "<query>"`,
		// the real-world examples
		`nlf "找 ovpn 檔"`, `nlf --dir ~/Downloads "找 ovpn 檔"`, `nlf '找內容包含 "20.2.250.249" 的檔案'`,
		`nlf "找最近 7 天修改的 go 檔"`, `nlf "找最大的 5 個檔案"`, `nlf "排除 vendor 和 node_modules，找 go 檔"`,
		`nlf --confirm "找 ovpn 檔"`, `nlf --explain "找 ovpn 檔"`,
		"Examples (中文):", "Examples (English):",
		// sections
		"Flags:", "--explain", "--confirm", "-0, --null", "for xargs -0", "Safety:", "Supported conditions", "Search root", "Exit status:", "Tips:",
		// --explain and --confirm
		"never searches", "Nothing is searched", "execution plan", "safety summary", "Only y or Y", `"Cancelled."`,
		// safety
		"Read-only", "does not modify, create or delete files", "does not run a", "shell", "never uses sudo",
		// exit codes
		"0  files found, or --confirm was cancelled", "1  valid query, but no files found", "2  error",
		// tips
		"Quote exact content", "Quote paths with spaces", "never\n    silently widens the search",
		// absolute date comparators
		"nlf '找 2026-09-01 以前修改的檔案'", "nlf 'files modified on or after Sep 1'",
		// filename search
		`filename   檔名是 application.yml  檔名包含 config  filename matches "app-*.yml"`, "(case-sensitive)",
		// boolean OR
		`or         今天或昨天  包含 "ERROR" 或 "FATAL"  大於 1GB 或小於 1KB`, "same kind; rest is AND", "OR across kinds",
		// date & time
		"Date & time", "start <= mtime < end", "an end date is included", "no year = this year",
		"nlf '找 2026 年 9 月的 pdf 檔'", "nlf '找 9 月修改的 yml 檔'", "nlf '查 2026 年 9 月 28 日改了什麼檔案'",
		"nlf '找 9 月 1 日到 9 月 15 日修改的 log'", "nlf '找最近 6 小時修改的 conf'", "nlf '找昨天 23:00 到今天 02:00 修改的 log'",
		"nlf '找本月修改的 yaml'", "nlf 'files modified on 2026-09-28'", "nlf 'log files modified from Sep 1 to Sep 15'",
		"nlf 'conf files modified in the last 6 hours'",
		// depth
		"--max-depth N", "--min-depth N", "(1 = directly inside)",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("--help is missing %q", want)
		}
	}
	// Fits a terminal screen: at most 70 lines of at most 100 columns (CJK counts double).
	lines := strings.Split(strings.TrimRight(out, "\n"), "\n")
	if len(lines) > 70 {
		t.Errorf("--help has %d lines; keep it to one screen (<= 70)", len(lines))
	}
	for i, l := range lines {
		w := 0
		for _, r := range l {
			if unicode.Is(unicode.Han, r) || r == '，' || r == '「' || r == '」' {
				w += 2
			} else {
				w++
			}
		}
		if w > 100 {
			t.Errorf("--help line %d is %d columns wide: %s", i+1, w, l)
		}
	}
}

func TestConfirmRunsOnlyOnYes(t *testing.T) {
	dir := fixture(t)
	direct, _, _ := nlf(t, "--dir", dir, "go files")
	if direct == "" {
		t.Fatal("fixture has no go files")
	}
	for _, in := range []string{"y\n", "Y\n", "y\r\n", "y"} {
		out, errOut, code := nlfInput(t, in, "--confirm", "--dir", dir, "go files")
		if code != 0 || out != direct || strings.Contains(errOut, "Cancelled.") {
			t.Errorf("input %q: code=%d out=%q err=%q", in, code, out, errOut)
		}
	}
	for _, in := range []string{"n\n", "N\n", "\n", "", "yes\n", "YES\n", " y\n", "y \n", "yy\n", "ok\n", "是\n"} {
		out, errOut, code := nlfInput(t, in, "--confirm", "--dir", dir, "go files")
		if code != 0 || out != "" || !strings.HasSuffix(errOut, "Cancelled.\n") {
			t.Errorf("input %q: code=%d out=%q err=%q, want no search and Cancelled.", in, code, out, errOut)
		}
	}
}

func TestConfirmReadsOnlyOneLine(t *testing.T) {
	dir := fixture(t)
	// "n" on the first line cancels even if a later line says "y".
	if out, _, _ := nlfInput(t, "n\ny\n", "--confirm", "--dir", dir, "go files"); out != "" {
		t.Errorf("searched after n: %q", out)
	}
}

func TestConfirmPlan(t *testing.T) {
	dir := fixture(t)
	out, plan, code := nlfInput(t, "", "--confirm", "--dir", dir, "go files larger than 1KB excluding vendor, top 3")
	if code != 0 || out != "" {
		t.Fatalf("code=%d stdout=%q", code, out)
	}
	for _, want := range []string{
		"NLF execution plan", "Search root:\n  " + dir, "Conditions:", "extension:", ".go", "size:", "> 1 KB",
		"exclude dirs:", "vendor", "limit:", "3",
		"Approximate " + query.PlatformFor(runtime.GOOS).Name() + " command", "nlf does not execute it",
		"find " + dir + " -type d \\( -iname vendor \\) -prune -o -type f -iname '?*.go' -size +1024c -print | head -n 3",
		"Read files:        YES", "Modify files:      NO", "Create files:      NO", "Delete files:      NO",
		"Execute shell:     NO", "sudo required:     NO", "Risk:\n  READ-ONLY", "Execute this search? [y/N]: ",
	} {
		if !strings.Contains(plan, want) {
			t.Errorf("plan missing %q:\n%s", want, plan)
		}
	}
	for _, bad := range []string{"WRITE", "DESTRUCTIVE", ": YES\n  Modify"} {
		if strings.Contains(plan, bad) {
			t.Errorf("plan contains %q:\n%s", bad, plan)
		}
	}
	// The plan shows the depth limit and the find command applies it.
	out, plan, code = nlfInput(t, "", "--confirm", "--max-depth", "1", "--dir", dir, "go files")
	if code != 0 || out != "" {
		t.Fatalf("--max-depth 1: code=%d stdout=%q", code, out)
	}
	for _, want := range []string{"depth:", "at most 1 level (", "find " + dir + " -maxdepth 1 -type f -iname '?*.go' -print"} {
		if !strings.Contains(plan, want) {
			t.Errorf("--max-depth 1 plan missing %q:\n%s", want, plan)
		}
	}
}

func TestConfirmAndExplainAreDistinct(t *testing.T) {
	dir := fixture(t)
	// --explain never prompts or searches, whatever is on stdin.
	out, errOut, code := nlfInput(t, "y\n", "--explain", "--dir", dir, "go files")
	if code != 0 || strings.Contains(out, "main.go\n") || strings.Contains(out+errOut, "[y/N]") ||
		strings.Contains(out+errOut, "NLF execution plan") || !strings.Contains(out, "Understood as:") {
		t.Errorf("--explain: code=%d out=%q err=%q", code, out, errOut)
	}
	// Both together are rejected rather than guessing which one was meant.
	out, errOut, code = nlfInput(t, "y\n", "--explain", "--confirm", "--dir", dir, "go files")
	if code != 2 || out != "" || !strings.Contains(errOut, "use one of them") {
		t.Errorf("--explain --confirm: code=%d out=%q err=%q", code, out, errOut)
	}
	// Without --confirm nothing is read from stdin and the search runs.
	out, errOut, code = nlfInput(t, "n\n", "--dir", dir, "go files")
	if code != 0 || out == "" || strings.Contains(errOut, "[y/N]") {
		t.Errorf("plain run: code=%d out=%q err=%q", code, out, errOut)
	}
}

func TestConfirmParseErrorDoesNotPrompt(t *testing.T) {
	_, errOut, code := nlfInput(t, "y\n", "--confirm", "files without TODO")
	if code != 2 || strings.Contains(errOut, "[y/N]") {
		t.Errorf("code=%d err=%q", code, errOut)
	}
}

// TestProductionCodeIsReadOnly backs the --confirm safety summary: the
// non-test sources must not import process-execution packages or call any
// os function that creates, changes or removes files. Without os.Create,
// os.OpenFile or os.WriteFile no file can be opened for writing at all.
func TestProductionCodeIsReadOnly(t *testing.T) {
	forbiddenImports := map[string]bool{"os/exec": true, "syscall": true, "unsafe": true, "plugin": true,
		"io/ioutil": true, "golang.org/x/sys/unix": true}
	forbiddenOS := map[string]bool{"StartProcess": true, "Create": true, "CreateTemp": true, "WriteFile": true,
		"OpenFile": true, "NewFile": true, "Remove": true, "RemoveAll": true, "Rename": true, "Mkdir": true,
		"MkdirAll": true, "MkdirTemp": true, "Chmod": true, "Chown": true, "Lchown": true, "Chtimes": true,
		"Symlink": true, "Link": true, "Truncate": true, "Setenv": true, "Unsetenv": true, "Clearenv": true}
	var files []string
	for _, pattern := range []string{"*.go", "internal/*/*.go"} {
		m, err := filepath.Glob(pattern)
		if err != nil {
			t.Fatal(err)
		}
		files = append(files, m...)
	}
	fset := token.NewFileSet()
	checked := 0
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		file, err := parser.ParseFile(fset, f, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		checked++
		for _, imp := range file.Imports {
			if p, _ := strconv.Unquote(imp.Path.Value); forbiddenImports[p] {
				t.Errorf("%s imports %s", fset.Position(imp.Pos()), p)
			}
		}
		ast.Inspect(file, func(n ast.Node) bool {
			if sel, ok := n.(*ast.SelectorExpr); ok {
				if id, ok := sel.X.(*ast.Ident); ok && id.Name == "os" && forbiddenOS[sel.Sel.Name] {
					t.Errorf("%s uses os.%s", fset.Position(sel.Pos()), sel.Sel.Name)
				}
			}
			return true
		})
	}
	if checked < 5 {
		t.Fatalf("checked only %d production files", checked)
	}
	if e := search.RunEffects(); e != (search.Effects{ReadFiles: true}) || e.Risk() != "READ-ONLY" {
		t.Errorf("RunEffects() = %+v, %s; want read-only", e, e.Risk())
	}
}

func TestPermissionHintByOS(t *testing.T) {
	mac := []string{permissionHint(query.Chinese, "darwin"), permissionHint(query.English, "darwin")}
	linux := []string{permissionHint(query.Chinese, "linux"), permissionHint(query.English, "linux")}
	for _, h := range mac {
		if !strings.Contains(h, "完整磁碟取用權限") && !strings.Contains(h, "Full Disk Access") {
			t.Errorf("macOS hint lacks Full Disk Access: %q", h)
		}
	}
	for _, h := range linux {
		for _, bad := range []string{"macOS", "Full Disk Access", "完整磁碟", "~/Library", "系統設定"} {
			if strings.Contains(h, bad) {
				t.Errorf("Linux hint mentions %q: %q", bad, h)
			}
		}
		if !strings.Contains(h, "ls -ld") || !strings.Contains(h, "SELinux") || !strings.Contains(h, "sudo") {
			t.Errorf("Linux hint lacks permission advice: %q", h)
		}
	}
	if !strings.Contains(linux[0], "提示") || !strings.Contains(linux[1], "hint:") {
		t.Errorf("Linux hints are not localized: %q", linux)
	}
}

func TestExplainAndConfirmUseHostPlatform(t *testing.T) {
	p := query.PlatformFor(runtime.GOOS)
	dir := fixture(t)
	out, _, _ := nlf(t, "--explain", "--dir", dir, "largest 2")
	_, plan, _ := nlfInput(t, "n\n", "--confirm", "--dir", dir, "largest 2")
	wantFlag := map[query.Platform]string{query.MacOS: "stat -f '%z %N'", query.Linux: "stat -c '%s %n'"}[p]
	for name, text := range map[string]string{"--explain": out, "--confirm": plan} {
		if !strings.Contains(text, "Approximate "+p.Name()+" command") || !strings.Contains(text, wantFlag) {
			t.Errorf("%s on %s should show a %s command with %s:\n%s", name, runtime.GOOS, p.Name(), wantFlag, text)
		}
	}
}

// TestGNUSortPipeline runs the Linux sort pipeline with real GNU stat (stat on
// Linux, gstat from coreutils on macOS, with gsort when it is installed) and
// checks it orders like nlf, including equal sizes, equal mtimes and mtimes
// that differ by less than a second.
func TestGNUSortPipeline(t *testing.T) {
	stat, sortCmd := "stat", ""
	if runtime.GOOS != "linux" {
		if _, err := exec.LookPath("gstat"); err != nil {
			t.Skip("GNU stat not available (install coreutils for gstat)")
		}
		stat = "gstat"
		if _, err := exec.LookPath("gsort"); err == nil {
			sortCmd = "gsort"
		}
	}
	dir := t.TempDir()
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i, f := range []struct {
		name string
		size int
		mt   time.Duration
	}{
		{"a.txt", 100, 0}, {"b b.txt", 201, time.Hour}, {"c.txt", 302, 2 * time.Hour}, {"d.txt", 103, 3 * time.Hour},
		// Equal sizes and equal mtimes are ordered by path.
		{"z-tie.txt", 302, 3 * time.Hour}, {"e-tie.txt", 302, 3 * time.Hour},
		// Later than the ties by less than a second.
		{"y-sub.txt", 5, 3*time.Hour + 400*time.Millisecond}, {"x-sub.txt", 5, 3*time.Hour + 399*time.Millisecond},
	} {
		p := filepath.Join(dir, f.name)
		mkfiles(t, dir, map[string]string{f.name: strings.Repeat("x", f.size)})
		mt := base.Add(f.mt)
		if err := os.Chtimes(p, mt, mt); err != nil {
			t.Fatal(err)
		}
		if fi, err := os.Stat(p); err != nil || !fi.ModTime().Equal(mt) {
			t.Fatalf("file %d: the file system must keep nanosecond mtimes (err %v)", i, err)
		}
	}
	for _, c := range []struct{ in, want string }{
		{"largest 4", "c.txt,e-tie.txt,z-tie.txt,b b.txt"},
		{"newest 5", "y-sub.txt,x-sub.txt,d.txt,e-tie.txt,z-tie.txt"},
		{"newest 8", "y-sub.txt,x-sub.txt,d.txt,e-tie.txt,z-tie.txt,c.txt,b b.txt,a.txt"},
	} {
		q, err := query.Parse(c.in, testNow)
		if err != nil {
			t.Fatal(err)
		}
		q.Root = dir
		cmd := query.FindCommand(q, query.Linux)
		if !strings.Contains(cmd, "stat -c") {
			t.Fatalf("not a GNU command: %s", cmd)
		}
		cmd = strings.Replace(cmd, "-exec stat ", "-exec "+stat+" ", 1)
		if sortCmd != "" {
			cmd = strings.Replace(cmd, "| LC_ALL=C sort ", "| LC_ALL=C "+sortCmd+" ", 1)
		}
		got, err := exec.Command("sh", "-c", cmd).Output()
		if err != nil {
			t.Fatalf("%s: %v", cmd, err)
		}
		want, _, _ := nlf(t, "--dir", dir, c.in)
		if string(got) != want {
			t.Errorf("%s:\n cmd  %s\n find %q\n nlf  %q", c.in, cmd, got, want)
		}
		if p := strings.Join(paths(dir, want), ","); p != c.want {
			t.Errorf("%s: nlf order %s, want %s", c.in, p, c.want)
		}
	}
}

// TestDateSearchEndToEnd runs date queries through the CLI against files
// with fixed modification times around the month and day boundaries.
func TestDateSearchEndToEnd(t *testing.T) {
	dir := t.TempDir()
	loc := testNow.Location()
	times := map[string]time.Time{
		"a-0831-2359.log": time.Date(2026, 8, 31, 23, 59, 0, 0, loc),
		"b-0901-0000.log": time.Date(2026, 9, 1, 0, 0, 0, 0, loc),
		"c-0915-1200.log": time.Date(2026, 9, 15, 12, 0, 0, 0, loc),
		"d-0915-1200.yml": time.Date(2026, 9, 15, 12, 0, 0, 0, loc),
		"e-0926-0130.log": time.Date(2026, 9, 26, 1, 30, 0, 0, loc),
		"f-0926-2330.log": time.Date(2026, 9, 26, 23, 30, 0, 0, loc),
		"g-0927-0130.log": time.Date(2026, 9, 27, 1, 30, 0, 0, loc),
		"h-0930-2359.log": time.Date(2026, 9, 30, 23, 59, 0, 0, loc),
		"i-1001-0000.log": time.Date(2026, 10, 1, 0, 0, 0, 0, loc),
	}
	for n, mt := range times {
		mkfiles(t, dir, map[string]string{n: strings.Repeat("x", len(n))})
		if err := os.Chtimes(filepath.Join(dir, n), mt, mt); err != nil {
			t.Fatal(err)
		}
	}
	// testNow is 2026-09-27 12:00 local.
	tests := []struct {
		query string
		want  string
	}{
		{"找 2026 年 9 月的檔案", "b-0901-0000.log,c-0915-1200.log,d-0915-1200.yml,e-0926-0130.log,f-0926-2330.log,g-0927-0130.log,h-0930-2359.log"},
		{"找 9 月修改的 yml 檔", "d-0915-1200.yml"},
		{"找 9 月 1 日到 9 月 15 日修改的 log", "b-0901-0000.log,c-0915-1200.log"},
		{"找昨天 23:00 到今天 02:00 修改的 log", "f-0926-2330.log,g-0927-0130.log"},
		{"找凌晨 1 點到 2 點修改的檔案", "g-0927-0130.log"},
		{"找今天修改的 log", "g-0927-0130.log"},
		{"找昨天修改的 log", "e-0926-0130.log,f-0926-2330.log"},
		{"找本月最新的 2 個 log", "h-0930-2359.log,g-0927-0130.log"},
		{"files modified on 2026-10-01", "i-1001-0000.log"},
		{"log files from Sep 1 to Sep 15", "b-0901-0000.log,c-0915-1200.log"},
		{"找上個月修改的檔案", "a-0831-2359.log"},
	}
	for _, tt := range tests {
		out, errOut, code := nlf(t, "--dir", dir, tt.query)
		if got := strings.Join(paths(dir, out), ","); code != 0 || got != tt.want {
			t.Errorf("%s: code=%d\n got  %s\n want %s\n%s", tt.query, code, got, tt.want, errOut)
		}
	}
	// Invalid dates and backwards ranges fail with exit 2 and search nothing.
	for _, q := range []string{"找 2026-02-29 的檔案", "找 9/20 到 9/10 的檔案", "找今天 25:00 到 26:00 的檔案", "03/04/2026"} {
		if out, _, code := nlf(t, "--dir", dir, q); code != 2 || out != "" {
			t.Errorf("%s: code=%d out=%q, want exit 2 and no output", q, code, out)
		}
	}
	// --confirm shows the range in the plan.
	_, plan, _ := nlfInput(t, "n\n", "--confirm", "--dir", dir, "找 2026-09-28 修改的 yml 檔")
	if !strings.Contains(plan, "modified:      2026-09-28 00:00:00 <= mtime < 2026-09-29 00:00:00") || !strings.Contains(plan, "time zone:") {
		t.Errorf("plan does not show the date range:\n%s", plan)
	}
}

// TestOrEndToEnd runs OR queries through the CLI and checks that --explain and
// --confirm show OR groups as OR, never as separate AND rows.
func TestOrEndToEnd(t *testing.T) {
	dir := t.TempDir()
	loc := testNow.Location() // testNow is 2026-09-27 12:00 local
	for _, f := range []struct {
		name, data string
		mtime      time.Time
	}{
		{"a.log", "ERROR", time.Date(2026, 9, 27, 9, 0, 0, 0, loc)},
		{"b.log", "FATAL", time.Date(2026, 9, 26, 9, 0, 0, 0, loc)},
		{"c.log", "ERROR", time.Date(2026, 9, 25, 9, 0, 0, 0, loc)},
		{"d.txt", "ERROR", time.Date(2026, 9, 27, 9, 0, 0, 0, loc)},
		{"e.log", "INFO", time.Date(2026, 9, 27, 9, 0, 0, 0, loc)},
	} {
		mkfiles(t, dir, map[string]string{f.name: f.data})
		if err := os.Chtimes(filepath.Join(dir, f.name), f.mtime, f.mtime); err != nil {
			t.Fatal(err)
		}
	}
	for _, c := range []struct{ query, want string }{
		{"找今天或昨天修改的檔案", "a.log,b.log,d.txt,e.log"},
		{"找今天或昨天修改的 log", "a.log,b.log,e.log"},
		{`找包含 "ERROR" 或 "FATAL" 的 log`, "a.log,b.log,c.log"},
		{`找今天或昨天修改，而且包含 "ERROR" 或 "FATAL" 的 log`, "a.log,b.log"},
		{"找 log 或 txt 檔", "a.log,b.log,c.log,d.txt,e.log"},
	} {
		out, errOut, code := nlf(t, "--dir", dir, c.query)
		if got := strings.Join(paths(dir, out), ","); code != 0 || got != c.want {
			t.Errorf("%s: code=%d got %s want %s\n%s", c.query, code, got, c.want, errOut)
		}
	}
	// Mixed kinds and dangling ORs are errors: exit 2, nothing searched.
	for _, q := range []string{"找大於 1GB 或最近 1 小時修改的檔案", `找包含 "ERROR" 或 9 月修改的 log`, "今天 或", "或 今天"} {
		if out, _, code := nlf(t, "--dir", dir, q); code != 2 || out != "" {
			t.Errorf("%s: code=%d out=%q, want exit 2", q, code, out)
		}
	}
	_, errOut, _ := nlf(t, "--dir", dir, "找大於 1GB 或最近 1 小時修改的檔案")
	if !strings.Contains(errOut, "目前 OR 只能連接同類條件") {
		t.Errorf("mixed OR message: %q", errOut)
	}

	q := `find log files containing "ERROR" or "panic" modified today or yesterday`
	ex, _, _ := nlf(t, "--explain", "--dir", dir, q)
	_, plan, _ := nlfInput(t, "n\n", "--confirm", "--dir", dir, q)
	for name, text := range map[string]string{"--explain": ex, "--confirm": plan} {
		for _, want := range []string{`content:       "ERROR" OR "panic" (any one matches, case-sensitive)`,
			"modified:      any of (OR), any one matches:",
			"- 2026-09-27 00:00:00 <= mtime < 2026-09-28 00:00:00", "- 2026-09-26 00:00:00 <= mtime < 2026-09-27 00:00:00",
			"-e ERROR -e panic"} {
			if !strings.Contains(text, want) {
				t.Errorf("%s missing %q:\n%s", name, want, text)
			}
		}
		if strings.Count(text, "content:") != 1 {
			t.Errorf("%s shows the OR terms as separate rows:\n%s", name, text)
		}
	}
	if !strings.Contains(plan, "Risk:\n  READ-ONLY") {
		t.Errorf("--confirm safety changed:\n%s", plan)
	}
}

// TestOrQAHardeningExitCodes: connector collisions, contradicting time OR
// groups and dateless clock alternatives fail at parse time (exit 2) and
// never search.
func TestOrQAHardeningExitCodes(t *testing.T) {
	dir := fixture(t)
	for _, q := range []string{"today or and yesterday", "今天 且 或 昨天", "找今天或昨天修改，而且 7 月或 8 月修改的檔案",
		"找昨天 23:00 到今天 02:00 或 05:00 到 06:00 修改的檔案"} {
		if out, errOut, code := nlf(t, "--dir", dir, q); code != 2 || out != "" || errOut == "" {
			t.Errorf("%s: code=%d out=%q err=%q, want exit 2 with a message", q, code, out, errOut)
		}
	}
	// A plain OR is still accepted (exit 0 or 1, never a parse error).
	if _, errOut, code := nlf(t, "--dir", dir, "today or yesterday"); code == 2 {
		t.Errorf("today or yesterday: exit 2: %s", errOut)
	}
}

// TestFilenameEndToEnd runs filename queries through the CLI.
func TestFilenameEndToEnd(t *testing.T) {
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"application.yml": "", "etc/nginx/nginx.conf": "listen 443", "httpd.conf": "",
		"nginx.conf.bak": "", ".env": "", "core.1234": "", "app-api.log": ""})
	for _, c := range []struct{ query, want string }{
		{"找檔名是 application.yml", "application.yml"},
		{"find files named nginx.conf", "etc/nginx/nginx.conf"},
		{"找檔名以 .bak 結尾", "nginx.conf.bak"},
		{`找檔名符合 "core.*"`, "core.1234"},
		{"找檔名是 nginx.conf 或 httpd.conf", "etc/nginx/nginx.conf,httpd.conf"},
		{`找檔名是 nginx.conf，而且內容包含 "listen 443"`, "etc/nginx/nginx.conf"},
		{"檔名是 .env", ".env"},
	} {
		out, errOut, code := nlf(t, "--dir", dir, c.query)
		if got := strings.Join(paths(dir, out), ","); code != 0 || got != c.want {
			t.Errorf("%s: code=%d got %s want %s\n%s", c.query, code, got, c.want, errOut)
		}
	}
	for _, q := range []string{"檔名是", "filename matches", `檔名符合 "[abc"`, "檔名包含 prod 或今天", "檔名包含 config，不分大小寫"} {
		if out, errOut, code := nlf(t, "--dir", dir, q); code != 2 || out != "" || errOut == "" {
			t.Errorf("%s: code=%d out=%q err=%q, want exit 2", q, code, out, errOut)
		}
	}
	ex, _, _ := nlf(t, "--explain", "--dir", dir, "找今天修改，而且檔名包含 config 的 yml")
	_, plan, _ := nlfInput(t, "n\n", "--confirm", "--dir", dir, "filename is nginx.conf or httpd.conf")
	for name, c := range map[string]struct{ text, row, find string }{
		"--explain": {ex, `檔名           包含 "config" (區分大小寫)`, "-name '*config*'"},
		"--confirm": {plan, `filename:      is "nginx.conf" OR is "httpd.conf" (any one matches, case-sensitive)`, `\( -name nginx.conf -o -name httpd.conf \)`},
	} {
		if !strings.Contains(c.text, c.row) || !strings.Contains(c.text, c.find) {
			t.Errorf("%s missing filename row or -name:\n%s", name, c.text)
		}
	}
	if !strings.Contains(plan, "Risk:\n  READ-ONLY") {
		t.Errorf("--confirm safety changed:\n%s", plan)
	}
}

// TestAbsoluteComparatorsEndToEnd runs before / after comparators through the CLI.
func TestAbsoluteComparatorsEndToEnd(t *testing.T) {
	dir := t.TempDir()
	loc := testNow.Location()
	for n, mt := range map[string]time.Time{
		"before.txt":   time.Date(2026, 8, 31, 23, 59, 59, 0, loc),
		"boundary.txt": time.Date(2026, 9, 1, 0, 0, 0, 0, loc),
		"during.txt":   time.Date(2026, 9, 1, 12, 0, 0, 0, loc),
		"after.txt":    time.Date(2026, 9, 2, 0, 0, 0, 0, loc),
	} {
		mkfiles(t, dir, map[string]string{n: n})
		if err := os.Chtimes(filepath.Join(dir, n), mt, mt); err != nil {
			t.Fatal(err)
		}
	}
	for _, c := range []struct{ query, want string }{
		{"before Sep 1", "before.txt"},
		{"找 2026-09-01 以前修改的檔案", "before.txt"},
		{"on or before Sep 1", "before.txt,boundary.txt,during.txt"},
		{"找 2026-09-01 或以前修改的檔案", "before.txt,boundary.txt,during.txt"},
		{"after Sep 1", "after.txt"},
		{"找 2026-09-01 以後修改的檔案", "after.txt"},
		{"on or after Sep 1", "after.txt,boundary.txt,during.txt"},
		{"since Sep 1", "after.txt,boundary.txt,during.txt"},
		{"before Sep 1 or after Sep 1", "after.txt,before.txt"},
		{"找 9 月以前修改的 txt 檔", "before.txt"},
		{"找檔名包含 u 的檔案，2026-09-01 或以後修改", "boundary.txt,during.txt"}, // bo-u-ndary, d-u-ring
	} {
		out, errOut, code := nlf(t, "--dir", dir, c.query)
		if got := strings.Join(paths(dir, out), ","); code != 0 || got != c.want {
			t.Errorf("%s: code=%d got %s want %s\n%s", c.query, code, got, c.want, errOut)
		}
	}
	for _, q := range []string{"before", "on or after", "before 14:00", "2026-02-29 以前", "找 9 月 20 日以後且 9 月 10 日以前修改的檔案"} {
		if out, errOut, code := nlf(t, "--dir", dir, q); code != 2 || out != "" || errOut == "" {
			t.Errorf("%s: code=%d out=%q err=%q, want exit 2", q, code, out, errOut)
		}
	}
	ex, _, _ := nlf(t, "--explain", "--dir", dir, "after Sep 1")
	_, plan, _ := nlfInput(t, "n\n", "--confirm", "--dir", dir, "on or after Sep 1")
	if !strings.Contains(ex, "after 2026-09-02 00:00:00 (mtime >= 2026-09-02 00:00:00)") ||
		!strings.Contains(plan, "after 2026-09-01 00:00:00 (mtime >= 2026-09-01 00:00:00)") || !strings.Contains(plan, "Risk:\n  READ-ONLY") {
		t.Errorf("explain/confirm bounds:\n%s\n%s", ex, plan)
	}
}

// TestPunctuationSafetyEndToEnd checks that punctuation which could change
// the meaning of a query is rejected before anything runs.
func TestPunctuationSafetyEndToEnd(t *testing.T) {
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"app/error.log": "ERROR: disk full", "app/ok.log": "all good"})
	rejected := map[string]string{
		`log files !containing "ERROR"`:                            `"!" may mean NOT`,
		"larger than 1GB or (smaller than 1KB and modified today)": "parentheses are not supported",
		"filename matches core.?":                                  "punctuation right after an unquoted value is ambiguous",
		"包含 錯誤？":                                                   "標點緊接在沒有引號的值後面",
		"find ? log files":                                         `"?" is only allowed at the end of the query`,
		"exclude .":                                                `cannot exclude "." or ".."`,
	}
	for q, want := range rejected {
		out, errOut, code := nlf(t, "--dir", dir, q)
		if code != 2 || out != "" || !strings.Contains(errOut, want) {
			t.Errorf("%s: code=%d out=%q err=%q, want exit 2 with %q", q, code, out, errOut, want)
		}
	}
	// --explain and --confirm show no plan and never prompt for a rejected query.
	q := `log files !containing "ERROR"`
	for _, flag := range []string{"--explain", "--confirm"} {
		out, errOut, code := nlfInput(t, "y\n", flag, "--dir", dir, q)
		if code != 2 || out != "" || strings.Contains(errOut, "[y/N]") || strings.Contains(errOut, "find ") ||
			!strings.Contains(errOut, `"!" may mean NOT`) {
			t.Errorf("%s: code=%d out=%q err=%q", flag, code, out, errOut)
		}
	}
	// Allowed punctuation still searches as before.
	out, errOut, code := nlf(t, "--dir", dir, "find log files?")
	if got := paths(dir, out); code != 0 || !slices.Equal(got, []string{"app/error.log", "app/ok.log"}) {
		t.Errorf("find log files?: code=%d got=%v err=%q", code, got, errOut)
	}
	out, errOut, code = nlf(t, "--dir", dir, `log files containing "ERROR".`)
	if got := paths(dir, out); code != 0 || !slices.Equal(got, []string{"app/error.log"}) {
		t.Errorf(`containing "ERROR".: code=%d got=%v err=%q`, code, got, errOut)
	}
}

// TestPunctuationQARegressionsEndToEnd covers the QA findings through the CLI.
func TestPunctuationQARegressionsEndToEnd(t *testing.T) {
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"parent.go": "", "src/a.go": "", "src/a.log": "", "src/a.log1": ""})
	sub := filepath.Join(dir, "src")
	// "檔.." used to set the root to "..": with --dir that was a location
	// conflict, and without it the parent directory was searched.
	out, errOut, code := nlf(t, "--dir", sub, "找 go 檔..")
	if got := paths(dir, out); code != 0 || !slices.Equal(got, []string{"src/a.go"}) {
		t.Errorf("找 go 檔..: code=%d got=%v err=%q, want only src/a.go", code, got, errOut)
	}
	// A spaced ".." is still the parent directory.
	if _, errOut, code := nlf(t, "--dir", sub, "go files in .."); code != 2 || !strings.Contains(errOut, "already names a location") {
		t.Errorf("go files in ..: code=%d err=%q, want the --dir conflict", code, errOut)
	}
	// "*.log?" used to drop the "?" and match a.log.
	for _, q := range []string{"*.log?", "?", "。", ",", "exclude ./", "exclude /", "包含 錯誤.."} {
		if out, errOut, code := nlf(t, "--dir", sub, q); code != 2 || out != "" || errOut == "" {
			t.Errorf("%s: code=%d out=%q err=%q, want exit 2 with a message", q, code, out, errOut)
		}
	}
	out, errOut, code = nlf(t, "--dir", sub, `filename matches "*.log?"`)
	if got := paths(dir, out); code != 0 || !slices.Equal(got, []string{"src/a.log1"}) {
		t.Errorf("quoted glob: code=%d got=%v err=%q", code, got, errOut)
	}
}

// TestParentPathRoot: a location ending in ".." in the query searches that
// directory, two levels up here, not one.
func TestParentPathRoot(t *testing.T) {
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"up.go": "", "b/mid.go": "", "b/src/x.go": ""})
	out, errOut, code := nlf(t, "go files in "+filepath.Join(dir, "b", "src")+"/../..")
	got := strings.Join(paths(filepath.Join(dir, "b", "src", "..", ".."), out), ",")
	if code != 0 || got != "b/mid.go,b/src/x.go,up.go" {
		t.Errorf("code=%d got %s\n%s", code, got, errOut)
	}
}

// TestMain lets subprocess tests run the real nlf main() from the test
// binary: they set NLF_TEST_RUN_MAIN=1 in the child's environment only
// (exporting it in your own shell makes every test run main() and fail).
func TestMain(m *testing.M) {
	if os.Getenv("NLF_TEST_RUN_MAIN") == "1" {
		main()
		return
	}
	os.Exit(m.Run())
}

func TestParseArgsNull(t *testing.T) {
	for _, a := range []string{"-0", "--null"} {
		o, err := parseArgs([]string{a, "go", "files"})
		if err != nil || !o.null || strings.Join(o.words, " ") != "go files" {
			t.Errorf("%s: opts=%+v err=%v", a, o, err)
		}
	}
	for _, a := range []string{"-l0", "-0l", "-00", "--null=1", "--nul"} {
		if _, err := parseArgs([]string{a, "go", "files"}); err == nil || err.Error() != "unknown flag "+a {
			t.Errorf("%s: err=%v, want unknown flag %s", a, err, a)
		}
	}
	o, err := parseArgs([]string{"--", "-0"})
	if err != nil || o.null || !slices.Equal(o.words, []string{"-0"}) {
		t.Errorf("-- -0: opts=%+v err=%v, want -0 as a word", o, err)
	}
}

func TestFlagConflicts(t *testing.T) {
	set := map[string]func(*options){
		"explain":  func(o *options) { o.explain = true },
		"confirm":  func(o *options) { o.confirm = true },
		"long":     func(o *options) { o.long = true },
		"null":     func(o *options) { o.null = true },
		"help":     func(o *options) { o.help = true },
		"version":  func(o *options) { o.version = true },
		"dir":      func(o *options) { o.dir = "x" },
		"maxdepth": func(o *options) { o.maxDepth, o.maxDepthSet = 1, true },
		"mindepth": func(o *options) { o.minDepth, o.minDepthSet = 1, true },
	}
	want := map[[2]string]string{
		{"explain", "confirm"}:  "--explain only shows the plan and --confirm asks before searching; use one of them",
		{"long", "null"}:        "-0/--null prints paths for programs and -l/--long adds size and time for people; use one of them",
		{"explain", "null"}:     "--explain never searches, so -0/--null has no paths to print; use one of them",
		{"null", "help"}:        "-h/--help prints text, not NUL-separated paths; use it without -0/--null",
		{"null", "version"}:     "--version prints text, not NUL-separated paths; use it without -0/--null",
		{"explain", "long"}:     "--explain never searches, so -l/--long has no files to list; use one of them",
		{"help", "version"}:     "-h/--help and --version both print text; use one of them",
		{"explain", "help"}:     "-h/--help prints text, not a query explanation; use it without --explain",
		{"confirm", "help"}:     "-h/--help prints text and never searches; use it without --confirm",
		{"long", "help"}:        "-h/--help prints text, not a file list; use it without -l/--long",
		{"help", "dir"}:         "-h/--help never searches, so -d/--dir has no effect; use it without -d/--dir",
		{"explain", "version"}:  "--version prints text, not a query explanation; use it without --explain",
		{"confirm", "version"}:  "--version prints text and never searches; use it without --confirm",
		{"long", "version"}:     "--version prints text, not a file list; use it without -l/--long",
		{"version", "dir"}:      "--version never searches, so -d/--dir has no effect; use it without -d/--dir",
		{"help", "maxdepth"}:    "-h/--help never searches, so --max-depth has no effect; use it without --max-depth",
		{"help", "mindepth"}:    "-h/--help never searches, so --min-depth has no effect; use it without --min-depth",
		{"version", "maxdepth"}: "--version never searches, so --max-depth has no effect; use it without --max-depth",
		{"version", "mindepth"}: "--version never searches, so --min-depth has no effect; use it without --min-depth",
	}
	names := []string{"explain", "confirm", "long", "null", "help", "version", "dir", "maxdepth", "mindepth"}
	matched, total := 0, 0
	for i, a := range names {
		for _, b := range names[i+1:] {
			total++
			o := &options{}
			set[a](o)
			set[b](o)
			msg, ok := want[[2]string{a, b}]
			if !ok {
				msg, ok = want[[2]string{b, a}]
			}
			err := checkConflicts(o)
			switch {
			case ok && (err == nil || err.Error() != msg):
				t.Errorf("%s+%s: err=%v, want %q", a, b, err, msg)
			case !ok && err != nil:
				t.Errorf("%s+%s: err=%v, want nil", a, b, err)
			}
			if ok {
				matched++
			}
		}
	}
	if matched != len(want) || matched != 19 || total != 36 {
		t.Errorf("matched %d conflict pairs of %d, want %d (19) of 36", matched, total, len(want))
	}
	// The first matching row wins.
	o, err := parseArgs([]string{"--explain", "--confirm", "-0"})
	if err != nil {
		t.Fatal(err)
	}
	if err := checkConflicts(o); err == nil || err.Error() != want[[2]string{"explain", "confirm"}] {
		t.Errorf("--explain --confirm -0: err=%v", err)
	}
}

// TestFlagConflictMatrixCLI runs every pair of flags through run():
// conflicting pairs exit 2 with the exact message and no stdout, and the
// other pairs work.
func TestFlagConflictMatrixCLI(t *testing.T) {
	dir := fixture(t)
	flags := map[string][]string{"explain": {"--explain"}, "confirm": {"--confirm"}, "long": {"-l"}, "null": {"-0"},
		"help": {"--help"}, "version": {"--version"}, "dir": {"--dir", dir},
		"maxdepth": {"--max-depth", "1"}, "mindepth": {"--min-depth", "1"}}
	conflict := map[[2]string]string{
		{"explain", "confirm"}:  "--explain only shows the plan and --confirm asks before searching; use one of them",
		{"long", "null"}:        "-0/--null prints paths for programs and -l/--long adds size and time for people; use one of them",
		{"explain", "null"}:     "--explain never searches, so -0/--null has no paths to print; use one of them",
		{"null", "help"}:        "-h/--help prints text, not NUL-separated paths; use it without -0/--null",
		{"null", "version"}:     "--version prints text, not NUL-separated paths; use it without -0/--null",
		{"explain", "long"}:     "--explain never searches, so -l/--long has no files to list; use one of them",
		{"help", "version"}:     "-h/--help and --version both print text; use one of them",
		{"explain", "help"}:     "-h/--help prints text, not a query explanation; use it without --explain",
		{"confirm", "help"}:     "-h/--help prints text and never searches; use it without --confirm",
		{"long", "help"}:        "-h/--help prints text, not a file list; use it without -l/--long",
		{"help", "dir"}:         "-h/--help never searches, so -d/--dir has no effect; use it without -d/--dir",
		{"explain", "version"}:  "--version prints text, not a query explanation; use it without --explain",
		{"confirm", "version"}:  "--version prints text and never searches; use it without --confirm",
		{"long", "version"}:     "--version prints text, not a file list; use it without -l/--long",
		{"version", "dir"}:      "--version never searches, so -d/--dir has no effect; use it without -d/--dir",
		{"help", "maxdepth"}:    "-h/--help never searches, so --max-depth has no effect; use it without --max-depth",
		{"help", "mindepth"}:    "-h/--help never searches, so --min-depth has no effect; use it without --min-depth",
		{"version", "maxdepth"}: "--version never searches, so --max-depth has no effect; use it without --max-depth",
		{"version", "mindepth"}: "--version never searches, so --min-depth has no effect; use it without --min-depth",
	}
	names := []string{"explain", "confirm", "long", "null", "help", "version", "dir", "maxdepth", "mindepth"}
	pairs, total := 0, 0
	for i, a := range names {
		for _, b := range names[i+1:] {
			total++
			msg, bad := conflict[[2]string{a, b}]
			if !bad {
				msg, bad = conflict[[2]string{b, a}]
			}
			for _, order := range [][2]string{{a, b}, {b, a}} {
				args := append(append([]string{}, flags[order[0]]...), flags[order[1]]...)
				// A search needs a location; -d/--dir itself is one of the pair.
				if a != "dir" && b != "dir" && a != "help" && b != "help" && a != "version" && b != "version" {
					args = append(args, "--dir", dir)
				}
				args = append(args, "go files")
				out, errOut, code := nlfInput(t, "n\n", args...)
				switch {
				case bad:
					if code != 2 || out != "" || errOut != "nlf: "+msg+"\nRun 'nlf --help' for usage.\n" {
						t.Errorf("%v: code=%d out=%q err=%q, want %q", args, code, out, errOut, msg)
					}
				case code != 0 || strings.Contains(errOut, "use one of them") || strings.Contains(errOut, "use it without"):
					t.Errorf("%v: code=%d out=%q err=%q, want allowed", args, code, out, errOut)
				case a == "confirm" || b == "confirm":
					if out != "" || !strings.HasSuffix(errOut, "Cancelled.\n") {
						t.Errorf("%v: out=%q err=%q, want Cancelled.", args, out, errOut)
					}
				case out == "":
					t.Errorf("%v: no output", args)
				}
			}
			if bad {
				pairs++
			}
		}
	}
	if pairs != len(conflict) || pairs != 19 || total != 36 {
		t.Errorf("matched %d conflict pairs of %d, want %d (19) of 36", pairs, total, len(conflict))
	}
	// Words after --help or --version are allowed and not checked.
	for _, args := range [][]string{{"--help", "go", "files"}, {"--version", "banana"}, {"-h", "files without TODO"}} {
		out, errOut, code := nlf(t, args...)
		if code != 0 || out == "" || errOut != "" {
			t.Errorf("%v: code=%d out=%q err=%q", args, code, out, errOut)
		}
	}
}

// TestDirFlagValidation: an empty or repeated -d/--dir is an error, not a
// silent default or a silent override.
func TestDirFlagValidation(t *testing.T) {
	dir := fixture(t)
	for _, c := range []struct {
		args []string
		msg  string
	}{
		{[]string{"-d", "", "go files"}, "-d needs a directory"},
		{[]string{"--dir", "", "go files"}, "--dir needs a directory"},
		{[]string{"--dir=", "go files"}, "--dir needs a directory"},
		{[]string{"go files", "-d"}, "-d needs a directory"},
		{[]string{"go files", "--dir"}, "--dir needs a directory"},
		{[]string{"--dir", dir, "--dir", dir, "go files"}, "-d/--dir can be given only once"},
		{[]string{"--dir=" + dir, "-d", dir, "go files"}, "-d/--dir can be given only once"},
		{[]string{"-d", dir, "--dir=/tmp", "go files"}, "-d/--dir can be given only once"},
		{[]string{"-d", dir, "--dir", "", "go files"}, "--dir needs a directory"},
		{[]string{"--explain", "--dir", dir, "-d", dir, "go files"}, "-d/--dir can be given only once"},
	} {
		out, errOut, code := nlf(t, c.args...)
		if code != 2 || out != "" || errOut != "nlf: "+c.msg+"\nRun 'nlf --help' for usage.\n" {
			t.Errorf("%q: code=%d out=%q err=%q, want %q", c.args, code, out, errOut, c.msg)
		}
	}
	for _, args := range [][]string{{"--dir=" + dir, "go files"}, {"-d", dir, "go files"}, {"--dir", dir, "go files"}} {
		if out, errOut, code := nlf(t, args...); code != 0 || len(paths(dir, out)) != 3 {
			t.Errorf("%q: code=%d out=%q err=%q", args, code, out, errOut)
		}
	}
	// "--" ends the flags: a later --dir is a query word.
	if o, err := parseArgs([]string{"-d", dir, "--", "--dir", dir}); err != nil || o.dir != dir || len(o.words) != 2 {
		t.Errorf("-- --dir: opts=%+v err=%v", o, err)
	}
}

func TestNullOutput(t *testing.T) {
	dir := fixture(t)
	want := filepath.Join(dir, "main.go") + "\x00" + filepath.Join(dir, "util.go") + "\x00" + filepath.Join(dir, "vendor", "lib.go") + "\x00"
	for _, flag := range []string{"-0", "--null"} {
		out, errOut, code := nlf(t, flag, "--dir", dir, "go files")
		if code != 0 || out != want || errOut != "" {
			t.Errorf("%s: code=%d out=%q err=%q, want %q", flag, code, out, errOut, want)
		}
	}
	out, _, code := nlf(t, "-0", "--dir", dir, "最大的 2 個檔案")
	if w := filepath.Join(dir, "big.log") + "\x00" + filepath.Join(dir, "main.go") + "\x00"; code != 0 || out != w {
		t.Errorf("最大的 2 個檔案: code=%d out=%q want %q", code, out, w)
	}
	for _, q := range []string{"go files", "最大的 2 個檔案", "files"} {
		lines, _, _ := nlf(t, "--dir", dir, q)
		nul, _, _ := nlf(t, "-0", "--dir", dir, q)
		if strings.Contains(nul, "\n") || !strings.HasSuffix(nul, "\x00") || strings.ReplaceAll(nul, "\x00", "\n") != lines {
			t.Errorf("%s: -0 output %q does not match newline output %q", q, nul, lines)
		}
	}
}

func TestNullConflictsCLI(t *testing.T) {
	long := "-0/--null prints paths for programs and -l/--long adds size and time for people; use one of them"
	explain := "--explain never searches, so -0/--null has no paths to print; use one of them"
	help := "-h/--help prints text, not NUL-separated paths; use it without -0/--null"
	version := "--version prints text, not NUL-separated paths; use it without -0/--null"
	for _, c := range []struct {
		args []string
		msg  string
	}{
		{[]string{"-0", "-l", "go files"}, long},
		{[]string{"-l", "-0", "go files"}, long},
		{[]string{"--null", "--long", "go files"}, long},
		{[]string{"-0", "--explain", "go files"}, explain},
		{[]string{"--explain", "-0", "go files"}, explain},
		{[]string{"-0", "--help"}, help},
		{[]string{"-h", "-0"}, help},
		{[]string{"-0", "--version"}, version},
		{[]string{"--null", "--json", "go files"}, "unknown flag --json"},
	} {
		out, errOut, code := nlfInput(t, "y\n", c.args...)
		if code != 2 || out != "" || errOut != "nlf: "+c.msg+"\nRun 'nlf --help' for usage.\n" {
			t.Errorf("%v: code=%d out=%q err=%q, want %q", c.args, code, out, errOut, c.msg)
		}
	}
	dir := fixture(t)
	if out, errOut, code := nlfInput(t, "y\n", "-0", "--confirm", "--dir", dir, "go files"); code != 0 || out == "" || strings.Contains(errOut, "use one of them") {
		t.Errorf("-0 --confirm: code=%d out=%q err=%q", code, out, errOut)
	}
}

func TestNullKeepsStderrAndExit(t *testing.T) {
	dir := fixture(t)
	for _, c := range []struct {
		args []string
		code int
	}{
		{[]string{"--dir", dir, "pdf files"}, 1},
		{[]string{"find files banana"}, 2},
		{[]string{"--dir", filepath.Join(dir, "does-not-exist"), "go files"}, 2},
		{[]string{"--dir"}, 2},
		{[]string{"--dir", dir, "in ./src go files"}, 2},
		{[]string{}, 2},
		{[]string{"--dir", dir, "exclude ../elsewhere"}, 2},
	} {
		out, errOut, code := nlf(t, c.args...)
		nout, nerr, ncode := nlf(t, append([]string{"-0"}, c.args...)...)
		if code != c.code || ncode != c.code || out != "" || nout != "" || errOut == "" || nerr != errOut {
			t.Errorf("%v: code=%d/-0 %d (want %d) out=%q/%q\n err  %q\n -0   %q", c.args, code, ncode, c.code, out, nout, errOut, nerr)
		}
	}
	if out, errOut, code := nlf(t, "--", "-0"); code != 2 || out != "" || !strings.Contains(errOut, "cannot understand") {
		t.Errorf("-- -0: code=%d out=%q err=%q", code, out, errOut)
	}
}

func TestNullWithConfirm(t *testing.T) {
	dir := fixture(t)
	direct, _, _ := nlf(t, "-0", "--dir", dir, "go files")
	out, errOut, code := nlfInput(t, "y\n", "-0", "--confirm", "--dir", dir, "go files")
	if code != 0 || out != direct || !strings.Contains(errOut, "NLF execution plan") || !strings.Contains(errOut, "[y/N]") {
		t.Errorf("y: code=%d out=%q err=%q, want %q", code, out, errOut, direct)
	}
	for _, in := range []string{"n\n", ""} {
		out, errOut, code := nlfInput(t, in, "-0", "--confirm", "--dir", dir, "go files")
		if code != 0 || out != "" || !strings.HasSuffix(errOut, "Cancelled.\n") {
			t.Errorf("input %q: code=%d out=%q err=%q", in, code, out, errOut)
		}
	}
}

func TestDisplayPath(t *testing.T) {
	for in, want := range map[string]string{"-x": "./-x", "-foo/a": "./-foo/a", "--help": "./--help", "-": "./-",
		"a/-x": "a/-x", "./-x": "./-x", "/abs/-x": "/abs/-x", "": ""} {
		if got := displayPath(in); got != want {
			t.Errorf("displayPath(%q) = %q, want %q", in, got, want)
		}
	}
}

// TestHostileFilenamesNullXargs runs the real nlf main() in a subprocess
// against file names that break newline-separated output or look like
// options, and pipes -0 output into xargs -0.
func TestHostileFilenamesNullXargs(t *testing.T) {
	xargs, err := exec.LookPath("xargs")
	if err != nil {
		t.Fatalf("xargs not found: %v", err)
	}
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	names := []string{"a b.txt", "a\tb.txt", "a\nb.txt", "-rf.txt", "--help.txt", "-n", " lead.txt", "trail.txt ",
		`it's "q".txt`, `back\slash.txt`, "中文 檔名.txt", "-sub/a.txt"}
	content := map[string]string{}
	var files, created []string
	add := func(name string) error {
		data := string(rune('A' + len(files)))
		if err := os.MkdirAll(filepath.Dir(filepath.Join(dir, name)), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(dir, name), []byte(data), 0o644); err != nil {
			return err
		}
		content[displayPath(name)] = data
		files = append(files, displayPath(name))
		created = append(created, name)
		return nil
	}
	for _, n := range names {
		if err := add(n); err != nil {
			t.Fatal(err)
		}
	}
	bad := "bad\xffname.txt"
	switch err := add(bad); {
	case err == nil:
	case runtime.GOOS == "darwin" && errors.Is(err, syscall.EILSEQ):
		t.Logf("file system rejects invalid UTF-8 names (%v); testing without %q", err, bad)
	default:
		t.Fatalf("create %q: %v", bad, err)
	}

	nlfRun := func(args ...string) []byte {
		t.Helper()
		cmd := exec.Command(self, args...)
		cmd.Dir = dir
		cmd.Env = append(os.Environ(), "NLF_TEST_RUN_MAIN=1")
		var errb bytes.Buffer
		cmd.Stderr = &errb
		out, err := cmd.Output()
		if err != nil {
			t.Fatalf("nlf %q: %v\n%s", args, err, errb.String())
		}
		return out
	}
	out := nlfRun("-0", "files")
	got := strings.Split(string(out), "\x00")
	if got[len(got)-1] != "" {
		t.Fatalf("-0 output does not end with NUL: %q", out)
	}
	got = got[:len(got)-1]
	sortedGot, sortedWant := slices.Sorted(slices.Values(got)), slices.Sorted(slices.Values(files))
	if !slices.Equal(sortedGot, sortedWant) {
		t.Errorf("-0 files:\n got  %q\n want %q", sortedGot, sortedWant)
	}
	// The default order is the walk order: byte order of the original names
	// (no name here shares a prefix with -sub/), before ./ is added.
	var wantOrder []string
	for _, n := range slices.Sorted(slices.Values(created)) {
		wantOrder = append(wantOrder, displayPath(n))
	}
	if !slices.Equal(got, wantOrder) {
		t.Errorf("-0 files order:\n got  %q\n want %q", got, wantOrder)
	}

	xrun := func(args ...string) []byte {
		t.Helper()
		cmd := exec.Command(xargs, args...)
		cmd.Dir = dir
		cmd.Stdin = bytes.NewReader(out)
		var errb bytes.Buffer
		cmd.Stderr = &errb
		res, err := cmd.Output()
		if err != nil {
			t.Fatalf("xargs %q: %v\n%s", args, err, errb.String())
		}
		return res
	}
	if res := xrun("-0", "printf", "%s\\0"); !bytes.Equal(res, out) {
		t.Errorf("xargs -0 printf:\n got  %q\n want %q", res, out)
	}
	var wantCat strings.Builder
	for _, p := range got {
		wantCat.WriteString(content[p])
	}
	if res := xrun("-0", "cat"); string(res) != wantCat.String() {
		t.Errorf("xargs -0 cat = %q, want %q", res, wantCat.String())
	}

	for _, d := range []string{"-sub", "./-sub"} {
		if res := nlfRun("--dir", d, "-0", "files"); string(res) != "./-sub/a.txt\x00" {
			t.Errorf("--dir %s -0 files = %q", d, res)
		}
	}
	lines := strings.Split(string(nlfRun("files")), "\n")
	if !slices.Contains(lines, "./-rf.txt") || slices.Contains(lines, "-rf.txt") {
		t.Errorf("newline output should show ./-rf.txt: %q", lines)
	}
	long := strings.Split(string(nlfRun("-l", "files")), "\n")
	if !slices.ContainsFunc(long, func(l string) bool { return strings.HasSuffix(l, "  ./-rf.txt") }) ||
		slices.ContainsFunc(long, func(l string) bool { return strings.HasSuffix(l, "  -rf.txt") }) {
		t.Errorf("-l output should show ./-rf.txt: %q", long)
	}
}
