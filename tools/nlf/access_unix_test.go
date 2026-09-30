//go:build !windows

package main

import (
	"os"
	"testing"
)

// denyAccess makes p unreadable (mode 0) until allowAccess or the end of
// the test.
func denyAccess(t *testing.T, p string) {
	t.Helper()
	if err := os.Chmod(p, 0); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { allowAccess(t, p) })
}

// allowAccess makes p readable again: 0755 for a directory, 0644 for a file.
func allowAccess(t *testing.T, p string) {
	t.Helper()
	fi, err := os.Lstat(p)
	if err != nil {
		t.Fatal(err)
	}
	mode := os.FileMode(0o644)
	if fi.IsDir() {
		mode = 0o755
	}
	if err := os.Chmod(p, mode); err != nil {
		t.Fatal(err)
	}
}
