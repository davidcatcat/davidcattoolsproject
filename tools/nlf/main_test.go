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
	if code != 2 || !strings.Contains(errOut, "不支援「或」條件") {
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
	if out, _, code := nlf(t, "--version"); code != 0 || out != "nlf 0.1.0\n" {
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
