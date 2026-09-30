package search

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
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

// mtimeTick is the smallest mtime step the test file system keeps: 1ns, or
// 100ns on Windows, where NTFS stores times in 100ns units. Boundary tests
// place files one tick before or after a bound.
var mtimeTick = time.Nanosecond

func init() {
	if runtime.GOOS == "windows" {
		mtimeTick = 100 * time.Nanosecond
	}
}

// lastTick is the last representable instant of a second, as nanoseconds.
func lastTick() int { return int(time.Second - mtimeTick) }

func run(t *testing.T, q *query.Query) ([]string, Report) {
	t.Helper()
	var got []string
	rep, err := Run(q, func(r Result) {
		rel, _ := filepath.Rel(q.Root, r.Path)
		got = append(got, filepath.ToSlash(rel)) // expectations use "/" on every OS
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
	denyAccess(t, locked)
	denyAccess(t, secret)

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

// TestDateRangeBoundaries checks start <= mtime < end on real files whose
// modification times sit exactly on and next to the range boundaries.
func TestDateRangeBoundaries(t *testing.T) {
	loc := time.FixedZone("CST", 8*3600)
	now := time.Date(2026, 9, 28, 10, 30, 0, 0, loc)
	at := func(mo time.Month, d, h, mi, s, ns int) time.Time { return time.Date(2026, mo, d, h, mi, s, ns, loc) }
	dir := t.TempDir()
	files := map[string]time.Time{
		"0831-235959":      at(8, 31, 23, 59, 59, 0),
		"0831-last-ns":     at(8, 31, 23, 59, 59, lastTick()),
		"0901-000000":      at(9, 1, 0, 0, 0, 0),
		"0915-120000":      at(9, 15, 12, 0, 0, 0),
		"0915-235959":      at(9, 15, 23, 59, 59, 0),
		"0916-000000":      at(9, 16, 0, 0, 0, 0),
		"0928-005959":      at(9, 28, 0, 59, 59, 0),
		"0928-010000":      at(9, 28, 1, 0, 0, 0),
		"0928-045959":      at(9, 28, 4, 59, 59, 0),
		"0928-050000":      at(9, 28, 5, 0, 0, 0),
		"0930-235959":      at(9, 30, 23, 59, 59, 0),
		"0930-last-ns":     at(9, 30, 23, 59, 59, lastTick()),
		"1001-000000":      at(10, 1, 0, 0, 0, 0),
		"1001-000000-plus": at(10, 1, 0, 0, 0, int(mtimeTick)),
	}
	for n, mt := range files {
		p := filepath.Join(dir, n)
		write(t, p, nil)
		if err := os.Chtimes(p, mt, mt); err != nil {
			t.Fatal(err)
		}
		if info, err := os.Stat(p); err != nil || !info.ModTime().Equal(mt) {
			t.Skipf("file system does not keep nanosecond mtimes (%v)", err)
		}
	}
	tests := []struct {
		query string
		want  []string
	}{
		{"找 2026 年 9 月的檔案", []string{"0901-000000", "0915-120000", "0915-235959", "0916-000000",
			"0928-005959", "0928-010000", "0928-045959", "0928-050000", "0930-235959", "0930-last-ns"}},
		{"找 9 月 1 日到 9 月 15 日的檔案", []string{"0901-000000", "0915-120000", "0915-235959"}},
		{"找 2026-09-16 的檔案", []string{"0916-000000"}},
		{"找 9 月 28 日凌晨 1 點到 5 點的檔案", []string{"0928-010000", "0928-045959"}},
		{"找 8 月的檔案", []string{"0831-235959", "0831-last-ns"}},
		{"找 10 月的檔案", []string{"1001-000000", "1001-000000-plus"}},
	}
	for _, tt := range tests {
		q, err := query.Parse(tt.query, now)
		if err != nil {
			t.Fatalf("Parse(%q): %v", tt.query, err)
		}
		q.Root = dir
		got, _ := run(t, q)
		if !reflect.DeepEqual(got, tt.want) {
			t.Errorf("%s: got %v, want %v", tt.query, got, tt.want)
		}
	}
}

// TestToNowBoundary checks that "to now" ends exactly at Query.Now
// (now-1ns matches, now and later do not) while "since" has no end.
func TestToNowBoundary(t *testing.T) {
	loc := time.FixedZone("CST", 8*3600)
	now := time.Date(2026, 9, 28, 10, 30, 0, 500, loc)
	dir := t.TempDir()
	files := map[string]time.Time{
		"a-before-start": time.Date(2026, 8, 31, 23, 59, 59, lastTick(), loc),
		"b-start":        time.Date(2026, 9, 1, 0, 0, 0, 0, loc),
		"c-now-minus-1":  now.Add(-mtimeTick),
		"d-now":          now,
		"e-now-plus-1":   now.Add(mtimeTick),
	}
	for n, mt := range files {
		p := filepath.Join(dir, n)
		write(t, p, nil)
		if err := os.Chtimes(p, mt, mt); err != nil {
			t.Fatal(err)
		}
		if info, err := os.Stat(p); err != nil || !info.ModTime().Equal(mt) {
			t.Skipf("file system does not keep nanosecond mtimes (%v)", err)
		}
	}
	tests := []struct {
		query string
		want  []string
	}{
		{"找從 9 月 1 日到現在修改的檔案", []string{"b-start", "c-now-minus-1"}},
		{"from Sep 1 until now", []string{"b-start", "c-now-minus-1"}},
		{"from Sep 1 to now", []string{"b-start", "c-now-minus-1"}},
		{"since Sep 1", []string{"b-start", "c-now-minus-1", "d-now", "e-now-plus-1"}},
		{"找 9 月 1 日以來的檔案", []string{"b-start", "c-now-minus-1", "d-now", "e-now-plus-1"}},
	}
	for _, tt := range tests {
		q, err := query.Parse(tt.query, now)
		if err != nil {
			t.Fatalf("Parse(%q): %v", tt.query, err)
		}
		q.Root = dir
		got, _ := run(t, q)
		if !reflect.DeepEqual(got, tt.want) {
			t.Errorf("%s: got %v, want %v", tt.query, got, tt.want)
		}
	}
}

// TestOrGroups evaluates OR groups on real files: each group needs one
// matching alternative, and groups are ANDed with each other and the rest.
func TestOrGroups(t *testing.T) {
	loc := time.FixedZone("CST", 8*3600)
	now := time.Date(2026, 9, 28, 10, 30, 0, 0, loc)
	today := time.Date(2026, 9, 28, 9, 0, 0, 0, loc)
	dir := t.TempDir()
	for _, f := range []struct {
		name, data string
		mtime      time.Time
	}{
		{"a.log", "boot ERROR here", today},
		{"b.log", "FATAL disk", today.AddDate(0, 0, -1)},
		{"c.log", "ERROR old", today.AddDate(0, 0, -2)},
		{"d.txt", "ERROR text", today},
		{"e.log", "INFO fine", today},
	} {
		p := filepath.Join(dir, f.name)
		write(t, p, []byte(f.data))
		if err := os.Chtimes(p, f.mtime, f.mtime); err != nil {
			t.Fatal(err)
		}
	}
	tests := []struct {
		query string
		want  []string
	}{
		{"找今天或昨天修改的檔案", []string{"a.log", "b.log", "d.txt", "e.log"}},
		{"找今天或昨天修改的 log", []string{"a.log", "b.log", "e.log"}},
		{`找包含 "ERROR" 或 "FATAL" 的 log`, []string{"a.log", "b.log", "c.log"}},
		{`找今天或昨天修改，而且包含 "ERROR" 或 "FATAL" 的 log`, []string{"a.log", "b.log"}},
		{`find log files containing "ERROR" or "panic" modified today or yesterday`, []string{"a.log"}},
		{`找包含 "error" 或 "fatal" 不分大小寫的檔案`, []string{"a.log", "b.log", "c.log", "d.txt"}},
		{`找包含 "ERROR" 且包含 "boot" 的檔案`, []string{"a.log"}},                          // AND unchanged
		{`找包含 "boot" 或 "disk"、包含 "ERROR" 的檔案`, []string{"a.log"}},                  // AND + OR
		{"找 2026-09-26 或 2026-09-27 修改的 log", []string{"b.log", "c.log"}},          // disjoint days
		{"找 2026-09-26 或 2026-09-28 修改的 log", []string{"a.log", "c.log", "e.log"}}, // not merged into one range
	}
	for _, tt := range tests {
		q, err := query.Parse(tt.query, now)
		if err != nil {
			t.Fatalf("Parse(%q): %v", tt.query, err)
		}
		q.Root = dir
		got, _ := run(t, q)
		if !reflect.DeepEqual(got, tt.want) {
			t.Errorf("%s: got %v, want %v", tt.query, got, tt.want)
		}
	}
}

func TestSizeOrGroup(t *testing.T) {
	dir := t.TempDir()
	write(t, filepath.Join(dir, "tiny"), bytes.Repeat([]byte("x"), 10))
	write(t, filepath.Join(dir, "mid"), bytes.Repeat([]byte("x"), 5<<10))
	write(t, filepath.Join(dir, "big"), bytes.Repeat([]byte("x"), 2<<20))
	for _, c := range []struct {
		query string
		want  []string
	}{
		{"找大於 1MB 或小於 1KB 的檔案", []string{"big", "tiny"}},
		{"files larger than 1MB or smaller than 1KB", []string{"big", "tiny"}},
		{"找大於 1KB 或小於 1KB 的檔案", []string{"big", "mid", "tiny"}},
		{"找大於 1MB 或小於 1KB、大於 1 bytes 的檔案", []string{"big", "tiny"}},
		{"找大於 3MB 或小於 5 bytes 的檔案", nil},
	} {
		q, err := query.Parse(c.query, time.Now())
		if err != nil {
			t.Fatalf("Parse(%q): %v", c.query, err)
		}
		q.Root = dir
		got, _ := run(t, q)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: got %v, want %v", c.query, got, c.want)
		}
	}
}

// TestContentOrScan checks the shared single-pass scanner with OR terms:
// chunk boundaries, binary files and AND terms mixed with an OR group.
func TestContentOrScan(t *testing.T) {
	dir := t.TempDir()
	write(t, filepath.Join(dir, "span.txt"), append(bytes.Repeat([]byte("a"), chunkSize-3), []byte("FATAL")...))
	write(t, filepath.Join(dir, "late.txt"), append(bytes.Repeat([]byte("b"), 3*chunkSize), []byte("panic")...))
	write(t, filepath.Join(dir, "bin.dat"), []byte("ERROR\x00FATAL"))
	write(t, filepath.Join(dir, "none.txt"), bytes.Repeat([]byte("c"), 2*chunkSize))
	anyOf := func(terms ...string) []query.OrGroup {
		return []query.OrGroup{{Kind: query.KindContent, Contents: terms}}
	}
	for _, c := range []struct {
		q    query.Query
		want []string
	}{
		{query.Query{AnyOf: anyOf("ERROR", "FATAL", "panic")}, []string{"late.txt", "span.txt"}},
		{query.Query{AnyOf: anyOf("fatal", "PANIC"), IgnoreCase: true}, []string{"late.txt", "span.txt"}},
		{query.Query{Contents: []string{"aaa"}, AnyOf: anyOf("FATAL", "panic")}, []string{"span.txt"}},
		{query.Query{Contents: []string{"bbb"}, AnyOf: anyOf("nope", "zzz")}, nil},
		{query.Query{AnyOf: append(anyOf("FATAL"), anyOf("panic")...)}, nil}, // two groups: both needed
	} {
		c.q.Root = dir
		got, _ := run(t, &c.q)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%+v: got %v, want %v", c.q, got, c.want)
		}
	}
}

// TestFilenameSearch matches base names on real files, case-sensitively.
func TestFilenameSearch(t *testing.T) {
	dir := t.TempDir()
	// Application.yml lives in its own directory: on a case-insensitive file
	// system (macOS default) it would otherwise be the same file as application.yml.
	for _, n := range []string{"application.yml", "Case/Application.yml", "application-prod.yml", "nginx.conf", "httpd.conf",
		"nginx.conf.bak", ".env", ".gitignore", "foo bar.conf", "中文設定檔.yml", "app-api.log", "api-app.log",
		"core.1234", "core.dump", "--weird-name", "name[1].txt", "sub/application.yml", "config/other.txt"} {
		write(t, filepath.Join(dir, n), nil)
	}
	tests := []struct {
		query string
		want  []string
	}{
		{"找檔名是 application.yml", []string{"application.yml", "sub/application.yml"}}, // any directory, base name only
		{`檔名是 "Application.yml"`, []string{"Case/Application.yml"}},                  // case-sensitive
		{"檔名包含 config", nil}, // "config" is a directory, not a file name
		{"檔名以 .bak 結尾", []string{"nginx.conf.bak"}},
		{`檔名符合 "core.*"`, []string{"core.1234", "core.dump"}},
		{"檔名以 app- 開頭", []string{"app-api.log"}},
		{"檔名包含 app", []string{"api-app.log", "app-api.log", "application-prod.yml", "application.yml", "sub/application.yml"}},
		{"檔名是 .env", []string{".env"}},
		{`檔名符合 ".*"`, []string{".env", ".gitignore"}},
		{`檔名是 "foo bar.conf"`, []string{"foo bar.conf"}},
		{`檔名是 "中文設定檔.yml"`, []string{"中文設定檔.yml"}},
		{"檔名是 --weird-name", []string{"--weird-name"}},
		{"檔名是 name[1].txt", []string{"name[1].txt"}},
		{`檔名符合 "*.conf"`, []string{"foo bar.conf", "httpd.conf", "nginx.conf"}},
		{"檔名是 nginx.conf 或 httpd.conf", []string{"httpd.conf", "nginx.conf"}},
		{`檔名符合 "*.conf" 或 "*.bak"`, []string{"foo bar.conf", "httpd.conf", "nginx.conf", "nginx.conf.bak"}},
		{"檔名包含 prod 或 staging 的 yml", []string{"application-prod.yml"}},
		{"檔名包含 app 且檔名以 app- 開頭", []string{"app-api.log"}},
		{"找檔名包含 application 的 yml，排除 sub", []string{"application-prod.yml", "application.yml"}},
	}
	for _, tt := range tests {
		q, err := query.Parse(tt.query, time.Now())
		if err != nil {
			t.Fatalf("Parse(%q): %v", tt.query, err)
		}
		q.Root = dir
		got, _ := run(t, q)
		if !reflect.DeepEqual(got, tt.want) {
			t.Errorf("%s: got %v, want %v", tt.query, got, tt.want)
		}
	}
}

// TestFilenameComposite combines filename conditions with time, content and size.
func TestFilenameComposite(t *testing.T) {
	loc := time.FixedZone("CST", 8*3600)
	now := time.Date(2026, 9, 28, 10, 30, 0, 0, loc)
	today, older := time.Date(2026, 9, 28, 9, 0, 0, 0, loc), time.Date(2026, 9, 20, 9, 0, 0, 0, loc)
	dir := t.TempDir()
	for _, f := range []struct {
		name, data string
		mtime      time.Time
	}{
		{"app-config.yml", "a: 1", today},
		{"old-config.yml", "a: 1", older},
		{"nginx.conf", "listen 443 ssl;", older},
		{"sites/nginx.conf", "listen 80;", today},
		{"httpd.conf", "Listen 443", today},
		{"prod.yml", "x", today},
		{"staging.yaml", "x", older},
		{"prod.json", "x", today},
		{"big.log", strings.Repeat("x", 2<<20), today},
		{"small.log", "x", today},
		{"big.txt", strings.Repeat("x", 2<<20), today},
	} {
		p := filepath.Join(dir, f.name)
		write(t, p, []byte(f.data))
		if err := os.Chtimes(p, f.mtime, f.mtime); err != nil {
			t.Fatal(err)
		}
	}
	tests := []struct {
		query string
		want  []string
	}{
		{"找今天修改而且檔名包含 config 的檔案", []string{"app-config.yml"}},
		{`找檔名是 nginx.conf，而且內容包含 "listen 443"`, []string{"nginx.conf"}},
		{`找檔名符合 "*.log" 且大於 1MB 的檔案`, []string{"big.log"}},
		{"找檔名是 nginx.conf 或 httpd.conf，今天修改", []string{"httpd.conf", "sites/nginx.conf"}},
		{"找檔名包含 prod 或 staging 的 yml", []string{"prod.yml", "staging.yaml"}},
		{"find yaml files whose name contains config", []string{"app-config.yml", "old-config.yml"}},
	}
	for _, tt := range tests {
		q, err := query.Parse(tt.query, now)
		if err != nil {
			t.Fatalf("Parse(%q): %v", tt.query, err)
		}
		q.Root = dir
		got, _ := run(t, q)
		if !reflect.DeepEqual(got, tt.want) {
			t.Errorf("%s: got %v, want %v", tt.query, got, tt.want)
		}
	}
}

// TestFilenameBeforeContent checks that a file whose name does not match is
// never opened: an unreadable non-matching file causes no permission error.
func TestFilenameBeforeContent(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores permissions")
	}
	dir := t.TempDir()
	write(t, filepath.Join(dir, "nginx.conf"), []byte("listen 443"))
	locked := filepath.Join(dir, "other.conf")
	write(t, locked, []byte("listen 443"))
	denyAccess(t, locked)
	q, _ := query.Parse(`檔名是 nginx.conf，內容包含 "listen 443"`, time.Now())
	q.Root = dir
	got, rep := run(t, q)
	if !reflect.DeepEqual(got, []string{"nginx.conf"}) || len(rep.PermissionDenied) != 0 {
		t.Errorf("got %v, denied %v; the locked file should not have been opened", got, rep.PermissionDenied)
	}
}

