package search

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

func write(t *testing.T, path string, data []byte) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
}

func run(t *testing.T, q *query.Query) ([]string, Report) {
	t.Helper()
	var got []string
	rep, err := Run(q, func(r Result) {
		rel, _ := filepath.Rel(q.Root, r.Path)
		got = append(got, rel)
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	return got, rep
}

func TestContentAcrossChunkBoundary(t *testing.T) {
	dir := t.TempDir()
	data := bytes.Repeat([]byte("a"), chunkSize-3)
	data = append(data, []byte("NEEDLE")...)
	write(t, filepath.Join(dir, "big.txt"), data)
	write(t, filepath.Join(dir, "other.txt"), bytes.Repeat([]byte("b"), 3*chunkSize))

	got, _ := run(t, &query.Query{Root: dir, Contents: []string{"NEEDLE"}})
	if want := []string{"big.txt"}; !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestContentIgnoreCaseAndAllTerms(t *testing.T) {
	dir := t.TempDir()
	write(t, filepath.Join(dir, "a.txt"), []byte("Hello World"))
	write(t, filepath.Join(dir, "b.txt"), []byte("hello there"))

	got, _ := run(t, &query.Query{Root: dir, Contents: []string{"hello", "world"}, IgnoreCase: true})
	if want := []string{"a.txt"}; !reflect.DeepEqual(got, want) {
		t.Errorf("ignore case: got %v, want %v", got, want)
	}
	got, _ = run(t, &query.Query{Root: dir, Contents: []string{"World"}})
	if want := []string{"a.txt"}; !reflect.DeepEqual(got, want) {
		t.Errorf("case-sensitive: got %v, want %v", got, want)
	}
}

func TestBinaryFilesDoNotMatchContent(t *testing.T) {
	dir := t.TempDir()
	write(t, filepath.Join(dir, "bin.dat"), []byte("TODO\x00\x01"))
	got, _ := run(t, &query.Query{Root: dir, Contents: []string{"TODO"}})
	if len(got) != 0 {
		t.Errorf("got %v, want no matches", got)
	}
}

func TestExtensionMatching(t *testing.T) {
	dir := t.TempDir()
	for _, n := range []string{"a.go", "B.GO", "c.tar.gz", "d.gz", ".go", "e.golang"} {
		write(t, filepath.Join(dir, n), nil)
	}
	got, _ := run(t, &query.Query{Root: dir, Extensions: []string{"go", "tar.gz"}})
	if want := []string{"B.GO", "a.go", "c.tar.gz"}; !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestExcludeByNameAndPath(t *testing.T) {
	dir := t.TempDir()
	for _, n := range []string{"keep/a.go", "vendor/b.go", "x/Vendor/c.go", "src/lib/d.go", "lib/e.go"} {
		write(t, filepath.Join(dir, n), nil)
	}
	got, _ := run(t, &query.Query{Root: dir, ExcludeDirs: []string{"vendor", "src/lib"}})
	if want := []string{"keep/a.go", "lib/e.go"}; !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestSortAndLimit(t *testing.T) {
	dir := t.TempDir()
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i, n := range []string{"a", "b", "c", "d"} {
		p := filepath.Join(dir, n)
		write(t, p, bytes.Repeat([]byte("x"), (i%2+1)*100+i))
		if err := os.Chtimes(p, base, base.Add(time.Duration(4-i)*time.Hour)); err != nil {
			t.Fatal(err)
		}
	}
	// sizes: a=100 b=201 c=102 d=203; newest: a, b, c, d
	cases := []struct {
		q    query.Query
		want []string
	}{
		{query.Query{Limit: 2}, []string{"a", "b"}},
		{query.Query{Sort: query.SortLargest, Limit: 3}, []string{"d", "b", "c"}},
		{query.Query{Sort: query.SortNewest, Limit: 2}, []string{"a", "b"}},
		{query.Query{Sizes: []query.SizeCond{{Op: ">=", Bytes: 102}, {Op: "<", Bytes: 203}}}, []string{"b", "c"}},
		{query.Query{ModifiedAfter: base.Add(2 * time.Hour), ModifiedBefore: base.Add(4 * time.Hour)}, []string{"b", "c"}},
	}
	for _, c := range cases {
		c.q.Root = dir
		got, _ := run(t, &c.q)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%+v: got %v, want %v", c.q, got, c.want)
		}
	}
}

func TestPermissionDeniedIsSkippedAndReported(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores permissions")
	}
	dir := t.TempDir()
	write(t, filepath.Join(dir, "ok.txt"), []byte("TODO"))
	write(t, filepath.Join(dir, "locked", "hidden.txt"), []byte("TODO"))
	write(t, filepath.Join(dir, "secret.txt"), []byte("TODO"))
	locked := filepath.Join(dir, "locked")
	secret := filepath.Join(dir, "secret.txt")
	for _, p := range []string{locked, secret} {
		if err := os.Chmod(p, 0); err != nil {
			t.Fatal(err)
		}
	}
	t.Cleanup(func() { os.Chmod(locked, 0o755); os.Chmod(secret, 0o644) })

	got, rep := run(t, &query.Query{Root: dir, Contents: []string{"TODO"}})
	if want := []string{"ok.txt"}; !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
	if want := []string{locked, secret}; !reflect.DeepEqual(rep.PermissionDenied, want) {
		t.Errorf("PermissionDenied = %v, want %v", rep.PermissionDenied, want)
	}
}

func TestSymlinkedRootIsFollowed(t *testing.T) {
	dir := t.TempDir()
	write(t, filepath.Join(dir, "real", "a.txt"), nil)
	link := filepath.Join(dir, "link")
	if err := os.Symlink(filepath.Join(dir, "real"), link); err != nil {
		t.Fatal(err)
	}
	got, _ := run(t, &query.Query{Root: link})
	if want := []string{"a.txt"}; !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestMissingRootIsAnError(t *testing.T) {
	_, err := Run(&query.Query{Root: filepath.Join(t.TempDir(), "nope")}, func(Result) {})
	if !os.IsNotExist(err) {
		t.Errorf("err = %v, want not-exist", err)
	}
}

func TestNormalizeExcludes(t *testing.T) {
	root := t.TempDir()
	tests := []struct {
		in      string
		want    string
		wantErr bool
	}{
		{"vendor", "vendor", false},
		{"src/gen", "./src/gen", false},
		{"./src/gen", "./src/gen", false},
		{"src/gen/", "./src/gen", false},
		{"src//gen", "./src/gen", false},
		{"./src/../src/gen", "./src/gen", false},
		{filepath.Join(root, "src", "gen"), "./src/gen", false},
		{"../outside", "", true},
		{"src/../../outside", "", true},
		{"/definitely/elsewhere", "", true},
		{"./", "", true},
		{root, "", true},
	}
	for _, tt := range tests {
		got, err := NormalizeExcludes(root, []string{tt.in})
		if tt.wantErr {
			var ee *ExcludeError
			if !errors.As(err, &ee) {
				t.Errorf("NormalizeExcludes(%q) err = %v, want *ExcludeError", tt.in, err)
			}
			continue
		}
		if err != nil || len(got) != 1 || got[0] != tt.want {
			t.Errorf("NormalizeExcludes(%q) = %v, %v; want %q", tt.in, got, err, tt.want)
		}
	}
}

func TestExcludeRelativeAndAbsolutePaths(t *testing.T) {
	dir := t.TempDir()
	for _, n := range []string{"src/gen/a.go", "src/keep/b.go", "gen/c.go"} {
		write(t, filepath.Join(dir, n), nil)
	}
	for _, ex := range []string{"./src/gen", "src/gen/", filepath.Join(dir, "src/gen")} {
		got, _ := run(t, &query.Query{Root: dir, ExcludeDirs: []string{ex}})
		if want := []string{"gen/c.go", "src/keep/b.go"}; !reflect.DeepEqual(got, want) {
			t.Errorf("exclude %q: got %v, want %v", ex, got, want)
		}
	}
	if _, err := Run(&query.Query{Root: dir, ExcludeDirs: []string{"../x"}}, func(Result) {}); err == nil {
		t.Error("exclude outside the root should be an error")
	}
}

func TestExcludePathCaseFollowsFileSystem(t *testing.T) {
	dir := t.TempDir()
	write(t, filepath.Join(dir, "src/gen/a.go"), nil)
	write(t, filepath.Join(dir, "src/keep/b.go"), nil)
	// Does this file system resolve a differently-cased path to the same dir?
	a, err1 := os.Stat(filepath.Join(dir, "src/gen"))
	b, err2 := os.Stat(filepath.Join(dir, "SRC/GEN"))
	insensitive := err1 == nil && err2 == nil && os.SameFile(a, b)

	got, _ := run(t, &query.Query{Root: dir, ExcludeDirs: []string{"SRC/GEN"}})
	want := []string{"src/gen/a.go", "src/keep/b.go"}
	if insensitive {
		want = []string{"src/keep/b.go"}
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("case-insensitive fs=%v: got %v, want %v", insensitive, got, want)
	}
}

func TestSymlinksBelowRootAreNotFollowed(t *testing.T) {
	dir := t.TempDir()
	outside := t.TempDir()
	write(t, filepath.Join(dir, "in.txt"), nil)
	write(t, filepath.Join(outside, "secret.txt"), nil)
	for _, l := range [][2]string{{outside, "linkdir"}, {filepath.Join(outside, "secret.txt"), "linkfile.txt"}} {
		if err := os.Symlink(l[0], filepath.Join(dir, l[1])); err != nil {
			t.Fatal(err)
		}
	}
	got, _ := run(t, &query.Query{Root: dir})
	if want := []string{"in.txt"}; !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}
