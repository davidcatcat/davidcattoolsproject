//go:build windows

package search

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"syscall"
	"testing"
	"unsafe"
)

// denyAccess makes p unreadable until allowAccess or the end of the test.
// On Windows chmod only toggles the read-only attribute, so this adds an
// ACL entry that denies Everyone (S-1-1-0) reading p's data: listing a
// directory or reading a file. The entry applies to administrators too.
// It is removed in a cleanup that runs before t.TempDir's, which could not
// delete a directory it cannot list.
//
// Go opens every file and directory for reading with
// FILE_FLAG_BACKUP_SEMANTICS, and a token with SeBackupPrivilege enabled
// then skips ACL checks, the Windows counterpart of root ignoring
// permissions on Unix. The test process therefore first disables that
// privilege (nlf itself never changes privileges), and denyAccess checks
// that access really is denied before the test relies on it.
func denyAccess(t *testing.T, p string) {
	t.Helper()
	privOnce.Do(func() {
		privBefore = whoamiPriv()
		privErr = disableBackupPrivileges()
		t.Logf("privileges of the test process before disabling backup/restore:\n%s", privBefore)
	})
	if privErr != nil {
		t.Fatalf("disabling SeBackupPrivilege/SeRestorePrivilege: %v\nwhoami /priv:\n%s", privErr, privBefore)
	}
	icacls(t, p, "/deny", "*S-1-1-0:(RD)")
	t.Cleanup(func() { allowAccess(t, p) })
	if err := readProbe(p); !errors.Is(err, fs.ErrPermission) {
		out, _ := exec.Command(icaclsExe(), p).CombinedOutput()
		t.Fatalf("the deny entry on %s does not deny access (read: %v)\nicacls:\n%s\nwhoami /priv before disabling:\n%s\nnow:\n%s",
			p, err, out, privBefore, whoamiPriv())
	}
}

// allowAccess removes the deny entries denyAccess added.
func allowAccess(t *testing.T, p string) {
	t.Helper()
	icacls(t, p, "/remove:d", "*S-1-1-0")
}

func icaclsExe() string { return filepath.Join(os.Getenv("SystemRoot"), "System32", "icacls.exe") }

func icacls(t *testing.T, args ...string) {
	t.Helper()
	if out, err := exec.Command(icaclsExe(), args...).CombinedOutput(); err != nil {
		t.Fatalf("icacls %q: %v\n%s", args, err, out)
	}
}

// readProbe lists a directory or reads a file.
func readProbe(p string) error {
	fi, err := os.Stat(p)
	if err != nil {
		return err
	}
	if fi.IsDir() {
		_, err = os.ReadDir(p)
	} else {
		_, err = os.ReadFile(p)
	}
	return err
}

var (
	privOnce   sync.Once
	privBefore string
	privErr    error
)

func whoamiPriv() string {
	out, err := exec.Command(filepath.Join(os.Getenv("SystemRoot"), "System32", "whoami.exe"), "/priv").CombinedOutput()
	if err != nil {
		return fmt.Sprintf("whoami /priv: %v\n%s", err, out)
	}
	return string(out)
}

// disableBackupPrivileges turns off SeBackupPrivilege and SeRestorePrivilege
// in this process's token. A privilege the token does not hold is left
// alone (AdjustTokenPrivileges then reports ERROR_NOT_ALL_ASSIGNED).
func disableBackupPrivileges() error {
	advapi := syscall.NewLazyDLL("advapi32.dll")
	lookup, adjust := advapi.NewProc("LookupPrivilegeValueW"), advapi.NewProc("AdjustTokenPrivileges")
	proc, err := syscall.GetCurrentProcess()
	if err != nil {
		return err
	}
	var tok syscall.Token
	if err := syscall.OpenProcessToken(proc, syscall.TOKEN_ADJUST_PRIVILEGES|syscall.TOKEN_QUERY, &tok); err != nil {
		return fmt.Errorf("OpenProcessToken: %w", err)
	}
	defer tok.Close()
	// TOKEN_PRIVILEGES with one LUID_AND_ATTRIBUTES; Attributes 0 disables.
	type luid struct {
		low  uint32
		high int32
	}
	type tokenPrivileges struct {
		count uint32
		luid  luid
		attrs uint32
	}
	for _, name := range []string{"SeBackupPrivilege", "SeRestorePrivilege"} {
		n, err := syscall.UTF16PtrFromString(name)
		if err != nil {
			return err
		}
		tp := tokenPrivileges{count: 1}
		if r, _, err := lookup.Call(0, uintptr(unsafe.Pointer(n)), uintptr(unsafe.Pointer(&tp.luid))); r == 0 {
			return fmt.Errorf("LookupPrivilegeValue %s: %w", name, err)
		}
		if r, _, err := adjust.Call(uintptr(tok), 0, uintptr(unsafe.Pointer(&tp)), 0, 0, 0); r == 0 {
			return fmt.Errorf("AdjustTokenPrivileges %s: %w", name, err)
		}
	}
	return nil
}