// TestAbsoluteComparatorBoundaries checks before / on or before / after /
// on or after against real files whose mtimes sit exactly on and one
// nanosecond before the calendar boundaries of a day, a month and a year.
func TestAbsoluteComparatorBoundaries(t *testing.T) {
	loc := time.FixedZone("CST", 8*3600)
	now := time.Date(2026, 9, 28, 10, 30, 0, 0, loc)
	at := func(y int, mo time.Month, d int) time.Time { return time.Date(y, mo, d, 0, 0, 0, 0, loc) }
	justBefore := func(t time.Time) time.Time { return t.Add(-mtimeTick) }
	dir := t.TempDir()
	files := map[string]time.Time{
		"2024-12-31-last": justBefore(at(2025, 1, 1)),
		"2025-01-01":      at(2025, 1, 1),
		"2025-12-31-last": justBefore(at(2026, 1, 1)),
		"2026-01-01":      at(2026, 1, 1),
		"0831-last":       justBefore(at(2026, 9, 1)),
		"0901-0000":       at(2026, 9, 1),
		"0901-1200":       at(2026, 9, 1).Add(12 * time.Hour),
		"0901-last":       justBefore(at(2026, 9, 2)),
		"0902-0000":       at(2026, 9, 2),
		"0930-last":       justBefore(at(2026, 10, 1)),
		"1001-0000":       at(2026, 10, 1),
	}
	for n, mt := range files {
		p := filepath.Join(dir, n)
		write(t, p, nil)
		if err := os.Chtimes(p, mt, mt); err != nil {
			t.Fatal(err)
		}
		if info, err := os.Stat(p); err != nil || !info.ModTime().Equal(mt) {
			t.Skipf("file system does not keep nanosecond mtimes (%v)", err)
		}
	}
	// Sorted names: 0831-last 0901-0000 0901-1200 0901-last 0902-0000 0930-last 1001-0000
	//               2024-12-31-last 2025-01-01 2025-12-31-last 2026-01-01
	tests := []struct {
		query string
		want  []string
	}{
		// day
		{"before Sep 1", []string{"0831-last", "2024-12-31-last", "2025-01-01", "2025-12-31-last", "2026-01-01"}},
		{"on or before Sep 1", []string{"0831-last", "0901-0000", "0901-1200", "0901-last", "2024-12-31-last", "2025-01-01", "2025-12-31-last", "2026-01-01"}},
		{"after Sep 1", []string{"0902-0000", "0930-last", "1001-0000"}},
		{"on or after Sep 1", []string{"0901-0000", "0901-1200", "0901-last", "0902-0000", "0930-last", "1001-0000"}},
		{"since Sep 1", []string{"0901-0000", "0901-1200", "0901-last", "0902-0000", "0930-last", "1001-0000"}},
		{"找 2026-09-01 以前的檔案", []string{"0831-last", "2024-12-31-last", "2025-01-01", "2025-12-31-last", "2026-01-01"}},
		{"找 2026-09-01 以後的檔案", []string{"0902-0000", "0930-last", "1001-0000"}},
		// month
		{"before September", []string{"0831-last", "2024-12-31-last", "2025-01-01", "2025-12-31-last", "2026-01-01"}},
		{"after September", []string{"1001-0000"}},
		{"on or before September", []string{"0831-last", "0901-0000", "0901-1200", "0901-last", "0902-0000", "0930-last",
			"2024-12-31-last", "2025-01-01", "2025-12-31-last", "2026-01-01"}},
		{"找 9 月或以後的檔案", []string{"0901-0000", "0901-1200", "0901-last", "0902-0000", "0930-last", "1001-0000"}},
		// year
		{"before 2025", []string{"2024-12-31-last"}},
		{"on or before 2025", []string{"2024-12-31-last", "2025-01-01", "2025-12-31-last"}},
		{"after 2025", []string{"0831-last", "0901-0000", "0901-1200", "0901-last", "0902-0000", "0930-last", "1001-0000", "2026-01-01"}},
		{"找 2025 年或以後的檔案", []string{"0831-last", "0901-0000", "0901-1200", "0901-last", "0902-0000", "0930-last",
			"1001-0000", "2025-01-01", "2025-12-31-last", "2026-01-01"}},
		// OR of two comparators
		{"before Sep 1 or after Sep 30", []string{"0831-last", "1001-0000", "2024-12-31-last", "2025-01-01", "2025-12-31-last", "2026-01-01"}},
	}
	for _, tt := range tests {
		q, err := query.Parse(tt.query, now)
		if err != nil {
			t.Fatalf("Parse(%q): %v", tt.query, err)
		}
		q.Root = dir
		got, _ := run(t, q)
		if !reflect.DeepEqual(got, tt.want) {
			t.Errorf("%s:\n got  %v\n want %v", tt.query, got, tt.want)
		}
	}
}
