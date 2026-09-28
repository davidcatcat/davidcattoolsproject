package search

import (
	"cmp"
	"fmt"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

// oracle is the pre-heap algorithm: sort everything, then cut to n.
func oracle(rs []Result, order query.SortOrder, n int) []Result {
	c := slices.Clone(rs)
	sortResults(c, order)
	if n < len(c) {
		c = c[:n]
	}
	return c
}

// viaTopN feeds rs to a topN in input order and returns its output.
func viaTopN(rs []Result, order query.SortOrder, n int) []Result {
	t := &topN{order: order, n: n, h: make([]Result, 0, min(n, topNInitialCap))}
	for _, r := range rs {
		t.add(r)
	}
	return t.results()
}

// refCompare is an ordering written independently of better (integer
// nanoseconds, cmp.Compare), so a mistake shared by sortResults and topN
// still shows up.
func refCompare(order query.SortOrder) func(a, b Result) int {
	return func(a, b Result) int {
		switch order {
		case query.SortLargest:
			if c := cmp.Compare(b.Size, a.Size); c != 0 {
				return c
			}
		case query.SortNewest:
			if c := cmp.Compare(b.ModTime.UnixNano(), a.ModTime.UnixNano()); c != 0 {
				return c
			}
		}
		return strings.Compare(a.Path, b.Path)
	}
}

func reference(rs []Result, order query.SortOrder, n int) []Result {
	c := slices.Clone(rs)
	slices.SortFunc(c, refCompare(order))
	if n < len(c) {
		c = c[:n]
	}
	return c
}

var (
	pathPieces = []string{"a", "A", "a-b", "a/", "-x", " sp", "日本", "\u00e9", "e\u0301", "b", "ab", "."}
	genSizes   = []int64{0, 1, 2, 3, 1 << 40}
	genNanos   = []int64{0, 1, 999999999}
	genLocs    = []*time.Location{nil, time.UTC, time.FixedZone("X", 3600)}
)

const genBase = 1_700_000_000

// genResults returns m results with unique paths and many ties. With
// identical set, every result has the same size and mtime (the instant is
// the same, the Location may differ) so only the path decides.
func genResults(rng *rand.Rand, m int, identical bool) []Result {
	seen := make(map[string]bool, m)
	rs := make([]Result, 0, m)
	for len(rs) < m {
		var sb strings.Builder
		for k := rng.Intn(4) + 1; k > 0; k-- {
			sb.WriteString(pathPieces[rng.Intn(len(pathPieces))])
		}
		p := sb.String()
		if seen[p] {
			continue
		}
		seen[p] = true
		size, sec, ns := genSizes[rng.Intn(len(genSizes))], int64(rng.Intn(4)), genNanos[rng.Intn(len(genNanos))]
		if identical {
			size, sec, ns = 7, 2, 1
		}
		mt := time.Unix(genBase+sec, ns)
		if loc := genLocs[rng.Intn(len(genLocs))]; loc != nil {
			mt = mt.In(loc)
		}
		rs = append(rs, Result{Path: p, Size: size, ModTime: mt})
	}
	return rs
}

func topNSizes(m int) []int {
	var ns []int
	for _, n := range []int{1, 2, 3, 5, m - 1, m, m + 1, 2*m + 7, math.MaxInt32} {
		if n >= 1 && !slices.Contains(ns, n) {
			ns = append(ns, n)
		}
	}
	return ns
}

var orders = []query.SortOrder{query.SortLargest, query.SortNewest}

func TestTopNMatchesFullSort(t *testing.T) {
	rng := rand.New(rand.NewSource(42))
	// Many small inputs, plus a few large ones (large M is slow under -race).
	ms := []int{0, 1, 2, 5, 50, 500}
	for it := 0; it < 305; it++ {
		m := ms[it%len(ms)]
		if it >= 300 {
			m = 3000
		}
		identical := it%5 == 4
		rs := genResults(rng, m, identical)
		for _, order := range orders {
			full := oracle(rs, order, m)
			if ref := reference(rs, order, m); !slices.Equal(full, ref) {
				t.Fatalf("it %d m %d order %v identical %v: sortResults disagrees with the reference ordering", it, m, order, identical)
			}
			for _, n := range topNSizes(m) {
				want := full[:min(n, m)]
				if got := viaTopN(rs, order, n); !slices.Equal(got, want) {
					t.Fatalf("it %d m %d order %v n %d identical %v:\n got %v\nwant %v", it, m, order, n, identical, got, want)
				}
			}
		}
	}
}

func TestTopNOrderIndependent(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	rs := genResults(rng, 400, false)
	for _, order := range orders {
		for _, n := range []int{1, 3, 17, 399, 400, 401} {
			want := oracle(rs, order, n)
			in := slices.Clone(rs)
			for i := 0; i < 20; i++ {
				rng.Shuffle(len(in), func(a, b int) { in[a], in[b] = in[b], in[a] })
				if got := viaTopN(in, order, n); !slices.Equal(got, want) {
					t.Fatalf("order %v n %d shuffle %d:\n got %v\nwant %v", order, n, i, got, want)
				}
			}
		}
	}
}

func TestTopNDeterministic(t *testing.T) {
	for _, order := range orders {
		a := viaTopN(genResults(rand.New(rand.NewSource(99)), 1000, false), order, 25)
		b := viaTopN(genResults(rand.New(rand.NewSource(99)), 1000, false), order, 25)
		if !slices.Equal(a, b) || len(a) != 25 {
			t.Fatalf("order %v: two runs with the same seed differ (%d, %d results)", order, len(a), len(b))
		}
	}
}

func TestTopNNeverExceedsN(t *testing.T) {
	rng := rand.New(rand.NewSource(3))
	rs := genResults(rng, 200, false)
	for _, order := range orders {
		for _, n := range []int{1, 2, 7, 199, 200, 201} {
			tp := &topN{order: order, n: n, h: make([]Result, 0, min(n, topNInitialCap))}
			for i, r := range rs {
				tp.add(r)
				if len(tp.h) > n {
					t.Fatalf("order %v n %d: %d results kept after %d adds", order, n, len(tp.h), i+1)
				}
				if want := min(i+1, n); len(tp.h) != want {
					t.Fatalf("order %v n %d: %d results kept after %d adds, want %d", order, n, len(tp.h), i+1, want)
				}
			}
		}
		tp := newTopNFor(&query.Query{Sort: order, Limit: math.MaxInt32})
		if tp == nil {
			t.Fatalf("order %v MaxInt32: newTopNFor returned nil", order)
		}
		for _, r := range rs[:10] {
			tp.add(r)
		}
		if len(tp.h) != 10 || cap(tp.h) > topNInitialCap {
			t.Fatalf("order %v MaxInt32: len %d cap %d, want len 10 and cap <= %d", order, len(tp.h), cap(tp.h), topNInitialCap)
		}
	}
}

// TestTopNTieCutoff checks the cutoff inside a group of equal keys. The
// input is in WalkDir order, where "a/x" comes before "a-b/x", although
// "a-b/x" sorts first ('-' is 0x2d, '/' is 0x2f).
func TestTopNTieCutoff(t *testing.T) {
	tie := time.Unix(genBase, 5)
	locs := []*time.Location{time.Local, time.UTC, time.FixedZone("X", 3600)}
	mk := func(order query.SortOrder) []Result {
		var rs []Result
		for i, p := range []string{"a", "a/x", "a-b/x", "m", "q", "x", "z"} {
			key := int64(10)
			switch p {
			case "z":
				key = 20
			case "q":
				key = 5
			}
			// Equal instants get different Locations, so only Equal sees the tie.
			r := Result{Path: p, Size: 1, ModTime: tie.In(locs[i%len(locs)])}
			if order == query.SortLargest {
				r.Size = key
			} else {
				r.ModTime = tie.Add(time.Duration(key) * time.Second).In(locs[i%len(locs)])
			}
			rs = append(rs, r)
		}
		return rs
	}
	paths := func(rs []Result) []string {
		var ps []string
		for _, r := range rs {
			ps = append(ps, r.Path)
		}
		return ps
	}
	for _, order := range orders {
		rs := mk(order)
		for _, c := range []struct {
			n    int
			want []string
		}{
			{1, []string{"z"}},
			{2, []string{"z", "a"}},
			{3, []string{"z", "a", "a-b/x"}},
			{4, []string{"z", "a", "a-b/x", "a/x"}},
			{6, []string{"z", "a", "a-b/x", "a/x", "m", "x"}},
			{7, []string{"z", "a", "a-b/x", "a/x", "m", "x", "q"}},
			{8, []string{"z", "a", "a-b/x", "a/x", "m", "x", "q"}},
		} {
			tp := &topN{order: order, n: c.n, h: make([]Result, 0, c.n)}
			for _, r := range rs {
				tp.add(r)
			}
			// The root is the worst kept result.
			for _, r := range tp.h[1:] {
				if better(tp.h[0], r, order) {
					t.Errorf("order %v n %d: root %q is better than kept %q", order, c.n, tp.h[0].Path, r.Path)
				}
			}
			if want := c.want[len(c.want)-1]; tp.h[0].Path != want {
				t.Errorf("order %v n %d: root %q, want the worst kept %q", order, c.n, tp.h[0].Path, want)
			}
			if got := paths(tp.results()); !slices.Equal(got, c.want) {
				t.Errorf("order %v n %d: got %q, want %q", order, c.n, got, c.want)
			}
		}
	}
}

func TestTopNEmpty(t *testing.T) {
	for _, order := range orders {
		for _, n := range []int{1, 5, math.MaxInt32} {
			tp := newTopNFor(&query.Query{Sort: order, Limit: n})
			if tp == nil {
				t.Fatalf("order %v n %d: newTopNFor returned nil", order, n)
			}
			if got := tp.results(); len(got) != 0 {
				t.Errorf("order %v n %d: got %v, want nothing", order, n, got)
			}
		}
	}
}

func TestNewTopNFor(t *testing.T) {
	for _, q := range []query.Query{
		{Sort: query.SortNone, Limit: 5},
		{Sort: query.SortNone},
		{Sort: query.SortLargest},
		{Sort: query.SortNewest},
	} {
		if tp := newTopNFor(&q); tp != nil {
			t.Errorf("newTopNFor(sort %v, limit %d) = %+v, want nil", q.Sort, q.Limit, tp)
		}
	}
	for _, order := range orders {
		tp := newTopNFor(&query.Query{Sort: order, Limit: 5})
		if tp == nil || tp.n != 5 || tp.order != order || len(tp.h) != 0 || cap(tp.h) != 5 {
			t.Fatalf("newTopNFor(sort %v, limit 5) = %+v", order, tp)
		}
		tp = newTopNFor(&query.Query{Sort: order, Limit: math.MaxInt32})
		if tp == nil || tp.n != math.MaxInt32 || cap(tp.h) > topNInitialCap {
			t.Fatalf("newTopNFor(sort %v, limit MaxInt32) = %v, want n MaxInt32 and cap <= %d", order, tp != nil, topNInitialCap)
		}
	}
}

// TestModTimeHasNoMonotonicReading backs using == on Results: mtimes from
// the file system carry no monotonic clock reading.
func TestModTimeHasNoMonotonicReading(t *testing.T) {
	p := filepath.Join(t.TempDir(), "f")
	write(t, p, []byte("x"))
	info, err := os.Stat(p)
	if err != nil {
		t.Fatal(err)
	}
	if mt := info.ModTime(); mt != mt.Round(0) {
		t.Errorf("ModTime %v has a monotonic reading", mt)
	}
}

func benchResults() []Result {
	rng := rand.New(rand.NewSource(1))
	rs := make([]Result, 200_000)
	for i := range rs {
		rs[i] = Result{
			Path:    fmt.Sprintf("d%03d/f%06d", i%200, i),
			Size:    int64(rng.Intn(1000)),
			ModTime: time.Unix(genBase+int64(rng.Intn(1000)), 0),
		}
	}
	return rs
}

func BenchmarkTopN(b *testing.B) {
	rs := benchResults()
	for _, c := range []struct {
		name  string
		order query.SortOrder
		n     int
	}{
		{"largest-5", query.SortLargest, 5},
		{"newest-5", query.SortNewest, 5},
		{"largest-1000", query.SortLargest, 1000},
	} {
		b.Run(c.name, func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				tp := newTopNFor(&query.Query{Sort: c.order, Limit: c.n})
				for _, r := range rs {
					tp.add(r)
				}
				if len(tp.results()) != c.n {
					b.Fatal("wrong count")
				}
			}
		})
	}
}

func BenchmarkFullSort(b *testing.B) {
	rs := benchResults()
	for _, c := range []struct {
		name  string
		order query.SortOrder
		n     int
	}{
		{"largest-5", query.SortLargest, 5},
		{"newest-5", query.SortNewest, 5},
	} {
		b.Run(c.name, func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				var collected []Result
				for _, r := range rs {
					collected = append(collected, r)
				}
				sortResults(collected, c.order)
				if len(collected) > c.n {
					collected = collected[:c.n]
				}
				if len(collected) != c.n {
					b.Fatal("wrong count")
				}
			}
		})
	}
}
