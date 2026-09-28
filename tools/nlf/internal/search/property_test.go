package search

import (
	"bytes"
	"fmt"
	"math/rand"
	"os"
	"path"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

// propBase is the reference time of the property fixture.
var propBase = time.Date(2026, 9, 28, 12, 0, 0, 0, time.UTC)

// propFile is what the fixture wrote, for the independent predicates.
type propFile struct {
	size    int64
	mtime   time.Time
	content []byte
}

// propFixture builds a deterministic tree with Unicode, hostile and
// case-variant names, binary files, files over 64 KiB, equal sizes and
// equal mtimes.
func propFixture(t *testing.T) (string, map[string]propFile) {
	t.Helper()
	dir := t.TempDir()
	names := []string{"a.go", "b.GO", "caf\u00e9.txt", "R\u00e9sum\u00e9.TXT", "x y.log", "-rf.log", "tar.tar.gz", ".go", "中文.md",
		"big.bin", "e.txt", "k.txt", "it's.txt", "[abc].txt", "*.txt"}
	// No two names differ only in case or normalization: macOS file systems
	// would alias them. NFC/NFD is covered by TestUnicodeNormalizationMatrix.
	dirs := []string{"", "sub/", "sub/deep/", "vendor/", "lib/Vendor/", "中/", "a b/"}
	tails := []string{"ERROR", "error", "TODO", "Kelvin", "", "\x00bin", "\u212Ak"}
	files := map[string]propFile{}
	i := 0
	for _, d := range dirs {
		for _, n := range names {
			i++
			content := strings.Repeat("z", i*97%3000) + tails[i%len(tails)]
			switch {
			case i%7 == 0:
				content += strings.Repeat("y", chunkSize) + "LATE"
			case i%11 == 0:
				content = strings.Repeat("a", chunkSize-3) + "\u212Ak" // B2 at the read boundary
			case i%13 == 0:
				content = "same size" // equal sizes
			}
			p := filepath.Join(dir, d, n)
			write(t, p, []byte(content))
			mt := propBase.Add(-time.Duration(i%9) * 12 * time.Hour) // equal mtimes
			if err := os.Chtimes(p, mt, mt); err != nil {
				t.Fatal(err)
			}
			files[p] = propFile{int64(len(content)), mt, []byte(content)}
		}
	}
	return dir, files
}

func resultPaths(t *testing.T, q *query.Query) []string {
	t.Helper()
	var out []string
	if _, err := Run(q, func(r Result) { out = append(out, r.Path) }); err != nil {
		t.Fatalf("Run: %v", err)
	}
	slices.Sort(out)
	return out
}

// cloneQuery deep-copies the slices of q that a tightening may change.
func cloneQuery(q *query.Query) *query.Query {
	c := *q
	c.Extensions = slices.Clone(q.Extensions)
	c.Names = slices.Clone(q.Names)
	c.Contents = slices.Clone(q.Contents)
	c.Sizes = slices.Clone(q.Sizes)
	c.ExcludeDirs = slices.Clone(q.ExcludeDirs)
	c.AnyOf = nil
	for _, g := range q.AnyOf {
		g.Contents = slices.Clone(g.Contents)
		g.Times = slices.Clone(g.Times)
		g.Sizes = slices.Clone(g.Sizes)
		g.Names = slices.Clone(g.Names)
		c.AnyOf = append(c.AnyOf, g)
	}
	return &c
}

// Independent predicates, written without the search package's helpers.

func nameHolds(c query.NameCond, base string) bool {
	switch c.Kind {
	case query.NameExact:
		return base == c.Pattern
	case query.NameContains:
		return strings.Contains(base, c.Pattern)
	case query.NamePrefix:
		return strings.HasPrefix(base, c.Pattern)
	case query.NameSuffix:
		return strings.HasSuffix(base, c.Pattern)
	case query.NameGlob:
		ok, err := path.Match(c.Pattern, base)
		return err == nil && ok
	}
	panic("unknown name kind")
}

func sizeHolds(c query.SizeCond, n int64) bool {
	switch c.Op {
	case ">":
		return n > c.Bytes
	case ">=":
		return n >= c.Bytes
	case "<":
		return n < c.Bytes
	case "<=":
		return n <= c.Bytes
	}
	panic("unknown size op " + c.Op)
}

func timeHolds(r query.TimeRange, t time.Time) bool {
	return (r.After.IsZero() || !t.Before(r.After)) && (r.Before.IsZero() || t.Before(r.Before))
}

func contentHolds(term string, content []byte, ic bool) bool {
	if bytes.IndexByte(content[:min(len(content), chunkSize)], 0) >= 0 {
		return false // binary
	}
	if ic {
		return bytes.Contains(bytes.ToLower(content), bytes.ToLower([]byte(term)))
	}
	return bytes.Contains(content, []byte(term))
}

// TestAndIsMonotonic adds exactly one tightening condition to random
// queries: the results may only shrink, and for name, size, time and
// content conditions they must be exactly the old results filtered by an
// independent predicate.
func TestAndIsMonotonic(t *testing.T) {
	dir, files := propFixture(t)
	r := rand.New(rand.NewSource(7))
	pick := func(s []string) string { return s[r.Intn(len(s))] }
	exts := []string{"go", "txt", "log", "gz", "tar.gz", "md", "bin"}
	terms := []string{"ERROR", "error", "TODO", "kelvin", "LATE", "z", "kk", "same"}
	namePats := []string{"a", "*.txt", "caf", ".log", "中", "e", "?.go", "k.txt", "[abc]*", "R\u00e9s", "it's.txt", "*"}
	randName := func() query.NameCond {
		return query.NameCond{Kind: query.NameMatchKind(r.Intn(5)), Pattern: pick(namePats)}
	}
	randSize := func() query.SizeCond {
		return query.SizeCond{Op: pick([]string{">", ">=", "<", "<="}), Bytes: int64(r.Intn(4000))}
	}
	randTime := func() time.Time { return propBase.Add(-time.Duration(r.Intn(120)) * time.Hour) }
	randRange := func() query.TimeRange {
		tr := query.TimeRange{}
		if r.Intn(3) > 0 {
			tr.After = randTime()
		}
		if r.Intn(3) > 0 {
			tr.Before = randTime()
		}
		return tr
	}
	randGroup := func(kind query.CondKind) query.OrGroup {
		g := query.OrGroup{Kind: kind}
		for k := 1 + r.Intn(3); k > 0; k-- {
			switch kind {
			case query.KindContent:
				g.Contents = append(g.Contents, pick(terms))
			case query.KindName:
				g.Names = append(g.Names, randName())
			case query.KindSize:
				g.Sizes = append(g.Sizes, randSize())
			case query.KindTime:
				g.Times = append(g.Times, randRange())
			}
		}
		return g
	}
	kinds := []query.CondKind{query.KindContent, query.KindName, query.KindSize, query.KindTime}

	// tighten adds one condition to q and returns its name and, for
	// conditions with an independent predicate, that predicate.
	tighten := func(q *query.Query) (string, func(p string) bool) {
		for {
			switch r.Intn(10) {
			case 0:
				switch {
				case len(q.Extensions) == 0:
					q.Extensions = []string{pick(exts)}
				case len(q.Extensions) >= 2:
					keep := 1 + r.Intn(len(q.Extensions)-1)
					q.Extensions = q.Extensions[:keep] // any-of: fewer alternatives
				default:
					continue // appending to a non-empty any-of list would widen
				}
				return "extension", nil
			case 1:
				c := randName()
				q.Names = append(q.Names, c)
				return fmt.Sprintf("name %+v", c), func(p string) bool { return nameHolds(c, filepath.Base(p)) }
			case 2:
				c, ic := pick(terms), q.IgnoreCase
				q.Contents = append(q.Contents, c)
				return "content " + c, func(p string) bool { return contentHolds(c, files[p].content, ic) }
			case 3:
				c := randSize()
				q.Sizes = append(q.Sizes, c)
				return fmt.Sprintf("size %+v", c), func(p string) bool { return sizeHolds(c, files[p].size) }
			case 4:
				tm := randTime()
				if r.Intn(2) == 0 {
					if q.ModifiedAfter.IsZero() || tm.After(q.ModifiedAfter) {
						q.ModifiedAfter = tm
					}
					return "after " + tm.String(), func(p string) bool { return !files[p].mtime.Before(tm) }
				}
				if q.ModifiedBefore.IsZero() || tm.Before(q.ModifiedBefore) {
					q.ModifiedBefore = tm
				}
				return "before " + tm.String(), func(p string) bool { return files[p].mtime.Before(tm) }
			case 5, 6:
				kind := query.KindTime
				if r.Intn(2) == 0 {
					kind = kinds[r.Intn(3)]
				}
				g, ic := randGroup(kind), q.IgnoreCase
				q.AnyOf = append(q.AnyOf, g)
				return fmt.Sprintf("group %+v", g), func(p string) bool {
					f := files[p]
					return slices.ContainsFunc(g.Contents, func(c string) bool { return contentHolds(c, f.content, ic) }) ||
						slices.ContainsFunc(g.Names, func(c query.NameCond) bool { return nameHolds(c, filepath.Base(p)) }) ||
						slices.ContainsFunc(g.Sizes, func(c query.SizeCond) bool { return sizeHolds(c, f.size) }) ||
						slices.ContainsFunc(g.Times, func(tr query.TimeRange) bool { return timeHolds(tr, f.mtime) })
				}
			case 7:
				i := slices.IndexFunc(q.AnyOf, func(g query.OrGroup) bool {
					return len(g.Contents)+len(g.Names)+len(g.Sizes)+len(g.Times) >= 2
				})
				if i < 0 {
					continue
				}
				g := &q.AnyOf[i]
				switch g.Kind {
				case query.KindContent:
					g.Contents = g.Contents[:len(g.Contents)-1]
				case query.KindName:
					g.Names = g.Names[1:]
				case query.KindSize:
					g.Sizes = g.Sizes[:len(g.Sizes)-1]
				case query.KindTime:
					g.Times = g.Times[1:]
				}
				return "drop alternative", nil
			case 8:
				q.ExcludeDirs = append(q.ExcludeDirs, pick([]string{"vendor", "sub", "deep", "./sub/deep", "中", "x", "a b", "./a b"}))
				return "exclude", nil
			default:
				if r.Intn(2) == 0 {
					q.MinDepth++
					return "min depth", nil
				}
				if !q.HasMaxDepth || q.MaxDepth > 0 {
					if q.HasMaxDepth {
						q.MaxDepth--
					} else {
						q.MaxDepth, q.HasMaxDepth = 1+r.Intn(3), true
					}
					return "max depth", nil
				}
			}
		}
	}

	for it := 0; it < 400; it++ {
		q := &query.Query{Root: dir, IgnoreCase: r.Intn(3) == 0, Sort: query.SortOrder(r.Intn(3))}
		for k := r.Intn(3); k > 0; k-- {
			q.Extensions = append(q.Extensions, pick(exts))
		}
		for k := r.Intn(3); k > 0; k-- {
			q2 := cloneQuery(q)
			tighten(q2)
			q = q2
		}
		if r.Intn(3) == 0 {
			q.AnyOf = append(q.AnyOf, randGroup(kinds[r.Intn(4)]))
		}
		norm, err := NormalizeExcludes(dir, q.ExcludeDirs)
		if err != nil {
			t.Fatal(err)
		}
		q.ExcludeDirs = norm
		before := resultPaths(t, q)
		q2 := cloneQuery(q)
		what, holds := tighten(q2)
		if q2.ExcludeDirs, err = NormalizeExcludes(dir, q2.ExcludeDirs); err != nil {
			t.Fatal(err)
		}
		after := resultPaths(t, q2)
		for _, p := range after {
			if !slices.Contains(before, p) {
				t.Fatalf("iteration %d: adding %s widened the results with %s\nQ  %+v\nQ2 %+v", it, what, p, q, q2)
			}
		}
		if holds != nil {
			want := slices.DeleteFunc(slices.Clone(before), func(p string) bool { return !holds(p) })
			if !slices.Equal(after, want) {
				t.Fatalf("iteration %d: adding %s:\n got  %q\n want %q\nQ  %+v", it, what, after, want, q)
			}
		}
		if !reflect.DeepEqual(cloneQuery(q), q) {
			t.Fatal("cloneQuery is not a copy")
		}
	}

	// The same through the parser: appending "，而且 <condition>" to a query
	// keeps exactly the old results that satisfy the condition.
	bases := []string{"go files", "txt 檔", "包含 \"z\"", "檔名包含 a", "今天", "最近 3 天", "larger than 1KB", "排除 vendor 目錄",
		"找 log 或 md 檔", "包含 \"ERROR\" 或 \"TODO\"", "大於 1KB 或小於 100 bytes", "今天或昨天", "files", "不分大小寫 包含 \"error\"",
		"檔名符合 \"*.txt\"", "最大的檔案"}
	extras := []string{"larger than 1KB", `包含 "ERROR"`, "今天", "檔名包含 a", "txt 檔"}
	today := query.TimeRange{After: time.Date(2026, 9, 28, 0, 0, 0, 0, time.UTC), Before: time.Date(2026, 9, 29, 0, 0, 0, 0, time.UTC)}
	extraHolds := map[string]func(p string, ic bool) bool{
		"larger than 1KB": func(p string, _ bool) bool { return files[p].size > 1024 },
		`包含 "ERROR"`:      func(p string, ic bool) bool { return contentHolds("ERROR", files[p].content, ic) },
		"今天":              func(p string, _ bool) bool { return timeHolds(today, files[p].mtime) },
		"檔名包含 a":          func(p string, _ bool) bool { return strings.Contains(filepath.Base(p), "a") },
		"txt 檔": func(p string, _ bool) bool {
			b := strings.ToLower(filepath.Base(p))
			return strings.HasSuffix(b, ".txt") && len(b) > len(".txt")
		},
	}
	pairs := 0
	for _, b := range bases {
		for _, c := range extras {
			q1, err1 := query.Parse(b, propBase)
			if c == "txt 檔" && (err1 != nil || len(q1.Extensions) > 0) {
				continue // a second extension list would be any-of
			}
			q2, err2 := query.Parse(b+"，而且 "+c, propBase)
			if err1 != nil || err2 != nil || q1.Limit != 0 {
				continue
			}
			q1.Root, q2.Root = dir, dir
			pairs++
			before, after := resultPaths(t, q1), resultPaths(t, q2)
			for _, p := range after {
				if !slices.Contains(before, p) {
					t.Errorf("%q widened %q with %s", b+"，而且 "+c, b, p)
				}
			}
			want := slices.DeleteFunc(slices.Clone(before), func(p string) bool { return !extraHolds[c](p, q1.IgnoreCase) })
			if !slices.Equal(after, want) {
				t.Errorf("%q:\n got  %q\n want %q", b+"，而且 "+c, after, want)
			}
		}
	}
	if pairs < 50 {
		t.Errorf("only %d parsed pairs were compared", pairs)
	}
}

// TestRunDeterminism runs the same queries 20 times: results and their
// order must be identical every time.
func TestRunDeterminism(t *testing.T) {
	dir, _ := propFixture(t)
	queries := map[string]query.Query{
		"unsorted limit":      {Limit: 7},
		"largest 5 with ties": {Sort: query.SortLargest, Limit: 5, Contents: []string{"same"}},
		"largest 5":           {Sort: query.SortLargest, Limit: 5},
		"newest 5 equal":      {Sort: query.SortNewest, Limit: 5},
		"newest all":          {Sort: query.SortNewest},
		"unicode names":       {Names: []query.NameCond{{Kind: query.NameContains, Pattern: "caf"}}},
		"hostile names":       {Names: []query.NameCond{{Kind: query.NameGlob, Pattern: "*[*'[]*"}}},
		"depth 1":             {MaxDepth: 1, HasMaxDepth: true},
		"depth 2":             {MinDepth: 2, MaxDepth: 2, HasMaxDepth: true},
		"excludes":            {ExcludeDirs: []string{"vendor", "./sub/deep", "a b"}},
		"content":             {Contents: []string{"kk"}, IgnoreCase: true},
		"content or":          {AnyOf: []query.OrGroup{{Kind: query.KindContent, Contents: []string{"ERROR", "LATE"}}}},
	}
	for name, q := range queries {
		q.Root = dir
		first, _ := runResults(t, q)
		if len(first) == 0 {
			t.Errorf("%s: no results; the check would be empty", name)
		}
		for i := 0; i < 20; i++ {
			if got, _ := runResults(t, q); !reflect.DeepEqual(got, first) {
				t.Fatalf("%s: run %d differs:\n got  %v\n want %v", name, i, got, first)
			}
		}
	}
}
