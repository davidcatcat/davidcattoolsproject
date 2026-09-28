package search

import (
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

// maxDepth returns a Query limited to at most n levels below root.
func maxDepth(root string, n int) query.Query {
	return query.Query{Root: root, MaxDepth: n, HasMaxDepth: true}
}

// chmod changes a mode for the test and restores restore afterwards.
func chmod(t *testing.T, p string, mode, restore os.FileMode) {
	t.Helper()
	if err := os.Chmod(p, mode); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(p, restore) })
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

// isRoot reports whether permissions are ignored; the positive controls
// that need a permission error are then only logged.
func isRoot(t *testing.T) bool {
	if os.Geteuid() == 0 {
		t.Log("running as root: permission errors cannot be provoked; checking only that nothing is reported")
		return true
	}
	return false
}

func emptyReport(rep Report) bool { return len(rep.PermissionDenied) == 0 && len(rep.OtherErrors) == 0 }

func TestDepthFunc(t *testing.T) {
	for _, c := range []struct {
		root, path string
		want       int
	}{
		{".", ".", 0}, {".", "a", 1}, {".", "d/b", 2}, {"./", "a", 1}, {"./x", "./x", 0}, {"./x", "x/a", 1},
		{"x/", "x/a", 1}, {"x/", "x/d/b", 2}, {"/", "/", 0}, {"/", "/a", 1}, {"/", "/d/b", 2},
		{"/abs/r", "/abs/r/a", 1}, {"/tmp/", "/tmp/a", 1}, {"a//b", "a/b/c", 1}, {"../x", "../x/a", 1},
		{"file", "file", 0}, {"日本 語/", "日本 語/a b/c", 2},
	} {
		root, path := filepath.FromSlash(c.root), filepath.FromSlash(c.path)
		if got := depthFunc(root)(path); got != c.want {
			t.Errorf("depthFunc(%q)(%q) = %d, want %d", c.root, c.path, got, c.want)
		}
	}
}

// TestDepthFuncMatchesWalk checks depthFunc against the paths WalkDir really
// passes for many spellings of the same root.
func TestDepthFuncMatchesWalk(t *testing.T) {
	dir := t.TempDir()
	for _, p := range []string{"sub/a.txt", "sub/d/b.txt", "sub/d/e/c.txt", "sub/日本 語/x y.txt"} {
		write(t, filepath.Join(dir, p), nil)
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(filepath.Join(dir, "sub"), link); err != nil {
		t.Fatal(err)
	}
	sep := string(filepath.Separator)
	check := func(root string) {
		t.Helper()
		depthOf := depthFunc(root)
		n := 0
		err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if p == root {
				if got := depthOf(p); got != 0 {
					t.Errorf("root %q: depth %d, want 0", root, got)
				}
				return nil
			}
			rel, err := filepath.Rel(root, p)
			if err != nil {
				return err
			}
			if got, want := depthOf(p), strings.Count(rel, sep)+1; got != want {
				t.Errorf("root %q, path %q: depth %d, want %d", root, p, got, want)
			}
			n++
			return nil
		})
		if err != nil {
			t.Fatalf("walk %q: %v", root, err)
		}
		if n < 4 {
			t.Errorf("root %q: walked only %d paths", root, n)
		}
	}
	check(filepath.Join(dir, "sub"))
	check(filepath.Join(dir, "sub") + sep)
	check(link + sep)
	chdir(t, dir)
	for _, root := range []string{".", "." + sep, "sub", "." + sep + "sub", "sub" + sep} {
		check(root)
	}
}

// depthTree has one file at each depth from 1 to 4.
func depthTree(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	for _, p := range []string{"a.txt", "d1/b.txt", "d1/d2/c.txt", "d1/d2/d3/e.txt"} {
		write(t, filepath.Join(dir, p), []byte(p))
	}
	return dir
}

var depthFiles = []string{"a.txt", "d1/b.txt", "d1/d2/c.txt", "d1/d2/d3/e.txt"}

func slash(ps []string) []string {
	out := make([]string, len(ps))
	for i, p := range ps {
		out[i] = filepath.ToSlash(p)
	}
	return out
}

