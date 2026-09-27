// Package query turns a natural-language search request (Chinese or English)
// into a structured, deterministic Query. No network or model calls are made.
package query

import (
	"path"
	"strings"
	"time"
)

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

// CondKind is the type of a condition that can take part in an OR group.
type CondKind int

const (
	KindNone CondKind = iota
	KindExtension
	KindContent
	KindTime
	KindSize
	KindName
)

// NameMatchKind is how a filename condition compares the file's base name.
type NameMatchKind int

const (
	NameExact    NameMatchKind = iota // base name == pattern
	NameContains                      // base name contains pattern
	NamePrefix                        // base name starts with pattern
	NameSuffix                        // base name ends with pattern
	NameGlob                          // path.Match(pattern, base name): *, ?, [...]
)

// NameCond is a condition on a file's base name (never its directory).
// Matching is case-sensitive. A NameGlob pattern is validated when parsed.
type NameCond struct {
	Kind    NameMatchKind
	Pattern string
}

// Match reports whether the base name satisfies the condition.
func (c NameCond) Match(name string) bool {
	switch c.Kind {
	case NameExact:
		return name == c.Pattern
	case NameContains:
		return strings.Contains(name, c.Pattern)
	case NamePrefix:
		return strings.HasPrefix(name, c.Pattern)
	case NameSuffix:
		return strings.HasSuffix(name, c.Pattern)
	case NameGlob:
		ok, err := path.Match(c.Pattern, name)
		return err == nil && ok
	}
	return false
}

// TimeRange is a half-open range on the modification time,
// After <= mtime < Before; a zero bound is unbounded.
type TimeRange struct {
	After, Before time.Time
}

// Match reports whether t lies in the range.
func (r TimeRange) Match(t time.Time) bool {
	return (r.After.IsZero() || !t.Before(r.After)) && (r.Before.IsZero() || t.Before(r.Before))
}

// OrGroup is "any of" a list of conditions of one kind: it is satisfied when
// at least one alternative matches. Only the field for Kind is used.
// (Extensions need no group: Query.Extensions is already any-of.)
type OrGroup struct {
	Kind     CondKind
	Contents []string    // KindContent: at least one term appears in the file
	Times    []TimeRange // KindTime: mtime lies in at least one range
	Sizes    []SizeCond  // KindSize: at least one comparison holds
	Names    []NameCond  // KindName: at least one filename condition holds
}

// Query is the structured form of a natural-language request.
//
// A file matches when every condition holds (AND): its extension is one of
// Extensions (any-of), its base name satisfies every one of Names, it
// contains every one of Contents, every size
// comparison holds, ModifiedAfter <= mtime < ModifiedBefore, and every
// OrGroup in AnyOf is satisfied by at least one of its alternatives.
type Query struct {
	Root        string     // search root; "" means current directory
	Extensions  []string   // lower case, without leading dot (e.g. "go", "tar.gz")
	Names       []NameCond // filename conditions on the base name, all of which must hold
	Contents    []string   // literal substrings that must all appear in the file
	IgnoreCase  bool       // content matching ignores case
	ExcludeDirs []string   // directory names (or relative paths) to skip
	Sizes       []SizeCond

	// ModifiedAfter/ModifiedBefore bound the modification time. Zero means unbounded.
	// A file matches when ModifiedAfter <= mtime < ModifiedBefore.
	ModifiedAfter  time.Time
	ModifiedBefore time.Time

	// AnyOf holds OR groups ("今天或昨天", `"ERROR" or "FATAL"`); each group
	// is ANDed with everything else.
	AnyOf []OrGroup

	Sort  SortOrder
	Limit int // 0 means no limit

	Lang Lang // language of the original request
	Now  time.Time

	// RootSymlink is set by the caller when Root is a symlink to a
	// directory, so Explain can make find follow it like the search does.
	RootSymlink bool
}
