// Package search walks a directory tree read-only and returns files that
// match a query.Query.
package search

import (
	"bytes"
	"container/heap"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

// Effects describes what Run does to the system. It backs the safety
// summary shown by nlf --confirm; TestProductionCodeIsReadOnly checks the
// source so this declaration cannot silently drift from the code.
type Effects struct {
	ReadFiles, ModifyFiles, CreateFiles, DeleteFiles, ExecuteShell, Sudo bool
}

// RunEffects returns the effects of Run: it reads directories and files and
// does nothing else.
func RunEffects() Effects { return Effects{ReadFiles: true} }

// Risk classifies e as "DESTRUCTIVE", "WRITE" or "READ-ONLY".
func (e Effects) Risk() string {
	switch {
	case e.DeleteFiles:
		return "DESTRUCTIVE"
	case e.ModifyFiles || e.CreateFiles || e.ExecuteShell || e.Sudo:
		return "WRITE"
	}
	return "READ-ONLY"
}

// Result is one matching regular file.
type Result struct {
	Path    string
	Size    int64
	ModTime time.Time
}

// Report summarizes problems encountered while walking.
type Report struct {
	PermissionDenied []string // paths skipped because access was denied
	OtherErrors      []error  // other unreadable paths (skipped)
}

// Run searches q.Root (or ".") and calls emit for each match. With a sort
// order, results are emitted after the full walk; with a sort order and a
// limit, only the best Limit results are kept during the walk. Nothing is
// ever written.
func Run(q *query.Query, emit func(Result)) (Report, error) {
	root := q.Root
	if root == "" {
		root = "."
	}
	var rep Report
	info, err := os.Stat(root)
	if err != nil {
		return rep, err
	}
	// Follow a symlinked root such as macOS /tmp; links below it are not followed.
	if li, err := os.Lstat(root); err == nil && li.Mode()&fs.ModeSymlink != 0 && info.IsDir() {
		root += string(filepath.Separator)
	}
	depthOf := depthFunc(root)

	ex, err := newExcluder(root, q.ExcludeDirs)
	if err != nil {
		return rep, err
	}

	var collected []Result
	top := newTopNFor(q)
	count := 0
	walkErr := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			if path == root {
				return err
			}
			rep.record(path, err)
			if d != nil && d.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		depth := depthOf(path)
		if d.IsDir() {
			// Returning SkipDir here comes before WalkDir reads the directory, so
			// nothing below the maximum depth is listed, opened or reported
			// (like find -maxdepth). This includes the root itself for --max-depth 0.
			if q.HasMaxDepth && depth >= q.MaxDepth {
				return fs.SkipDir
			}
			if path != root && ex.match(root, path, d) {
				return fs.SkipDir
			}
			return nil
		}
		// Directories at the maximum depth were not read, so depth <= MaxDepth
		// here. Files above the minimum depth are skipped before any Stat or read;
		// shallower directories are still walked, and excluded ones still pruned.
		if depth < q.MinDepth {
			return nil
		}
		// Cheapest checks first: extension and base name need no Stat and no read.
		if !d.Type().IsRegular() || !matchExt(d.Name(), q.Extensions) || !matchNames(d.Name(), q) {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			rep.record(path, err)
			return nil
		}
		// Size and mtime are read once; every AND condition and OR group is
		// evaluated against those values.
		size, mtime := info.Size(), info.ModTime()
		if !matchSize(size, q.Sizes) || !matchTime(mtime, q) || !matchGroups(size, mtime, q.AnyOf) {
			return nil
		}
		if hasContent(q) {
			ok, err := matchContent(path, q)
			if err != nil {
				rep.record(path, err)
				return nil
			}
			if !ok {
				return nil
			}
		}
		r := Result{Path: path, Size: info.Size(), ModTime: info.ModTime()}
		if q.Sort != query.SortNone {
			if top != nil {
				top.add(r)
			} else {
				collected = append(collected, r)
			}
			return nil
		}
		emit(r)
		count++
		if q.Limit > 0 && count >= q.Limit {
			return fs.SkipAll
		}
		return nil
	})
	if walkErr != nil {
		return rep, walkErr
	}
	if q.Sort != query.SortNone {
		if top != nil {
			collected = top.results()
		} else {
			sortResults(collected, q.Sort) // no limit: top handles Limit > 0
		}
		for _, r := range collected {
			emit(r)
		}
	}
	return rep, nil
}

