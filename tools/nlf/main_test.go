package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
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

func nlf(t *testing.T, args ...string) (stdout, stderr string, code int) {
	t.Helper()
	var out, errb bytes.Buffer
	code = run(args, &out, &errb, testNow)
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
	_, errOut, code = nlf(t, "find banana files")
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
		"find /definitely/missing \\( -iname vendor \\) -prune -o -type f -iname '*.go' -size +1048576c -newermt '2026-09-24 12:00:00' -print | head -n 5"} {
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
	for _, want := range []string{"有 1 個位置因權限不足而略過", "不會自動使用 sudo", locked, "完整磁碟取用權限"} {
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
