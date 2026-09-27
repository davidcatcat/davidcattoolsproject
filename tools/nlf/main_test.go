package main

import (
	"bytes"
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
	for _, want := range []string{"解析結果", ".go", "> 1 MB", "vendor", "5",
		"find /definitely/missing -type d \\( -iname vendor \\) -prune -o -type f -iname '*.go' -size +1048576c -newermt '2026-09-24 12:00:00' -print | head -n 5"} {
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
	want := "find " + link + "/ -type d \\( -path " + link + "/src/gen -o -iname vendor \\) -prune -o -type f -iname '*.txt' -print"
	if code != 0 || !strings.Contains(out, want) || !strings.Contains(out, "Approximate") {
		t.Errorf("code=%d\n got %s\nwant %s", code, out, want)
	}
}

// TestExplainParity runs the printed find command (read-only) and checks it
// finds the same files as nlf for the cases --explain is meant to cover.
func TestExplainParity(t *testing.T) {
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		t.Skip("--explain targets BSD tools on macOS and GNU tools on Linux")
	}
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"real/a.txt": "TODO", "real/big.txt": strings.Repeat("x", 2048),
		"real/vendor/v.txt": "TODO", "real/d/vendor": "TODO", "real/src/gen/g.txt": "", "real/src/keep/k.txt": "",
		"real/sp ace/s.txt": "TODO", "real/it's.txt": ""})
	link := filepath.Join(dir, "link")
	if err := os.Symlink(filepath.Join(dir, "real"), link); err != nil {
		t.Fatal(err)
	}
	set := func(s string) string {
		var ps []string
		for _, l := range strings.Split(strings.TrimSpace(s), "\n") {
			if l != "" {
				ps = append(ps, strings.TrimPrefix(strings.TrimPrefix(l, link), "/"))
			}
		}
		slices.Sort(ps)
		return strings.Join(ps, ",")
	}
	for _, q := range []string{"txt files", "exclude vendor", "exclude ./src/gen", "exclude src/gen/",
		"larger than 1KB", "at most 1KB", `containing "TODO"`, "largest 1"} { // largest ties are ordered differently by design
		out, _, _ := nlf(t, "--dir", link, q)
		ex, _, _ := nlf(t, "--explain", "--dir", link, q)
		lines := strings.Split(strings.TrimSpace(ex), "\n")
		cmd := strings.TrimSpace(lines[len(lines)-1])
		findOut, err := exec.Command("sh", "-c", cmd).Output()
		if err != nil {
			t.Fatalf("%s: %s: %v", q, cmd, err)
		}
		if a, b := set(out), set(string(findOut)); a != b {
			t.Errorf("%s:\n cmd  %s\n nlf  %s\n find %s", q, cmd, a, b)
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
	re := regexp.MustCompile(`nlf((?: (?:--explain|--confirm|-l|--long|(?:-d|--dir) [^\s"']+))*) (?:"((?:[^"\\]|\\.)*)"|'([^']*)')`)
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
		for _, flag := range []string{"--explain", "--confirm", "--long", "--dir", "--help", "--version"} {
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
			args = slices.DeleteFunc(args, func(a string) bool { return a == "--explain" || a == "--confirm" })
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
		"Flags:", "--explain", "--confirm", "Safety:", "Supported conditions", "Search root", "Exit status:", "Tips:",
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
		"find " + dir + " -type d \\( -iname vendor \\) -prune -o -type f -iname '*.go' -size +1024c -print | head -n 3",
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
// Linux, gstat from coreutils on macOS) and checks it orders like nlf.
func TestGNUSortPipeline(t *testing.T) {
	stat := "stat"
	if runtime.GOOS != "linux" {
		if _, err := exec.LookPath("gstat"); err != nil {
			t.Skip("GNU stat not available (install coreutils for gstat)")
		}
		stat = "gstat"
	}
	dir := t.TempDir()
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i, n := range []string{"a.txt", "b b.txt", "c.txt", "d.txt"} {
		p := filepath.Join(dir, n)
		mkfiles(t, dir, map[string]string{n: strings.Repeat("x", 100*(i%3+1)+i)})
		mt := base.Add(time.Duration(i) * time.Hour)
		if err := os.Chtimes(p, mt, mt); err != nil {
			t.Fatal(err)
		}
	}
	for _, in := range []string{"largest 3", "newest 3"} {
		q, err := query.Parse(in, testNow)
		if err != nil {
			t.Fatal(err)
		}
		q.Root = dir
		cmd := query.FindCommand(q, query.Linux)
		if !strings.Contains(cmd, "stat -c") {
			t.Fatalf("not a GNU command: %s", cmd)
		}
		cmd = strings.Replace(cmd, "-exec stat ", "-exec "+stat+" ", 1)
		got, err := exec.Command("sh", "-c", cmd).Output()
		if err != nil {
			t.Fatalf("%s: %v", cmd, err)
		}
		want, _, _ := nlf(t, "--dir", dir, in)
		if string(got) != want {
			t.Errorf("%s:\n cmd  %s\n find %q\n nlf  %q", in, cmd, got, want)
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