// depthFunc returns a function giving the depth of a path WalkDir passes
// for root: root itself is 0, its entries 1. WalkDir builds child paths
// with filepath.Join, so they start with filepath.Clean(root) + separator,
// except that Clean(".") children have no prefix and "/" is its own prefix.
func depthFunc(root string) func(path string) int {
	sep := string(filepath.Separator)
	base := filepath.Clean(root)
	prefix := base + sep
	switch {
	case base == ".":
		prefix = ""
	case strings.HasSuffix(base, sep):
		prefix = base
	}
	n := strings.Count(prefix, sep)
	return func(path string) int {
		if path == root {
			return 0
		}
		return strings.Count(path, sep) - n + 1
	}
}

func (r *Report) record(path string, err error) {
	if errors.Is(err, fs.ErrPermission) {
		r.PermissionDenied = append(r.PermissionDenied, path)
		return
	}
	r.OtherErrors = append(r.OtherErrors, err)
}

// ExcludeError reports an exclude path that cannot apply to the search root.
type ExcludeError struct {
	Path string
	Self bool // the path is the search root itself (otherwise it is outside it)
}

func (e *ExcludeError) Error() string {
	if e.Self {
		return fmt.Sprintf("exclude path %q is the search location itself", e.Path)
	}
	return fmt.Sprintf("exclude path %q is outside the search location", e.Path)
}

// NormalizeExcludes rewrites exclude paths (entries containing "/") relative
// to root, e.g. "./src/gen", "src/gen/" and "<abs root>/src/gen" all become
// "./src/gen". Names without "/" are returned unchanged. A "~" prefix must
// already be expanded. Paths outside root, or root itself, are an error so an
// exclusion never silently does nothing.
func NormalizeExcludes(root string, dirs []string) ([]string, error) {
	if root == "" {
		root = "."
	}
	var out []string
	for _, d := range dirs {
		if !strings.Contains(d, "/") && !strings.ContainsRune(d, filepath.Separator) {
			out = append(out, d)
			continue
		}
		p := filepath.FromSlash(d)
		if filepath.IsAbs(p) {
			rel, ok := relUnder(root, p)
			if !ok {
				return nil, &ExcludeError{Path: d}
			}
			p = rel
		}
		p = filepath.Clean(p)
		if p == "." {
			return nil, &ExcludeError{Path: d, Self: true}
		}
		if p == ".." || strings.HasPrefix(p, ".."+string(filepath.Separator)) {
			return nil, &ExcludeError{Path: d}
		}
		out = append(out, "./"+filepath.ToSlash(p))
	}
	return out, nil
}

// relUnder returns abs relative to root when abs lies inside root, trying
// the paths as given first and then with symlinks resolved (/tmp vs /private/tmp).
func relUnder(root, abs string) (string, bool) {
	absRoot, err := filepath.Abs(root)
	if err != nil {
		return "", false
	}
	inside := func(r string) bool {
		return r != ".." && !strings.HasPrefix(r, ".."+string(filepath.Separator))
	}
	if rel, err := filepath.Rel(absRoot, abs); err == nil && inside(rel) {
		return rel, true
	}
	rr, err1 := filepath.EvalSymlinks(absRoot)
	ra, err2 := filepath.EvalSymlinks(abs)
	if err1 == nil && err2 == nil {
		if rel, err := filepath.Rel(rr, ra); err == nil && inside(rel) {
			return rel, true
		}
	}
	return "", false
}

// excluder decides which directories to skip.
type excluder struct {
	names []string
	paths []pathExclude
}

type pathExclude struct {
	rel  string      // cleaned, OS-separated path relative to root
	info fs.FileInfo // the directory it names, if it exists
}

func newExcluder(root string, dirs []string) (*excluder, error) {
	norm, err := NormalizeExcludes(root, dirs)
	if err != nil {
		return nil, err
	}
	ex := &excluder{}
	for _, d := range norm {
		if !strings.HasPrefix(d, "./") {
			ex.names = append(ex.names, d)
			continue
		}
		rel := filepath.Clean(filepath.FromSlash(d))
		pe := pathExclude{rel: rel}
		if info, err := os.Lstat(filepath.Join(root, rel)); err == nil {
			pe.info = info
		}
		ex.paths = append(ex.paths, pe)
	}
	return ex, nil
}

