package main

import (
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

// A7 hostile-input tests for the CLI: file names that break naive output
// handling, NFC/NFD spellings, and queries that are not valid UTF-8.

// subNLF runs the real nlf main() from the test binary in dir and returns
// stdout, stderr and the exit code.
func subNLF(t *testing.T, dir string, args ...string) ([]byte, string, int) {
	t.Helper()
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(self, args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "NLF_TEST_RUN_MAIN=1")
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err = cmd.Run()
	code := 0
	var ee *exec.ExitError
	switch {
	case errors.As(err, &ee):
		code = ee.ExitCode()
	case err != nil:
		t.Fatalf("nlf %q: %v", args, err)
	}
	return out.Bytes(), errb.String(), code
}

// walkOrder sorts slash-separated relative paths the way filepath.WalkDir
// visits them: directory entries in byte order, a directory's contents
// right after it.
func walkOrder(ps []string) []string {
	out := slices.Clone(ps)
	slices.SortFunc(out, func(a, b string) int {
		return slices.Compare(strings.Split(a, "/"), strings.Split(b, "/"))
	})
	return out
}

func nulJoin(ps []string) []byte {
	var b bytes.Buffer
	for _, p := range ps {
		b.WriteString(displayPath(p) + "\x00")
	}
	return b.Bytes()
}

func depthOfRel(p string) int { return strings.Count(p, "/") + 1 }

// TestHostileFilenameMatrix creates file names with spaces, control
// characters, shell metacharacters, option-like prefixes, quotes, Unicode
// and maximum length, and checks every output mode byte for byte.
func TestHostileFilenameMatrix(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Log("running as root; the matrix does not depend on permissions")
	}
	dir := t.TempDir()
	names := []string{"normal.txt", "a b.txt", " lead.txt", "trail.txt ", "a\tb.txt", "a\nb.txt", "a\rb.txt", "-rf.txt",
		"--help.txt", "-0", "it's.txt", `q"q.txt`, `back\slash.txt`, "*.txt", "?.txt", "[abc].txt", "semi;.txt", "pipe|.txt",
		"$x.txt", "bq`.txt", "中文 檔名.txt", "😀.txt", "caf\u00e9.txt",
		strings.Repeat("a", 251) + ".txt", // 255 bytes
		strings.Repeat("中", 85),           // 255 bytes
		"目錄 dir/a b.txt", "目錄 dir/-rf.txt", "目錄 dir/子 sub/a b.txt", "目錄 dir/子 sub/中文 檔名.txt"}
	mtime := time.Date(2026, 9, 27, 10, 0, 0, 0, time.Local)
	var files []string
	add := func(name string) error {
		p := filepath.Join(dir, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			return err
		}
		// Distinct one-byte contents: every size is equal (a tie), and a
		// mix-up of two files would show in the content.
		if err := os.WriteFile(p, []byte{byte('!' + len(files))}, 0o644); err != nil {
			return err
		}
		if err := os.Chtimes(p, mtime, mtime); err != nil {
			return err
		}
		files = append(files, name)
		return nil
	}
	for _, n := range names {
		if len(filepath.Base(n)) > 255 {
			t.Fatalf("test name longer than 255 bytes: %q", n)
		}
		if err := add(n); err != nil {
			t.Fatalf("create %q: %v", n, err)
		}
	}
	bad := "bad\xff.txt"
	switch err := add(bad); runtime.GOOS {
	case "darwin":
		// APFS rejects names that are not valid UTF-8.
		if !errors.Is(err, syscall.EILSEQ) {
			t.Fatalf("darwin: create %q = %v, want EILSEQ", bad, err)
		}
	default:
		if err != nil {
			t.Fatalf("%s: create %q: %v", runtime.GOOS, bad, err)
		}
	}
	walk := walkOrder(files)

	// (1) -0: exact bytes in walk order, and xargs -0 passes them through.
	out, errOut, code := subNLF(t, dir, "-0", "files")
	if code != 0 || !bytes.Equal(out, nulJoin(walk)) {
		t.Fatalf("-0 files: exit %d\n got  %q\n want %q\n%s", code, out, nulJoin(walk), errOut)
	}
	xargs, err := exec.LookPath("xargs")
	if err != nil {
		t.Fatalf("xargs not found: %v", err)
	}
	cmd := exec.Command(xargs, "-0", "printf", `%s\0`)
	cmd.Dir, cmd.Stdin = dir, bytes.NewReader(out)
	if echoed, err := cmd.Output(); err != nil || !bytes.Equal(echoed, out) {
		t.Errorf("xargs -0 printf: %v\n got  %q\n want %q", err, echoed, out)
	}

	// (2) Default output: one path per line. L1 (documented limitation): a
	// name containing a newline ("a\nb.txt") spans two lines here; -0 is
	// the unambiguous format. The bytes are pinned anyway.
	var lines strings.Builder
	for _, p := range walk {
		lines.WriteString(displayPath(p) + "\n")
	}
	if out, errOut, code := subNLF(t, dir, "files"); code != 0 || string(out) != lines.String() {
		t.Errorf("files: exit %d\n got  %q\n want %q\n%s", code, out, lines.String(), errOut)
	}

	// (3) -l: size, time and the display path, exactly.
	var long strings.Builder
	for _, p := range walk {
		fmt.Fprintf(&long, "%10s  %s  %s\n", query.HumanBytes(1), query.FormatTime(mtime), displayPath(p))
	}
	if out, errOut, code := subNLF(t, dir, "-l", "files"); code != 0 || string(out) != long.String() {
		t.Errorf("-l files: exit %d\n got  %q\n want %q\n%s", code, out, long.String(), errOut)
	}

	// (4) Equal sizes and equal mtimes: the ties go to the smallest paths.
	byPath := slices.Sorted(slices.Values(files))
	for _, q := range []string{"5 largest files", "5 newest files", "最大的 5 個檔案", "最新的 5 個檔案"} {
		if out, errOut, code := subNLF(t, dir, "-0", q); code != 0 || !bytes.Equal(out, nulJoin(byPath[:5])) {
			t.Errorf("%s: exit %d\n got  %q\n want %q\n%s", q, code, out, nulJoin(byPath[:5]), errOut)
		}
	}

	// (5) Depth limits give exact sets.
	for _, c := range []struct {
		args []string
		keep func(depth int) bool
	}{
		{[]string{"--max-depth", "1"}, func(d int) bool { return d <= 1 }},
		{[]string{"--max-depth", "2"}, func(d int) bool { return d <= 2 }},
		{[]string{"--min-depth", "2"}, func(d int) bool { return d >= 2 }},
		{[]string{"--min-depth", "3", "--max-depth", "3"}, func(d int) bool { return d == 3 }},
	} {
		want := slices.DeleteFunc(slices.Clone(walk), func(p string) bool { return !c.keep(depthOfRel(p)) })
		args := append(slices.Clone(c.args), "-0", "files")
		if out, errOut, code := subNLF(t, dir, args...); code != 0 || !bytes.Equal(out, nulJoin(want)) {
			t.Errorf("%q: exit %d\n got  %q\n want %q\n%s", args, code, out, nulJoin(want), errOut)
		}
	}

	// (6) A path exclude with spaces and Chinese prunes exactly that directory.
	want := slices.DeleteFunc(slices.Clone(walk), func(p string) bool { return strings.HasPrefix(p, "目錄 dir/子 sub/") })
	if out, errOut, code := subNLF(t, dir, "-0", `排除 "./目錄 dir/子 sub"，找 txt 檔`); code != 0 ||
		!bytes.Equal(out, nulJoin(slices.DeleteFunc(want, func(p string) bool { return !strings.HasSuffix(p, ".txt") }))) {
		t.Errorf("path exclude: exit %d, got %q\n%s", code, out, errOut)
	}

	// (6) Directory names that look like options, globs or quotes are
	// excluded literally: only that directory is pruned; the siblings a, b
	// and c (which [abc] and * would match as globs) are kept.
	exDir := t.TempDir()
	hostileDirs := []string{"-rf", "a b", "it's", "[abc]", "*", "中文 目錄", "a\nb"}
	var exFiles []string
	for _, d := range append(slices.Clone(hostileDirs), "a", "b", "c") {
		p := filepath.Join(exDir, d, "f.txt")
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(d), 0o644); err != nil {
			t.Fatal(err)
		}
		exFiles = append(exFiles, d+"/f.txt")
	}
	exWalk := walkOrder(exFiles)
	for _, d := range hostileDirs {
		q := `排除 "` + d + `" 目錄，找 txt 檔`
		want := slices.DeleteFunc(slices.Clone(exWalk), func(p string) bool { return p == d+"/f.txt" })
		if out, errOut, code := subNLF(t, exDir, "-0", q); code != 0 || !bytes.Equal(out, nulJoin(want)) {
			t.Errorf("%q: exit %d\n got  %q\n want %q\n%s", q, code, out, nulJoin(want), errOut)
		}
	}
}