func TestMaxDepth(t *testing.T) {
	dir := depthTree(t)
	for m := 0; m <= 5; m++ {
		q := maxDepth(dir, m)
		got, rep := run(t, &q)
		want := depthFiles[:min(m, len(depthFiles))]
		if !slices.Equal(slash(got), want) || !emptyReport(rep) {
			t.Errorf("max %d: got %v (%+v), want %v", m, got, rep, want)
		}
	}
	// The zero Query has no depth limit: MaxDepth 0 applies only with HasMaxDepth.
	got, _ := run(t, &query.Query{Root: dir})
	if !slices.Equal(slash(got), depthFiles) {
		t.Errorf("zero Query: got %v, want %v", got, depthFiles)
	}
}

func TestMinDepth(t *testing.T) {
	dir := depthTree(t)
	for _, c := range []struct {
		min  int
		want []string
	}{
		{0, depthFiles}, {1, depthFiles}, {2, depthFiles[1:]}, {3, depthFiles[2:]}, {5, nil},
	} {
		got, rep := run(t, &query.Query{Root: dir, MinDepth: c.min})
		if !slices.Equal(slash(got), c.want) || !emptyReport(rep) {
			t.Errorf("min %d: got %v (%+v), want %v", c.min, got, rep, c.want)
		}
	}
}

func TestMinMaxDepth(t *testing.T) {
	dir := depthTree(t)
	for _, c := range []struct {
		min, max int
		want     []string
	}{
		{1, 1, depthFiles[:1]}, {2, 3, depthFiles[1:3]}, {2, 2, depthFiles[1:2]},
	} {
		q := maxDepth(dir, c.max)
		q.MinDepth = c.min
		got, rep := run(t, &q)
		if !slices.Equal(slash(got), c.want) || !emptyReport(rep) {
			t.Errorf("min %d max %d: got %v (%+v), want %v", c.min, c.max, got, rep, c.want)
		}
	}
}

func TestDepthEmptyTree(t *testing.T) {
	dir := t.TempDir()
	for _, q := range []query.Query{maxDepth(dir, 0), maxDepth(dir, 1), {Root: dir, MinDepth: 1}} {
		got, rep := run(t, &q)
		if len(got) != 0 || !emptyReport(rep) {
			t.Errorf("%+v: got %v (%+v), want nothing", q, got, rep)
		}
	}
}

func TestDepthRootFile(t *testing.T) {
	file := filepath.Join(t.TempDir(), "f.txt")
	write(t, file, []byte("x"))
	both := maxDepth(file, 0)
	for _, c := range []struct {
		q    query.Query
		want []string
	}{
		{maxDepth(file, 0), []string{file}},
		{query.Query{Root: file, MinDepth: 1}, nil},
		{both, []string{file}},
	} {
		var got []string
		rep, err := Run(&c.q, func(r Result) { got = append(got, r.Path) })
		if err != nil || !slices.Equal(got, c.want) || !emptyReport(rep) {
			t.Errorf("%+v: got %v (%+v, %v), want %v", c.q, got, rep, err, c.want)
		}
	}
}