func (ex *excluder) match(root, path string, d fs.DirEntry) bool {
	for _, n := range ex.names {
		// Names match regardless of case, as documented.
		if strings.EqualFold(n, d.Name()) {
			return true
		}
	}
	if len(ex.paths) == 0 {
		return false
	}
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return false
	}
	for _, pe := range ex.paths {
		if rel == pe.rel {
			return true
		}
		// A differently-cased spelling excludes the directory only when the
		// file system itself resolves it to the same directory, so
		// case-sensitive file systems are not treated as case-insensitive.
		if pe.info != nil && strings.EqualFold(rel, pe.rel) {
			if info, err := d.Info(); err == nil && os.SameFile(info, pe.info) {
				return true
			}
		}
	}
	return false
}

func matchExt(name string, exts []string) bool {
	if len(exts) == 0 {
		return true
	}
	lower := strings.ToLower(name)
	for _, e := range exts {
		if strings.HasSuffix(lower, "."+e) && len(lower) > len(e)+1 {
			return true
		}
	}
	return false
}

func matchSize(size int64, conds []query.SizeCond) bool {
	for _, c := range conds {
		if !c.Match(size) {
			return false
		}
	}
	return true
}

// matchNames reports whether the base name satisfies every filename
// condition and at least one condition of each filename OR group.
func matchNames(name string, q *query.Query) bool {
	for _, c := range q.Names {
		if !c.Match(name) {
			return false
		}
	}
	for _, g := range q.AnyOf {
		if g.Kind == query.KindName && !slices.ContainsFunc(g.Names, func(c query.NameCond) bool { return c.Match(name) }) {
			return false
		}
	}
	return true
}

// matchGroups reports whether every size and time OR group has at least one
// matching alternative. Content groups are checked by matchContent.
func matchGroups(size int64, mtime time.Time, groups []query.OrGroup) bool {
	for _, g := range groups {
		switch g.Kind {
		case query.KindSize:
			if !slices.ContainsFunc(g.Sizes, func(c query.SizeCond) bool { return c.Match(size) }) {
				return false
			}
		case query.KindTime:
			if !slices.ContainsFunc(g.Times, func(r query.TimeRange) bool { return r.Match(mtime) }) {
				return false
			}
		}
	}
	return true
}

func matchTime(t time.Time, q *query.Query) bool {
	if !q.ModifiedAfter.IsZero() && t.Before(q.ModifiedAfter) {
		return false
	}
	if !q.ModifiedBefore.IsZero() && !t.Before(q.ModifiedBefore) {
		return false
	}
	return true
}

func hasContent(q *query.Query) bool {
	return len(q.Contents) > 0 || slices.ContainsFunc(q.AnyOf, func(g query.OrGroup) bool { return g.Kind == query.KindContent })
}

// matchContent reports whether the file contains every term of q.Contents
// (AND) and at least one term of each content OR group. The file is read
// once for all terms.
func matchContent(path string, q *query.Query) (bool, error) {
	terms := append([]string(nil), q.Contents...)
	type span struct{ lo, hi int } // a content OR group's terms within terms
	var groups []span
	for _, g := range q.AnyOf {
		if g.Kind == query.KindContent {
			groups = append(groups, span{len(terms), len(terms) + len(g.Contents)})
			terms = append(terms, g.Contents...)
		}
	}
	satisfied := func(found []bool) bool {
		for i := range q.Contents {
			if !found[i] {
				return false
			}
		}
		for _, g := range groups {
			if !slices.Contains(found[g.lo:g.hi], true) {
				return false
			}
		}
		return true
	}
	found, err := scanTerms(path, terms, q.IgnoreCase, satisfied)
	if err != nil || found == nil {
		return false, err
	}
	return satisfied(found), nil
}

const chunkSize = 64 << 10

