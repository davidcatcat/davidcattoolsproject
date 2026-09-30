//go:build windows

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// denyAccess makes p unreadable until allowAccess or the end of the test.
// On Windows chmod only toggles the read-only attribute, so this adds an
// ACL entry that denies Everyone (S-1-1-0) reading p's data: listing a
// directory or reading a file. The entry applies to administrators too.
// It is removed in a cleanup that runs before t.TempDir's, which could not
// delete a directory it cannot list.
func denyAccess(t *testing.T, p string) {
	t.Helper()
	icacls(t, p, "/deny", "*S-1-1-0:(RD)")
	t.Cleanup(func() { allowAccess(t, p) })
}

// allowAccess removes the deny entries denyAccess added.
func allowAccess(t *testing.T, p string) {
	t.Helper()
	icacls(t, p, "/remove:d", "*S-1-1-0")
}

func icacls(t *testing.T, args ...string) {
	t.Helper()
	exe := filepath.Join(os.Getenv("SystemRoot"), "System32", "icacls.exe")
	if out, err := exec.Command(exe, args...).CombinedOutput(); err != nil {
		t.Fatalf("icacls %q: %v\n%s", args, err, out)
	}
}