func TestDepthSymlinkRoot(t *testing.T) {
	dir := t.TempDir()
	for _, p := range []string{"real/a.txt", "real/s/b.txt", "real/s/t/c.txt"} {
		write(t, filepath.Join(dir, p), nil)
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(filepath.Join(dir, "real"), link); err != nil {
		t.Fatal(err)
	}
	q := maxDepth(link, 1)
	if got, _ := run(t, &q); !slices.Equal(slash(got), []string{"a.txt"}) {
		t.Errorf("max 1 on a symlinked root: got %v, want [a.txt]", got)
	}
	if got, _ := run(t, &query.Query{Root: link, MinDepth: 2}); !slices.Equal(slash(got), []string{"s/b.txt", "s/t/c.txt"}) {
		t.Errorf("min 2 on a symlinked root: got %v, want [s/b.txt s/t/c.txt]", got)
	}
}

// TestDepthExcludeAboveMinDepth: directories above the minimum depth are
// still walked and excluded ones are still pruned.
func TestDepthExcludeAboveMinDepth(t *testing.T) {
	dir := t.TempDir()
	for _, p := range []string{"top.txt", "vendor/v.txt", "vendor/sub/u.txt", "d/vendor/w.txt", "d/k.txt"} {
		write(t, filepath.Join(dir, p), nil)
	}
	got, _ := run(t, &query.Query{Root: dir, ExcludeDirs: []string{"vendor"}, MinDepth: 2})
	if want := []string{"d/k.txt"}; !slices.Equal(slash(got), want) {
		t.Errorf("min 2 exclude vendor: got %v, want %v", got, want)
	}
	// An excluded directory exactly at the maximum depth.
	q := maxDepth(dir, 2)
	q.ExcludeDirs = []string{"vendor"}
	if got, _ := run(t, &q); !slices.Equal(slash(got), []string{"d/k.txt", "top.txt"}) {
		t.Errorf("max 2 exclude vendor: got %v, want [d/k.txt top.txt]", got)
	}
	q = maxDepth(dir, 1)
	q.ExcludeDirs = []string{"vendor"}
	if got, _ := run(t, &q); !slices.Equal(slash(got), []string{"top.txt"}) {
		t.Errorf("max 1 exclude vendor: got %v, want [top.txt]", got)
	}
}

func TestDepthUnicodeSpacePath(t *testing.T) {
	root := filepath.Join(t.TempDir(), "日本 語")
	write(t, filepath.Join(root, "sp ace", "檔案.txt"), nil)
	write(t, filepath.Join(root, "x", "sp ace", "深.txt"), nil)
	q := maxDepth(root, 2)
	if got, _ := run(t, &q); !slices.Equal(slash(got), []string{"sp ace/檔案.txt"}) {
		t.Errorf("max 2: got %v", got)
	}
	if got, _ := run(t, &query.Query{Root: root, MinDepth: 3}); !slices.Equal(slash(got), []string{"x/sp ace/深.txt"}) {
		t.Errorf("min 3: got %v", got)
	}
}

func TestDepthSortAndLimit(t *testing.T) {
	dir := t.TempDir()
	base := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	for _, f := range []struct {
		path string
		size int
		age  time.Duration
	}{
		{"a.txt", 10, 5 * time.Hour}, {"b.txt", 20, 4 * time.Hour}, {"c.txt", 5, time.Minute},
		{"d/m.txt", 30, 2 * time.Hour}, {"d/e/big.txt", 1000, 3 * time.Hour},
	} {
		p := filepath.Join(dir, f.path)
		write(t, p, []byte(strings.Repeat("x", f.size)))
		mt := base.Add(-f.age)
		if err := os.Chtimes(p, mt, mt); err != nil {
			t.Fatal(err)
		}
	}
	q := maxDepth(dir, 1)
	q.Sort, q.Limit = query.SortLargest, 2
	if got, _ := run(t, &q); !slices.Equal(slash(got), []string{"b.txt", "a.txt"}) {
		t.Errorf("largest 2 max 1: got %v, want [b.txt a.txt]", got)
	}
	// The newest file (c.txt) is at depth 1.
	if got, _ := run(t, &query.Query{Root: dir, Sort: query.SortNewest, MinDepth: 2}); !slices.Equal(slash(got), []string{"d/m.txt", "d/e/big.txt"}) {
		t.Errorf("newest min 2: got %v, want [d/m.txt d/e/big.txt]", got)
	}
}

func TestDepthUnsortedLimit(t *testing.T) {
	dir := t.TempDir()
	for _, p := range []string{"a.txt", "b/x.txt", "c/y.txt"} {
		write(t, filepath.Join(dir, p), nil)
	}
	if got, _ := run(t, &query.Query{Root: dir, Limit: 1, MinDepth: 2}); !slices.Equal(slash(got), []string{"b/x.txt"}) {
		t.Errorf("limit 1 min 2: got %v, want [b/x.txt]", got)
	}
}

// TestMaxDepthDoesNotReadBelow proves --max-depth prunes during the walk:
// unreadable directories exactly at the maximum depth are never opened, so
// they are not reported.
func TestMaxDepthDoesNotReadBelow(t *testing.T) {
	root := isRoot(t)
	dir := t.TempDir()
	write(t, filepath.Join(dir, "ok.txt"), []byte("TODO"))
	write(t, filepath.Join(dir, "lock", "x.txt"), []byte("TODO"))
	write(t, filepath.Join(dir, "d", "z.txt"), []byte("TODO"))
	write(t, filepath.Join(dir, "d", "lock2", "y.txt"), []byte("TODO"))
	lock, lock2 := filepath.Join(dir, "lock"), filepath.Join(dir, "d", "lock2")
	chmod(t, lock, 0, 0o755)
	chmod(t, lock2, 0, 0o755)

	q := maxDepth(dir, 1)
	q.Contents = []string{"TODO"}
	got, rep := run(t, &q)
	if !slices.Equal(slash(got), []string{"ok.txt"}) || !emptyReport(rep) {
		t.Errorf("max 1: got %v (%+v), want [ok.txt] and nothing reported", got, rep)
	}

	q.MaxDepth = 2
	got, rep = run(t, &q)
	want := []string{"d/z.txt", "ok.txt"}
	if root {
		want = []string{"d/z.txt", "lock/x.txt", "ok.txt"}
	}
	if !slices.Equal(slash(got), want) || slices.Contains(rep.PermissionDenied, lock2) || len(rep.OtherErrors) != 0 {
		t.Errorf("max 2: got %v (%+v), want %v and %s not reported", got, rep, want, lock2)
	}
	// Positive control: the directory above the maximum is read and denied.
	if !root && !slices.Equal(rep.PermissionDenied, []string{lock}) {
		t.Errorf("max 2: PermissionDenied = %v, want [%s]", rep.PermissionDenied, lock)
	}

	// --max-depth 0 on an unreadable root: the root is never read.
	locked := filepath.Join(t.TempDir(), "locked")
	write(t, filepath.Join(locked, "f.txt"), nil)
	chmod(t, locked, 0, 0o755)
	var n int
	lq := maxDepth(locked, 0)
	rep, err := Run(&lq, func(Result) { n++ })
	if err != nil || n != 0 || !emptyReport(rep) {
		t.Errorf("max 0 on an unreadable root: err=%v results=%d report=%+v, want nothing", err, n, rep)
	}
}

// TestMinDepthSkipsBeforeStatAndOpen proves files above the minimum depth
// are skipped before d.Info() and before their content is read.
func TestMinDepthSkipsBeforeStatAndOpen(t *testing.T) {
	root := isRoot(t)
	// (a) An unreadable file at depth 1 is never opened.
	dir := t.TempDir()
	write(t, filepath.Join(dir, "top.txt"), []byte("TODO"))
	write(t, filepath.Join(dir, "d", "deep.txt"), []byte("TODO"))
	chmod(t, filepath.Join(dir, "top.txt"), 0, 0o644)
	got, rep := run(t, &query.Query{Root: dir, Contents: []string{"TODO"}, MinDepth: 2})
	if !slices.Equal(slash(got), []string{"d/deep.txt"}) || !emptyReport(rep) {
		t.Errorf("(a) min 2: got %v (%+v), want [d/deep.txt] and nothing reported", got, rep)
	}
	if !root {
		if _, rep := run(t, &query.Query{Root: dir, Contents: []string{"TODO"}}); len(rep.PermissionDenied) != 1 {
			t.Errorf("(a) control: PermissionDenied = %v, want top.txt", rep.PermissionDenied)
		}
	}

	// (b) In a directory without search permission, names can be listed but
	// d.Info() fails: a file above the minimum depth is skipped before it.
	dir = t.TempDir()
	r := filepath.Join(dir, "r")
	write(t, filepath.Join(r, "f.txt"), nil)
	chmod(t, r, 0o444, 0o755)
	got, rep = run(t, &query.Query{Root: dir, Extensions: []string{"txt"}, MinDepth: 3})
	if len(got) != 0 || !emptyReport(rep) {
		t.Errorf("(b) min 3: got %v (%+v), want nothing", got, rep)
	}
	got, rep = run(t, &query.Query{Root: dir, Extensions: []string{"txt"}, MinDepth: 2})
	if !root && (len(got) != 0 || !slices.Equal(rep.PermissionDenied, []string{filepath.Join(r, "f.txt")})) {
		t.Errorf("(b) control min 2: got %v, PermissionDenied = %v, want [%s]", got, rep.PermissionDenied, filepath.Join(r, "f.txt"))
	}
}
