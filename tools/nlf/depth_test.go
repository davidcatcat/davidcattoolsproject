package main

import (
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

func TestDepthFlagParsing(t *testing.T) {
	for _, c := range []struct {
		args               []string
		minDepth, maxDepth int
		minSet, maxSet     bool
	}{
		{[]string{"--max-depth", "0", "go files"}, 0, 0, false, true},
		{[]string{"--max-depth=2", "go files"}, 0, 2, false, true},
		{[]string{"--min-depth", "007", "go files"}, 7, 0, true, false},
		{[]string{"--max-depth", "4096", "go files"}, 0, 4096, false, true},
		{[]string{"go files", "--min-depth=1", "--max-depth", "3"}, 1, 3, true, true},
		{[]string{"--min-depth", "0", "go files"}, 0, 0, true, false},
	} {
		o, err := parseArgs(c.args)
		if err != nil || o.minDepth != c.minDepth || o.maxDepth != c.maxDepth || o.minDepthSet != c.minSet ||
			o.maxDepthSet != c.maxSet || strings.Join(o.words, " ") != "go files" {
			t.Errorf("%q: opts=%+v err=%v", c.args, o, err)
		}
	}
	// "--" ends the flags: a later --max-depth is a query word.
	o, err := parseArgs([]string{"-d", "x", "--", "--max-depth", "1"})
	if err != nil || o.maxDepthSet || !slices.Equal(o.words, []string{"--max-depth", "1"}) {
		t.Errorf("-- --max-depth 1: opts=%+v err=%v", o, err)
	}
}

func TestDepthFlagValidation(t *testing.T) {
	dir := fixture(t)
	type tc struct {
		args []string
		msg  string
	}
	var cases []tc
	for _, name := range []string{"--max-depth", "--min-depth"} {
		cases = append(cases,
			tc{[]string{"go files", name}, name + " needs a number"},
			tc{[]string{name, "", "go files"}, name + " needs a number"},
			tc{[]string{name + "=", "go files"}, name + " needs a number"},
			tc{[]string{name, "1", name, "1", "go files"}, name + " can be given only once"},
			tc{[]string{name + "=1", name, "2", "go files"}, name + " can be given only once"},
		)
		for _, v := range []string{"-1", "1.5", "abc", "+1", " 1", "1 ", "0x1", "1e3", "１", "4097",
			"12345678901234567890", "--", "--explain"} {
			cases = append(cases, tc{[]string{name, v, "--dir", dir, "go files"},
				name + ` needs a whole number from 0 to 4096, got "` + v + `"`})
			cases = append(cases, tc{[]string{name + "=" + v, "--dir", dir, "go files"},
				name + ` needs a whole number from 0 to 4096, got "` + v + `"`})
		}
	}
	minMax := "--min-depth 3 is greater than --max-depth 1, so no file can match"
	cases = append(cases,
		tc{[]string{"--min-depth", "3", "--max-depth", "1", "--dir", dir, "go files"}, minMax},
		tc{[]string{"--max-depth=1", "--min-depth=03"}, minMax},
		tc{[]string{"--min-depth", "3", "--max-depth", "1", "--explain", "--dir", dir, "go files"}, minMax},
		tc{[]string{"--min-depth", "2", "--max-depth", "0", "go files"}, "--min-depth 2 is greater than --max-depth 0, so no file can match"},
	)
	for _, c := range cases {
		out, errOut, code := nlf(t, c.args...)
		if code != 2 || out != "" || errOut != "nlf: "+c.msg+"\nRun 'nlf --help' for usage.\n" {
			t.Errorf("%q: code=%d out=%q err=%q, want %q", c.args, code, out, errOut, c.msg)
		}
	}
	// With --confirm the error comes before the plan and the prompt.
	out, errOut, code := nlfInput(t, "y\n", "--confirm", "--min-depth", "3", "--max-depth", "1", "--dir", dir, "go files")
	if code != 2 || out != "" || errOut != "nlf: "+minMax+"\nRun 'nlf --help' for usage.\n" {
		t.Errorf("--confirm min > max: code=%d out=%q err=%q", code, out, errOut)
	}
	// Valid values are not rejected.
	for _, c := range []struct {
		args []string
		code int
	}{
		{[]string{"--min-depth", "2", "--max-depth", "2", "--dir", dir, "go files"}, 0},
		{[]string{"--min-depth", "07", "--max-depth", "7", "--dir", dir, "go files"}, 1},
		{[]string{"--max-depth=4096", "--dir", dir, "go files"}, 0},
		{[]string{"--min-depth", "0", "--max-depth", "0", "--dir", dir, "go files"}, 1},
	} {
		out, errOut, code := nlf(t, c.args...)
		if code != c.code || strings.Contains(errOut, "depth") || (code == 0) == (out == "") {
			t.Errorf("%q: code=%d out=%q err=%q, want exit %d", c.args, code, out, errOut, c.code)
		}
	}
}

func TestDepthEndToEnd(t *testing.T) {
	dir := fixture(t)
	cases := []struct {
		args []string
		want []string
	}{
		{[]string{"--max-depth", "1", "--dir", dir, "go files"}, []string{"main.go", "util.go"}},
		{[]string{"--min-depth", "2", "--dir", dir, "go files"}, []string{"vendor/lib.go"}},
		{[]string{"--min-depth=1", "--max-depth=2", "--dir", dir, "go files"}, []string{"main.go", "util.go", "vendor/lib.go"}},
		{[]string{"--min-depth", "2", "--dir", dir, "files excluding vendor"}, []string{"docs/readme.md"}},
		{[]string{"--max-depth", "1", "--dir", dir, "最大的 2 個檔案"}, []string{"big.log", "main.go"}},
	}
	if runtime.GOOS != "windows" { // a Windows path cannot be written inside the query
		cases = append(cases, struct {
			args []string
			want []string
		}{[]string{"--max-depth", "1", "go files in " + dir}, []string{"main.go", "util.go"}})
	}
	for _, c := range cases {
		out, errOut, code := nlf(t, c.args...)
		if got := paths(dir, out); code != 0 || !slices.Equal(got, c.want) || errOut != "" {
			t.Errorf("%q: code=%d got %v err=%q, want %v", c.args, code, got, errOut, c.want)
		}
	}
	noMatch := "nlf: no matching files. Add --explain to check how the query was understood.\n"
	if out, errOut, code := nlf(t, "--max-depth", "0", "--dir", dir, "go files"); code != 1 || out != "" || errOut != noMatch {
		t.Errorf("--max-depth 0 on a directory: code=%d out=%q err=%q", code, out, errOut)
	}
	// A file as the search location is depth 0.
	file := filepath.Join(dir, "main.go")
	if out, errOut, code := nlf(t, "--dir", file, "--max-depth", "0", "go files"); code != 0 || out != file+"\n" || errOut != "" {
		t.Errorf("file root --max-depth 0: code=%d out=%q err=%q", code, out, errOut)
	}
	if out, errOut, code := nlf(t, "--dir", file, "--max-depth", "0", "--min-depth", "1", "go files"); code != 2 || out != "" ||
		!strings.Contains(errOut, "--min-depth 1 is greater than --max-depth 0") {
		t.Errorf("file root min 1 max 0: code=%d out=%q err=%q", code, out, errOut)
	}
	if out, errOut, code := nlf(t, "--dir", file, "--min-depth", "1", "go files"); code != 1 || out != "" || errOut != noMatch {
		t.Errorf("file root --min-depth 1: code=%d out=%q err=%q", code, out, errOut)
	}
	// With -l, -0 and --confirm.
	out, _, code := nlf(t, "-l", "--max-depth", "1", "--dir", dir, "go files")
	if lines := strings.Split(strings.TrimSpace(out), "\n"); code != 0 || len(lines) != 2 ||
		!strings.HasSuffix(lines[0], "  "+filepath.Join(dir, "main.go")) || !strings.HasSuffix(lines[1], "  "+filepath.Join(dir, "util.go")) {
		t.Errorf("-l --max-depth 1: code=%d out=%q", code, out)
	}
	want0 := filepath.Join(dir, "vendor", "lib.go") + "\x00"
	if out, _, code := nlf(t, "-0", "--min-depth", "2", "--dir", dir, "go files"); code != 0 || out != want0 {
		t.Errorf("-0 --min-depth 2: code=%d out=%q, want %q", code, out, want0)
	}
	direct, _, _ := nlf(t, "--max-depth", "1", "--dir", dir, "go files")
	out, errOut, code := nlfInput(t, "y\n", "--confirm", "--max-depth", "1", "--dir", dir, "go files")
	if code != 0 || direct == "" || out != direct {
		t.Errorf("--confirm y --max-depth 1: code=%d out=%q (direct %q) err=%q", code, out, direct, errOut)
	}
	checkCommand(t, "--confirm y --max-depth 1", errOut, query.English, "-maxdepth 1")
	// --explain shows the depth and never searches.
	out, errOut, code = nlf(t, "--explain", "--min-depth", "2", "--dir", filepath.Join(dir, "missing"), "go files excluding vendor")
	if code != 0 || errOut != "" || !strings.Contains(out, "depth:") || !strings.Contains(out, "at least 2 levels") ||
		strings.Contains(out, "-mindepth") {
		t.Errorf("--explain --min-depth 2: code=%d out=%q err=%q", code, out, errOut)
	}
	checkCommand(t, "--explain --min-depth 2", out, query.English, "-type f -path '"+filepath.Join(dir, "missing")+"/*/*'")
}

// TestDepthPruningCLI: what lies below the maximum depth, or files above the
// minimum depth, are never read, so unreadable ones produce no warnings.
func TestDepthPruningCLI(t *testing.T) {
	root := os.Geteuid() == 0
	if root {
		t.Log("running as root: permission errors cannot be provoked; checking only that nothing is reported")
	}
	dir := fixture(t)
	docs := filepath.Join(dir, "docs")
	denyAccess(t, docs)
	q := `files containing "TODO"`
	out, errOut, code := nlf(t, "--max-depth", "1", "--dir", dir, q)
	if got := paths(dir, out); code != 0 || !slices.Equal(got, []string{"main.go"}) || errOut != "" {
		t.Errorf("--max-depth 1: code=%d got %v err=%q, want [main.go] and empty stderr", code, got, errOut)
	}
	if _, errOut, _ := nlf(t, "--dir", dir, q); !root && !strings.Contains(errOut, docs) {
		t.Errorf("control without --max-depth: stderr does not report %s:\n%s", docs, errOut)
	}

	// An unreadable search location with --max-depth 0 is never opened.
	if out, errOut, code := nlf(t, "--max-depth", "0", "--dir", docs, "md files"); code != 1 || out != "" ||
		errOut != "nlf: no matching files. Add --explain to check how the query was understood.\n" {
		t.Errorf("unreadable root --max-depth 0: code=%d out=%q err=%q", code, out, errOut)
	}
	if _, errOut, code := nlf(t, "--dir", docs, "md files"); !root && (code != 2 || !strings.Contains(errOut, "permission denied")) {
		t.Errorf("control without --max-depth: code=%d err=%q", code, errOut)
	}

	// An unreadable file above the minimum depth is never opened.
	allowAccess(t, docs)
	mainGo := filepath.Join(dir, "main.go")
	denyAccess(t, mainGo)
	out, errOut, code = nlf(t, "--min-depth", "2", "--dir", dir, q)
	if got := paths(dir, out); code != 0 || !slices.Equal(got, []string{"docs/readme.md", "vendor/lib.go"}) || errOut != "" {
		t.Errorf("--min-depth 2: code=%d got %v err=%q, want [docs/readme.md vendor/lib.go] and empty stderr", code, got, errOut)
	}
	if _, errOut, _ := nlf(t, "--dir", dir, q); !root && !strings.Contains(errOut, mainGo) {
		t.Errorf("control without --min-depth: stderr does not report %s:\n%s", mainGo, errOut)
	}
}
