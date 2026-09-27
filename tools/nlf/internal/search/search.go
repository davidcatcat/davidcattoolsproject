// Package search walks a directory tree read-only and returns files that
// match a query.Query.
package search

import (
	"bytes"
	"errors"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
)

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
			if path != root && excluded(root, path, d.Name(), q.ExcludeDirs) {
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

func excluded(root, path, name string, dirs []string) bool {
	rel, _ := filepath.Rel(root, path)
	for _, d := range dirs {
		if strings.Contains(d, "/") {
			if filepath.Clean(d) == rel {
				return true
			}
		} else if strings.EqualFold(d, name) {
			// macOS file systems are case-insensitive by default.
			return true
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
