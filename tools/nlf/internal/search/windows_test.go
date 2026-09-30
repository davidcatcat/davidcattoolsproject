//go:build windows

package search

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

// TestDepthFuncWindowsPaths covers the path forms WalkDir passes on Windows:
// drive roots, a drive-relative root ("C:" has children "C:name"), UNC
// shares with and without a trailing separator, and "/" spellings.
func TestDepthFuncWindowsPaths(t *testing.T) {
	for _, c := range []struct {
		root, path string
		want       int
	}{
		{`C:\`, `C:\`, 0}, {`C:\`, `C:\a`, 1}, {`C:\`, `C:\d\b`, 2},
		{`C:`, `C:`, 0}, {`C:`, `C:a`, 1}, {`C:`, `C:d\b`, 2},
		{`C:.`, `C:a`, 1},
		{`C:\x\y`, `C:\x\y\a`, 1}, {`C:\x\y\`, `C:\x\y\a`, 1}, {`C:/x/y`, `C:\x\y\a`, 1},
		{`\\srv\share`, `\\srv\share`, 0}, {`\\srv\share`, `\\srv\share\a`, 1}, {`\\srv\share`, `\\srv\share\d\b`, 2},
		{`\\srv\share\`, `\\srv\share\a`, 1}, {`\\srv\share\x`, `\\srv\share\x\a`, 1},
		{`\x`, `\x\a`, 1},
	} {
		if got := depthFunc(c.root)(c.path); got != c.want {
			t.Errorf("depthFunc(%q)(%q) = %d, want %d", c.root, c.path, got, c.want)
		}
	}
}

// TestRootIsLinkWindows: a junction reports neither a directory nor a
// symlink to Lstat, but is a link to a directory to follow as the root.
func TestRootIsLinkWindows(t *testing.T) {
	dir := t.TempDir()
	write(t, filepath.Join(dir, "target", "a.txt"), nil)
	junction := filepath.Join(dir, "junction")
	cmd := filepath.Join(os.Getenv("SystemRoot"), "System32", "cmd.exe")
	if out, err := exec.Command(cmd, "/c", "mklink", "/J", junction, filepath.Join(dir, "target")).CombinedOutput(); err != nil {
		t.Fatalf("mklink /J: %v\n%s", err, out)
	}
	symlink := filepath.Join(dir, "symlink")
	if err := os.Symlink(filepath.Join(dir, "target"), symlink); err != nil {
		t.Fatal(err)
	}
	for p, want := range map[string]bool{junction: true, symlink: true, filepath.Join(dir, "target"): false,
		filepath.Join(dir, "target", "a.txt"): false, filepath.Join(dir, "missing"): false} {
		if got := RootIsLink(p); got != want {
			t.Errorf("RootIsLink(%q) = %v, want %v", p, got, want)
		}
	}
	got, rep := run(t, &query.Query{Root: junction})
	if len(got) != 1 || got[0] != "a.txt" || !emptyReport(rep) {
		t.Errorf("Run on a junction root: got %v (%+v), want [a.txt]", got, rep)
	}
}