// isMissingRootError reports whether stderr starts with the missing search
// location error for root. The message follows the query's language, so a
// test must accept both instead of assuming English.
func isMissingRootError(stderr, root string) bool {
	return strings.HasPrefix(stderr, "nlf：找不到搜尋位置「"+root+"」\n") ||
		strings.HasPrefix(stderr, fmt.Sprintf("nlf: search location %q does not exist\n", root))
}

// TestMissingDirFollowsQueryLanguage pins the missing --dir error in both
// languages on every platform, so a cross-platform test cannot again accept
// only the English message.
func TestMissingDirFollowsQueryLanguage(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "missing")
	for _, c := range []struct{ q, want string }{
		{"找 txt 檔", "nlf：找不到搜尋位置「" + missing + "」\n"},
		{"find txt files", fmt.Sprintf("nlf: search location %q does not exist\n", missing)},
	} {
		out, errOut, code := nlf(t, "--dir", missing, c.q)
		if code != 2 || out != "" || errOut != c.want || !isMissingRootError(errOut, missing) {
			t.Errorf("%q: exit %d, stdout %q, stderr %q; want exit 2, empty stdout, stderr %q", c.q, code, out, errOut, c.want)
		}
	}
	for _, s := range []string{"", "nlf: permission denied reading \"" + missing + "\"\n", "nlf：找不到搜尋位置「" + missing + "/x」\n"} {
		if isMissingRootError(s, missing) {
			t.Errorf("isMissingRootError(%q) = true; want false", s)
		}
	}
}

