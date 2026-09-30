package main

import (
	"os"
	"runtime"
	"strings"
	"testing"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

// Helpers that let the CLI tests run unchanged on macOS, Linux and Windows.

// hostPlatform is the command dialect --explain and --confirm use here.
var hostPlatform = query.PlatformFor(runtime.GOOS)

// setHome points the home directory at dir for this test. os.UserHomeDir
// reads HOME on Unix and only USERPROFILE on Windows.
func setHome(t *testing.T, dir string) {
	t.Helper()
	t.Setenv("HOME", dir)
	t.Setenv("USERPROFILE", dir)
}

// chdir changes the working directory for the test (Go 1.23 has no t.Chdir).
func chdir(t *testing.T, dir string) {
	t.Helper()
	old, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.Chdir(old); err != nil {
			t.Fatal(err)
		}
	})
}

// checkCommand checks the command part of --explain or --confirm output.
// Where find exists (macOS, Linux) text must contain every want. On Windows
// it must instead carry the no-command note in lang and no Unix command.
func checkCommand(t *testing.T, label, text string, lang query.Lang, wants ...string) {
	t.Helper()
	if hostPlatform.HasFindCommand() {
		for _, w := range wants {
			if !strings.Contains(text, w) {
				t.Errorf("%s: missing %q:\n%s", label, w, text)
			}
		}
		return
	}
	checkNoCommand(t, label, text, lang)
}

// checkNoCommand checks that text shows the Windows note and no find,
// grep or stat command.
func checkNoCommand(t *testing.T, label, text string, lang query.Lang) {
	t.Helper()
	if !strings.Contains(text, query.NoCommandNote(lang)) {
		t.Errorf("%s: missing the no-command note %q:\n%s", label, query.NoCommandNote(lang), text)
	}
	for _, bad := range []string{"find ", "-exec", "-iname", "-type f", "stat -", "| head", "Approximate " + hostPlatform.Name()} {
		if strings.Contains(text, bad) {
			t.Errorf("%s: shows a Unix command (%q):\n%s", label, bad, text)
		}
	}
}
