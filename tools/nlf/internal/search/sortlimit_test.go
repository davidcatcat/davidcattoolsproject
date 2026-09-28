package search

import (
	"math"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

// runResults runs q and returns the emitted Results in order.
func runResults(t *testing.T, q query.Query) ([]Result, Report) {
	t.Helper()
	var got []Result
	rep, err := Run(&q, func(r Result) { got = append(got, r) })
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	return got, rep
}

var sortBase = time.Date(2026, 9, 1, 12, 0, 0, 0, time.UTC)

// put writes a file of the given size and whole-second mtime offset.
func put(t *testing.T, path string, size int, sec int) {
	t.Helper()
	write(t, path, []byte(strings.Repeat("x", size)))
	mt := sortBase.Add(time.Duration(sec) * time.Second)
	if err := os.Chtimes(path, mt, mt); err != nil {
		t.Fatal(err)
	}
}

// sortTree builds a tree with awkward names and heavy ties in both size
// and mtime. It returns the root and the number of files.
func sortTree(t *testing.T) (string, int) {
	dir := t.TempDir()
	files := []struct {
		path      string
		size, sec int
	}{
		{"a/x", 10, 5}, {"a-b/x", 10, 5}, {"-dash/y", 10, 5}, {"-dash/-z", 3, 9},
		{"日本 語/檔 案.txt", 10, 5}, {"日本 語/b", 20, 1}, {"sp ace/c d", 20, 9},
		{"sp ace/e", 0, 0}, {"top", 10, 7}, {"a/deep/er/f", 20, 7}, {"a/deep/g", 3, 5},
		{"a-b/h", 0, 9}, {"z", 10, 5},
	}
	for _, f := range files {
		put(t, filepath.Join(dir, f.path), f.size, f.sec)
	}
	return dir, len(files)
}

// checkLimitPrefix checks that every limit gives the prefix of the unlimited
// sorted run, with the same report.
func checkLimitPrefix(t *testing.T, name string, q query.Query, limits []int) []Result {
	t.Helper()
	q.Limit = 0
	all, allRep := runResults(t, q)
	if !slices.IsSortedFunc(all, refCompare(q.Sort)) {
		t.Errorf("%s: unlimited run is not in (key, path) order: %v", name, all)
	}
	for _, n := range limits {
		q.Limit = n
		got, rep := runResults(t, q)
		if want := all[:min(n, len(all))]; !slices.Equal(got, want) {
			t.Errorf("%s limit %d:\n got %v\nwant %v", name, n, got, want)
		}
		if !reflect.DeepEqual(rep, allRep) {
			t.Errorf("%s limit %d: report %+v, want %+v", name, n, rep, allRep)
		}
	}
	return all
}

func TestSortedLimitMatchesUnlimitedPrefix(t *testing.T) {
	dir, m := sortTree(t)
	var limits []int
	for n := 1; n <= m+2; n++ {
		limits = append(limits, n)
	}
	limits = append(limits, math.MaxInt32)
	for _, order := range orders {
		all := checkLimitPrefix(t, "tree", query.Query{Root: dir, Sort: order}, limits)
		if len(all) != m {
			t.Errorf("order %v: %d results, want %d", order, len(all), m)
		}
	}
}

func TestSortedLimitTiePathOrder(t *testing.T) {
	dir := t.TempDir()
	put(t, filepath.Join(dir, "a", "x"), 10, 3)
	put(t, filepath.Join(dir, "a-b", "x"), 10, 3)
	walk, _ := run(t, &query.Query{Root: dir})
	if want := []string{"a/x", "a-b/x"}; !slices.Equal(slash(walk), want) {
		t.Fatalf("walk order %v, want %v", walk, want)
	}
	for _, order := range orders {
		got, _ := run(t, &query.Query{Root: dir, Sort: order, Limit: 1})
		if want := []string{"a-b/x"}; !slices.Equal(slash(got), want) {
			t.Errorf("order %v limit 1: got %v, want %v", order, got, want)
		}
		got, _ = run(t, &query.Query{Root: dir, Sort: order, Limit: 2})
		if want := []string{"a-b/x", "a/x"}; !slices.Equal(slash(got), want) {
			t.Errorf("order %v limit 2: got %v, want %v", order, got, want)
		}
	}
}

func TestSortedLimitCombos(t *testing.T) {
	dir, _ := sortTree(t)
	write(t, filepath.Join(dir, "a", "needle1"), []byte("has NEEDLE"))
	write(t, filepath.Join(dir, "a-b", "needle2"), []byte("NEEDLE and more text"))
	write(t, filepath.Join(dir, "sp ace", "needle3"), []byte("xNEEDLEx"))
	for _, p := range []string{"a/needle1", "a-b/needle2", "sp ace/needle3"} {
		mt := sortBase.Add(3 * time.Second)
		if err := os.Chtimes(filepath.Join(dir, p), mt, mt); err != nil {
			t.Fatal(err)
		}
	}
	link := filepath.Join(t.TempDir(), "link")
	if err := os.Symlink(dir, link); err != nil {
		t.Fatal(err)
	}
	lim := []int{1, 2, 3, math.MaxInt32}
	for _, c := range []struct {
		name string
		q    query.Query
	}{
		{"content+largest", query.Query{Root: dir, Sort: query.SortLargest, Contents: []string{"NEEDLE"}}},
		{"size+newest", query.Query{Root: dir, Sort: query.SortNewest, Sizes: []query.SizeCond{{Op: ">=", Bytes: 10}}}},
		{"after+largest", query.Query{Root: dir, Sort: query.SortLargest, ModifiedAfter: sortBase.Add(5 * time.Second)}},
		{"exclude+largest", query.Query{Root: dir, Sort: query.SortLargest, ExcludeDirs: []string{"sp ace", "日本 語"}}},
		{"maxdepth1+largest", query.Query{Root: dir, Sort: query.SortLargest, MaxDepth: 1, HasMaxDepth: true}},
		{"mindepth2+newest", query.Query{Root: dir, Sort: query.SortNewest, MinDepth: 2}},
		{"symlink root+largest", query.Query{Root: link, Sort: query.SortLargest}},
		{"file root+largest", query.Query{Root: filepath.Join(dir, "top"), Sort: query.SortLargest}},
	} {
		if all := checkLimitPrefix(t, c.name, c.q, lim); len(all) == 0 {
			t.Errorf("%s: no results; the case checks nothing", c.name)
		}
	}
}

func TestSortedLimitPermissionDenied(t *testing.T) {
	dir, _ := sortTree(t)
	locked := filepath.Join(dir, "a-b")
	chmod(t, locked, 0, 0o755)
	root := isRoot(t)
	all := checkLimitPrefix(t, "denied+largest", query.Query{Root: dir, Sort: query.SortLargest}, []int{1, 2, math.MaxInt32})
	_, rep := runResults(t, query.Query{Root: dir, Sort: query.SortLargest, Limit: 1})
	if root {
		if !emptyReport(rep) {
			t.Errorf("as root: report %+v, want empty", rep)
		}
		return
	}
	if want := []string{locked}; !slices.Equal(rep.PermissionDenied, want) || len(rep.OtherErrors) != 0 {
		t.Errorf("report %+v, want PermissionDenied %v", rep, want)
	}
	if len(all) == 0 || slices.ContainsFunc(all, func(r Result) bool { return strings.HasPrefix(r.Path, locked+string(filepath.Separator)) }) {
		t.Errorf("results %v: want some, none below %s", all, locked)
	}
}