// TestUnicodeNormalizationMatrix pins byte semantics for NFC and NFD
// spellings (L2): nlf never normalizes. Only what the file system itself
// does differs by platform, and both sides are asserted.
func TestUnicodeNormalizationMatrix(t *testing.T) {
	nfc, nfd := "caf\u00e9", "cafe\u0301"
	forms := map[string]string{"nfc": nfc, "nfd": nfd}

	// Probe: does the file system treat the two spellings as one name?
	probe := t.TempDir()
	if err := os.WriteFile(filepath.Join(probe, nfc+".txt"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := os.Lstat(filepath.Join(probe, nfd+".txt"))
	switch runtime.GOOS {
	case "darwin":
		if err != nil {
			t.Fatalf("darwin: an NFD lookup of an NFC name failed (%v); expected APFS normalization-insensitive lookup", err)
		}
	default:
		if !errors.Is(err, fs.ErrNotExist) {
			t.Fatalf("%s: an NFD lookup of an NFC name = %v; expected distinct names", runtime.GOOS, err)
		}
	}

	root := t.TempDir()
	for k, f := range forms {
		mkfiles(t, root, map[string]string{k + "/" + f + ".txt": "say " + f, k + "/d" + f + "/x.txt": "x"})
	}
	all := []string{"nfc/d" + nfc + "/x.txt", "nfc/" + nfc + ".txt", "nfd/d" + nfd + "/x.txt", "nfd/" + nfd + ".txt"}
	sortedRel := func(stdout string) []string {
		var out []string
		for _, p := range strings.Split(strings.TrimSuffix(stdout, "\x00"), "\x00") {
			if p != "" {
				rel, _ := filepath.Rel(root, p)
				out = append(out, filepath.ToSlash(rel))
			}
		}
		slices.Sort(out)
		return out
	}
	// Same table on both platforms: filename and content matching compare bytes.
	for dk, df := range forms {
		for qk, qf := range forms {
			for _, q := range []string{`檔名包含 "` + qf + `"`, `包含 "` + qf + `"`} {
				out, errOut, code := nlf(t, "--dir", filepath.Join(root, dk), q)
				switch {
				case dk == qk:
					if want := filepath.Join(root, dk, df+".txt") + "\n"; code != 0 || out != want {
						t.Errorf("%s in %s: exit %d, got %q, want %q\n%s", qk, dk, code, out, want, errOut)
					}
				case code != 1 || out != "":
					t.Errorf("%s in %s: exit %d, got %q; the other spelling must not match\n%s", qk, dk, code, out, errOut)
				}
			}
		}
	}
	for _, c := range []struct {
		q    string
		want []string
	}{
		{`排除 "d` + nfc + `" 目錄`, []string{all[1], all[2], all[3]}},
		{`排除 "d` + nfd + `" 目錄`, []string{all[0], all[1], all[3]}},
		{`排除 "./nfc/d` + nfc + `"`, []string{all[1], all[2], all[3]}},
		{`排除 "./nfd/d` + nfd + `"`, []string{all[0], all[1], all[3]}},
		{`排除 "./nfc/d` + nfd + `"`, all}, // the other spelling prunes nothing, even where it resolves
		{`排除 "./nfd/d` + nfc + `"`, all},
		{"找 txt 檔", all},
	} {
		out, errOut, code := nlf(t, "-0", "--dir", root, c.q)
		want := slices.Sorted(slices.Values(c.want))
		if got := sortedRel(out); code != 0 || !slices.Equal(got, want) {
			t.Errorf("%q: exit %d, got %q, want %q\n%s", c.q, code, got, want, errOut)
		}
	}
	for _, f := range forms {
		for _, q := range []string{"副檔名 " + f, "*." + f, f + " 檔"} {
			if out, _, code := nlf(t, "--dir", root, q); code != 2 || out != "" {
				t.Errorf("%q: exit %d, stdout %q; a non-ASCII extension must be rejected", q, code, out)
			}
		}
	}

	// --dir in the other spelling, and both spellings in one directory:
	// here the platforms differ, because the file system does.
	mkfiles(t, root, map[string]string{"dir" + nfc + "/f.txt": "f"})
	other := filepath.Join(root, "dir"+nfd)
	out, errOut, code := nlf(t, "--dir", other, "找 txt 檔")
	co := filepath.Join(root, "co")
	mkfiles(t, co, map[string]string{nfc + ".txt": "1"})
	mkfiles(t, co, map[string]string{nfd + ".txt": "2"})
	entries, err := os.ReadDir(co)
	if err != nil {
		t.Fatal(err)
	}
	coOut, coErr, coCode := nlf(t, "--dir", co, "檔名包含 caf")
	switch runtime.GOOS {
	case "darwin":
		if want := filepath.Join(other, "f.txt") + "\n"; code != 0 || out != want {
			t.Errorf("darwin: --dir in NFD: exit %d, got %q, want %q (the given spelling)\n%s", code, out, want, errOut)
		}
		if len(entries) != 1 || entries[0].Name() != nfc+".txt" {
			t.Errorf("darwin: writing both spellings should give one file named in NFC, got %q", entries)
		}
		if want := filepath.Join(co, nfc+".txt") + "\n"; coCode != 0 || coOut != want {
			t.Errorf("darwin: co: exit %d, got %q, want %q\n%s", coCode, coOut, want, coErr)
		}
	default:
		if code != 2 || out != "" || !isMissingRootError(errOut, other) {
			t.Errorf("%s: --dir in NFD: exit %d, stdout %q, stderr %q; want exit 2 and a missing search location error", runtime.GOOS, code, out, errOut)
		}
		if len(entries) != 2 {
			t.Errorf("%s: both spellings should coexist, got %q", runtime.GOOS, entries)
		}
		want := filepath.Join(co, nfd+".txt") + "\n" + filepath.Join(co, nfc+".txt") + "\n" // byte order: 'e' < 0xc3
		if coCode != 0 || coOut != want {
			t.Errorf("%s: co: exit %d, got %q, want %q\n%s", runtime.GOOS, coCode, coOut, want, coErr)
		}
	}
}

// TestInvalidUTF8QueryCLI pins B1 at the CLI: a query that is not valid
// UTF-8 exits 2 with a clear message instead of being rewritten, while
// --dir still takes the raw bytes of a path.
func TestInvalidUTF8QueryCLI(t *testing.T) {
	en := "nlf: the query contains bytes that are not valid UTF-8\n"
	empty := t.TempDir() // never walk the real working directory, even if Parse accepted the query
	for _, c := range []struct {
		args []string
		want string
	}{
		{[]string{"in /tmp/\xff"}, en},
		{[]string{"--dir", empty, "containing \xff"}, en},
		{[]string{"--dir", empty, "exclude \"\xff\" dir"}, en},
		{[]string{"--dir", empty, "exclude \"\xff\" dir, find go files"}, en},
		{[]string{"--dir", empty, "包含 \xff"}, "nlf: 查詢含有無效的 UTF-8 位元組\n"},
	} {
		out, errOut, code := nlf(t, c.args...)
		if code != 2 || out != "" || errOut != c.want {
			t.Errorf("nlf %q: exit %d, stdout %q, stderr %q; want exit 2, %q", c.args, code, out, errOut, c.want)
		}
	}
	// The same through a real argv.
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"a.txt": "a"})
	if out, errOut, code := subNLF(t, dir, "exclude \"\xff\" dir"); code != 2 || len(out) != 0 || errOut != en {
		t.Errorf("subprocess: exit %d, stdout %q, stderr %q", code, out, errOut)
	}
	if out, errOut, code := nlf(t, "--dir", dir, "files"); code != 0 || out != filepath.Join(dir, "a.txt")+"\n" {
		t.Errorf("--dir with a valid query: exit %d, %q\n%s", code, out, errOut)
	}
	bad := filepath.Join(dir, "bad\xff")
	err := os.Mkdir(bad, 0o755)
	switch runtime.GOOS {
	case "darwin":
		if !errors.Is(err, syscall.EILSEQ) {
			t.Fatalf("darwin: mkdir %q = %v, want EILSEQ", bad, err)
		}
		if out, _, code := nlf(t, "--dir", bad, "files"); code != 2 || out != "" {
			t.Errorf("darwin: --dir %q: exit %d, stdout %q; want exit 2", bad, code, out)
		}
	default:
		if err != nil {
			t.Fatal(err)
		}
		mkfiles(t, bad, map[string]string{"b.txt": "b"})
		if out, errOut, code := nlf(t, "--dir", bad, "files"); code != 0 || out != filepath.Join(bad, "b.txt")+"\n" {
			t.Errorf("%s: --dir with raw bytes: exit %d, %q\n%s", runtime.GOOS, code, out, errOut)
		}
	}
}
