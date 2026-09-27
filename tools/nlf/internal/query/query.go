// Package query turns a natural-language search request (Chinese or English)
// into a structured, deterministic Query. No network or model calls are made.
package query

import "time"

// Lang selects the language used for messages shown to the user.
type Lang int

const (
	English Lang = iota
	Chinese
)

// SortOrder controls result ordering. SortNone keeps directory walk order.
type SortOrder int

const (
	SortNone SortOrder = iota
	SortLargest
	SortNewest
)

// SizeCond is a single size comparison against the file size in bytes.
type SizeCond struct {
	Op    string // one of ">", ">=", "<", "<="
	Bytes int64
}

// Match reports whether size satisfies the condition.
func (c SizeCond) Match(size int64) bool {
	switch c.Op {
	case ">":
		return size > c.Bytes
	case ">=":
		return size >= c.Bytes
	case "<":
		return size < c.Bytes
	case "<=":
		return size <= c.Bytes
	}
	return false
}

// Query is the structured form of a natural-language request.
// All conditions are combined with AND; Extensions match if any one matches.
type Query struct {
	Root        string   // search root; "" means current directory
	Extensions  []string // lower case, without leading dot (e.g. "go", "tar.gz")
	Contents    []string // literal substrings that must all appear in the file
	IgnoreCase  bool     // content matching ignores case
	ExcludeDirs []string // directory names (or relative paths) to skip
	Sizes       []SizeCond

	// ModifiedAfter/ModifiedBefore bound the modification time. Zero means unbounded.
	// A file matches when ModifiedAfter <= mtime < ModifiedBefore.
	ModifiedAfter  time.Time
	ModifiedBefore time.Time

	Sort  SortOrder
	Limit int // 0 means no limit

	Lang Lang // language of the original request
	Now  time.Time

	// RootSymlink is set by the caller when Root is a symlink to a
	// directory, so Explain can make find follow it like the search does.
	RootSymlink bool
}
