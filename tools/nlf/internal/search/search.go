// Package search walks a directory tree read-only and returns files that
// match a query.Query.
package search

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

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
// order, results are emitted after the full walk. Nothing is ever written.
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

	ex, err := newExcluder(root, q.ExcludeDirs)
	if err != nil {
		return rep, err
	}

	var collected []Result
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
		if d.IsDir() {
			if path != root && ex.match(root, path, d) {
				return fs.SkipDir
			}
			return nil
		}
		if !d.Type().IsRegular() || !matchExt(d.Name(), q.Extensions) {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			rep.record(path, err)
			return nil
		}
		if !matchSize(info.Size(), q.Sizes) || !matchTime(info.ModTime(), q) {
			return nil
		}
		if len(q.Contents) > 0 {
			ok, err := containsAll(path, q.Contents, q.IgnoreCase)
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
			collected = append(collected, r)
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
		sortResults(collected, q.Sort)
		if q.Limit > 0 && len(collected) > q.Limit {
			collected = collected[:q.Limit]
		}
		for _, r := range collected {
			emit(r)
		}
	}
	return rep, nil
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

func matchTime(t time.Time, q *query.Query) bool {
	if !q.ModifiedAfter.IsZero() && t.Before(q.ModifiedAfter) {
		return false
	}
	if !q.ModifiedBefore.IsZero() && !t.Before(q.ModifiedBefore) {
		return false
	}
	return true
}

const chunkSize = 64 << 10

// containsAll reports whether the file contains every term. Binary files
// (a NUL byte in the first chunk) never match, like grep -I.
func containsAll(path string, terms []string, ignoreCase bool) (bool, error) {
	f, err := os.Open(path)
	if err != nil {
		return false, err
	}
	defer f.Close()

	pending := make([][]byte, 0, len(terms))
	maxLen := 0
	for _, t := range terms {
		b := []byte(t)
		if ignoreCase {
			b = bytes.ToLower(b)
		}
		pending = append(pending, b)
		maxLen = max(maxLen, len(b))
	}

	buf := make([]byte, chunkSize)
	var window []byte
	first := true
	for {
		n, err := f.Read(buf)
		if n > 0 {
			chunk := buf[:n]
			if first && bytes.IndexByte(chunk, 0) >= 0 {
				return false, nil
			}
			first = false
			window = append(window, chunk...)
			hay := window
			if ignoreCase {
				hay = bytes.ToLower(window)
			}
			remaining := pending[:0]
			for _, t := range pending {
				if !bytes.Contains(hay, t) {
					remaining = append(remaining, t)
				}
			}
			pending = remaining
			if len(pending) == 0 {
				return true, nil
			}
			// Keep a tail so matches spanning chunk boundaries are found.
			if keep := max(maxLen-1, 0); len(window) > keep {
				window = append([]byte(nil), window[len(window)-keep:]...)
			}
		}
		if err == io.EOF {
			return false, nil
		}
		if err != nil {
			return false, err
		}
	}
}

func sortResults(rs []Result, order query.SortOrder) {
	sort.SliceStable(rs, func(i, j int) bool {
		switch order {
		case query.SortLargest:
			if rs[i].Size != rs[j].Size {
				return rs[i].Size > rs[j].Size
			}
		case query.SortNewest:
			if !rs[i].ModTime.Equal(rs[j].ModTime) {
				return rs[i].ModTime.After(rs[j].ModTime)
			}
		}
		return rs[i].Path < rs[j].Path
	})
}