// scanTerms streams the file once and reports which terms occur in it,
// stopping as soon as satisfied(found) holds. Binary files (a NUL byte in the
// first chunk) are skipped like grep -I: found is nil. With ignoreCase the
// result is that of bytes.ToLower applied to the whole file and each term.
func scanTerms(path string, terms []string, ignoreCase bool, satisfied func(found []bool) bool) ([]bool, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	needles := make([][]byte, len(terms))
	maxLen := 0
	for i, t := range terms {
		needles[i] = []byte(t)
		if ignoreCase {
			needles[i] = bytes.ToLower(needles[i])
		}
		maxLen = max(maxLen, len(needles[i]))
	}
	found := make([]bool, len(terms))

	buf := make([]byte, chunkSize)
	var window []byte // the bytes searched; already lower-cased with ignoreCase
	var carry []byte  // ignoreCase: an incomplete UTF-8 sequence ending the last read
	first := true
	for {
		n, err := f.Read(buf)
		if n > 0 && first {
			if bytes.IndexByte(buf[:n], 0) >= 0 {
				return nil, nil
			}
			first = false
		}
		if n > 0 || len(carry) > 0 && err != nil {
			chunk := buf[:n]
			if ignoreCase {
				// Lower-case the stream, not the window: lowering can change
				// the length (K U+212A is 3 bytes, k is 1), so the tail kept
				// below must be measured after lowering. A rune split between
				// reads is carried over whole instead of becoming U+FFFD.
				data := chunk
				if len(carry) > 0 {
					data = append(carry, chunk...)
					carry = nil
				}
				if err == nil {
					if k := incompleteTail(data); k > 0 {
						carry = bytes.Clone(data[len(data)-k:])
						data = data[:len(data)-k]
					}
				}
				window = append(window, bytes.ToLower(data)...)
			} else {
				window = append(window, chunk...)
			}
			for i, nd := range needles {
				if !found[i] && bytes.Contains(window, nd) {
					found[i] = true
				}
			}
			if satisfied(found) {
				return found, nil
			}
			// Keep a tail so matches spanning chunk boundaries are found.
			if keep := max(maxLen-1, 0); len(window) > keep {
				window = append([]byte(nil), window[len(window)-keep:]...)
			}
		}
		if err == io.EOF {
			return found, nil
		}
		if err != nil {
			return nil, err
		}
	}
}

// incompleteTail returns the length of the incomplete UTF-8 sequence that
// ends b, or 0 when b ends with a whole rune or with bytes that are invalid
// whatever follows them.
func incompleteTail(b []byte) int {
	for k := 1; k < utf8.UTFMax && k <= len(b); k++ {
		if utf8.RuneStart(b[len(b)-k]) {
			if utf8.FullRune(b[len(b)-k:]) {
				return 0
			}
			return k
		}
	}
	return 0
}

// better reports whether a sorts before b: larger size or newer mtime first,
// ties broken by ascending Path. Paths are unique within one Run, so this is
// a strict total order. sortResults and topN share it.
func better(a, b Result, order query.SortOrder) bool {
	switch order {
	case query.SortLargest:
		if a.Size != b.Size {
			return a.Size > b.Size
		}
	case query.SortNewest:
		if !a.ModTime.Equal(b.ModTime) {
			return a.ModTime.After(b.ModTime)
		}
	}
	return a.Path < b.Path
}

func sortResults(rs []Result, order query.SortOrder) {
	sort.SliceStable(rs, func(i, j int) bool { return better(rs[i], rs[j], order) })
}

// topNInitialCap bounds the first allocation: limits up to MaxInt32 are
// valid, and the heap grows only as results arrive.
const topNInitialCap = 1024

// topN keeps the n best results seen so far (n >= 1). The worst kept result
// is at h[0], so a new result either replaces it or is dropped.
type topN struct {
	order query.SortOrder
	n     int
	h     []Result
}

// newTopNFor returns a bounded collector when q is sorted with a limit, else nil.
func newTopNFor(q *query.Query) *topN {
	if q.Sort == query.SortNone || q.Limit <= 0 {
		return nil
	}
	return &topN{order: q.Sort, n: q.Limit, h: make([]Result, 0, min(q.Limit, topNInitialCap))}
}

func (t *topN) Len() int           { return len(t.h) }
func (t *topN) Less(i, j int) bool { return better(t.h[j], t.h[i], t.order) } // worst at root
func (t *topN) Swap(i, j int)      { t.h[i], t.h[j] = t.h[j], t.h[i] }
func (t *topN) Push(x any)         { t.h = append(t.h, x.(Result)) }
func (t *topN) Pop() any           { r := t.h[len(t.h)-1]; t.h = t.h[:len(t.h)-1]; return r }

func (t *topN) add(r Result) {
	if len(t.h) < t.n {
		heap.Push(t, r)
		return
	}
	if better(r, t.h[0], t.order) {
		t.h[0] = r
		heap.Fix(t, 0)
	}
}

// results returns the kept results in output order (not heap order).
func (t *topN) results() []Result {
	rs := t.h
	t.h = nil
	sortResults(rs, t.order)
	return rs
}
