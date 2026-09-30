//go:build windows

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"testing"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

// Windows-only CLI tests (A8): OS-native paths given with --dir (drive
// letters, a drive-relative "C:", UNC, "/" and a trailing "\"), junctions,
// hidden files, "~\", case-insensitive NTFS, and Windows paths in excludes.

// mklinkJunction creates a directory junction (mount point) link -> target.
// Tests may run programs; nlf itself never does.
func mklinkJunction(t *testing.T, link, target string) {
	t.Helper()
	cmd := filepath.Join(os.Getenv("SystemRoot"), "System32", "cmd.exe")
	if out, err := exec.Command(cmd, "/c", "mklink", "/J", link, target).CombinedOutput(); err != nil {
		t.Fatalf("mklink /J %s %s: %v\n%s", link, target, err, out)
	}
}

// lines splits newline-separated stdout.
func lines(out string) []string {
	return strings.FieldsFunc(out, func(r rune) bool { return r == '\n' })
}

func TestWindowsDriveAndUNCPaths(t *testing.T) {
	dir := fixture(t)
	vol := filepath.VolumeName(dir)
	if len(vol) != 2 || vol[1] != ':' {
		t.Fatalf("temporary directory %q is not on a drive letter", dir)
	}
	join := func(root string, rel ...string) string { return filepath.Join(append([]string{root}, rel...)...) }
	for _, c := range []struct {
		name string
		args []string
		want []string
	}{
		{"drive letter", []string{"--dir", dir, "go files excluding vendor"}, []string{join(dir, "main.go"), join(dir, "util.go")}},
		{"forward slashes", []string{"--dir", filepath.ToSlash(dir), "md files"}, []string{join(dir, "docs", "readme.md")}},
		{"trailing backslash", []string{"--dir", join(dir, "docs") + `\`, "md files"}, []string{join(dir, "docs", "readme.md")}},
		{"lower-case drive and other case", []string{"--dir", strings.ToLower(vol) + strings.ToUpper(dir[2:]), "--max-depth", "1", "go files"},
			[]string{join(strings.ToLower(vol)+strings.ToUpper(dir[2:]), "main.go"), join(strings.ToLower(vol)+strings.ToUpper(dir[2:]), "util.go")}},
		{"exclude a path in another case", []string{"--dir", dir, "go files excluding ./VENDOR"}, []string{join(dir, "main.go"), join(dir, "util.go")}},
		{"exclude a Windows absolute path", []string{"--dir", dir, `go files excluding "` + join(dir, "vendor") + `"`}, []string{join(dir, "main.go"), join(dir, "util.go")}},
	} {
		out, errOut, code := nlf(t, c.args...)
		if got := lines(out); code != 0 || !slices.Equal(got, c.want) || errOut != "" {
			t.Errorf("%s: %q: exit %d\n got  %q\n want %q\n%s", c.name, c.args, code, got, c.want, errOut)
		}
	}

	// A Windows path outside the search location is an error, not ignored.
	if out, errOut, code := nlf(t, "--dir", dir, `go files excluding "`+join(vol+`\`, "elsewhere")+`"`); code != 2 || out != "" || !strings.Contains(errOut, "outside") {
		t.Errorf("exclude outside: exit %d, stdout %q, stderr %q", code, out, errOut)
	}

	// Drive-relative "C:" is the current directory on that drive; children
	// are "C:name", one level deep. A path starting with "\" is on the
	// current drive, so it is only inside dir once dir is the working
	// directory.
	chdir(t, dir)
	if out, errOut, code := nlf(t, "--dir", dir, `go files excluding "`+join(dir, "vendor")[2:]+`"`); code != 0 ||
		!slices.Equal(lines(out), []string{join(dir, "main.go"), join(dir, "util.go")}) {
		t.Errorf("exclude a rooted path without a drive: exit %d, got %q\n%s", code, lines(out), errOut)
	}
	if out, errOut, code := nlf(t, "--max-depth", "1", "--dir", vol, "go files"); code != 0 ||
		!slices.Equal(lines(out), []string{vol + "main.go", vol + "util.go"}) {
		t.Errorf("--dir %s --max-depth 1: exit %d, got %q\n%s", vol, code, lines(out), errOut)
	}
	if out, errOut, code := nlf(t, "--min-depth", "2", "--dir", vol, "go files"); code != 0 ||
		!slices.Equal(lines(out), []string{vol + `vendor\lib.go`}) {
		t.Errorf("--dir %s --min-depth 2: exit %d, got %q\n%s", vol, code, lines(out), errOut)
	}

	// The drive root: at most one level, every path under it with "\".
	root := vol + `\`
	out, errOut, code := nlf(t, "--max-depth", "1", "--dir", root, "files larger than 0 bytes")
	if code != 0 && code != 1 {
		t.Errorf("--dir %s: exit %d\n%s", root, code, errOut)
	}
	for _, l := range lines(out) {
		if !strings.HasPrefix(l, root) || strings.Contains(l, "/") || strings.Contains(l[len(root):], `\`) {
			t.Errorf("--dir %s --max-depth 1 printed %q", root, l)
		}
	}

	// UNC: the same directory through the administrative share.
	unc := `\\localhost\` + vol[:1] + `$` + dir[2:]
	if _, err := os.Stat(unc); err != nil {
		t.Fatalf("administrative share not reachable: %v", err)
	}
	out, errOut, code = nlf(t, "--dir", unc, "go files excluding vendor")
	if want := []string{join(unc, "main.go"), join(unc, "util.go")}; code != 0 || !slices.Equal(lines(out), want) {
		t.Errorf("UNC --dir: exit %d\n got  %q\n want %q\n%s", code, lines(out), want, errOut)
	}
	out, errOut, code = nlf(t, "--min-depth", "2", "--dir", unc, "go files")
	if want := []string{join(unc, "vendor", "lib.go")}; code != 0 || !slices.Equal(lines(out), want) {
		t.Errorf("UNC --min-depth 2: exit %d\n got  %q\n want %q\n%s", code, lines(out), want, errOut)
	}
	// A share root without a trailing "\": its entries are one level deep.
	share := `\\localhost\` + vol[:1] + `$`
	out, errOut, code = nlf(t, "--max-depth", "1", "--dir", share, "files larger than 0 bytes")
	if code != 0 && code != 1 {
		t.Errorf("--dir %s: exit %d\n%s", share, code, errOut)
	}
	for _, l := range lines(out) {
		if rest, ok := strings.CutPrefix(l, share+`\`); !ok || strings.Contains(rest, `\`) {
			t.Errorf("--dir %s --max-depth 1 printed %q", share, l)
		}
	}
}

func TestWindowsJunctions(t *testing.T) {
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"target/a.txt": "a", "tree/own.txt": "o", "other/b.txt": "b"})
	link := filepath.Join(dir, "link")
	mklinkJunction(t, link, filepath.Join(dir, "target"))
	// A junction as the search location is followed, like a symlinked root.
	out, errOut, code := nlf(t, "--dir", link, "txt files")
	if want := filepath.Join(link, "a.txt") + "\n"; code != 0 || out != want {
		t.Errorf("junction root: exit %d, got %q, want %q\n%s", code, out, want, errOut)
	}
	// A junction below the search location is not followed, like a
	// symlinked directory.
	mklinkJunction(t, filepath.Join(dir, "tree", "j"), filepath.Join(dir, "other"))
	out, errOut, code = nlf(t, "--dir", filepath.Join(dir, "tree"), "txt files")
	if want := filepath.Join(dir, "tree", "own.txt") + "\n"; code != 0 || out != want {
		t.Errorf("junction below the root: exit %d, got %q, want %q\n%s", code, out, want, errOut)
	}
	// --explain shows the junction root as given.
	out, _, code = nlf(t, "--explain", "--dir", link, "txt files")
	if code != 0 || !strings.Contains(out, "root:          "+link+"\n") {
		t.Errorf("--explain junction root: exit %d\n%s", code, out)
	}
	checkNoCommand(t, "--explain junction root", out, query.English)
}

func TestWindowsHiddenFilesAreSearched(t *testing.T) {
	dir := t.TempDir()
	mkfiles(t, dir, map[string]string{"shown.log": "x", "hidden.log": "x", "hiddendir/in.log": "x"})
	for _, p := range []string{filepath.Join(dir, "hidden.log"), filepath.Join(dir, "hiddendir")} {
		name, err := syscall.UTF16PtrFromString(p)
		if err != nil {
			t.Fatal(err)
		}
		if err := syscall.SetFileAttributes(name, syscall.FILE_ATTRIBUTE_HIDDEN); err != nil {
			t.Fatal(err)
		}
	}
	// Like dot files on Unix, hidden files and directories are searched.
	out, errOut, code := nlf(t, "--dir", dir, "log files")
	want := []string{filepath.Join(dir, "hidden.log"), filepath.Join(dir, "hiddendir", "in.log"), filepath.Join(dir, "shown.log")}
	if code != 0 || !slices.Equal(lines(out), want) {
		t.Errorf("hidden files: exit %d\n got  %q\n want %q\n%s", code, lines(out), want, errOut)
	}
}

func TestWindowsHomeAndSpacesAndUnicode(t *testing.T) {
	home := t.TempDir()
	setHome(t, home)
	mkfiles(t, home, map[string]string{"My Docs/a.md": "", "中文 目錄/說明.md": ""})
	for _, c := range []struct{ dir, want string }{
		{`~\My Docs`, filepath.Join(home, "My Docs", "a.md")},
		{`~/My Docs`, filepath.Join(home, "My Docs", "a.md")},
		{filepath.Join(home, "中文 目錄"), filepath.Join(home, "中文 目錄", "說明.md")},
	} {
		if out, errOut, code := nlf(t, "--dir", c.dir, "找 md 檔"); code != 0 || out != c.want+"\n" {
			t.Errorf("--dir %s: exit %d, got %q, want %q\n%s", c.dir, code, out, c.want, errOut)
		}
	}
	// In the query, the home directory is written "~/" as on every OS.
	if out, errOut, code := nlf(t, `在 "~/My Docs" 找 md 檔`); code != 0 || out != filepath.Join(home, "My Docs", "a.md")+"\n" {
		t.Errorf("~/ in the query: exit %d, got %q\n%s", code, out, errOut)
	}
}
