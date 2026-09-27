package query

import (
	"fmt"
	"path"
	"strings"
	"time"
	"unicode"
)

const timeLayout = "2006-01-02 15:04:05"

// Condition is one understood search condition, ready for display.
type Condition struct {
	Label, Value string
}

// Conditions lists the conditions of q (everything except the root) with
// labels in the given language.
func Conditions(q *Query, lang Lang) []Condition {
	zh := lang == Chinese
	pick := func(c, e string) string {
		if zh {
			return c
		}
		return e
	}
	var cs []Condition
	add := func(label, value string) { cs = append(cs, Condition{label, value}) }
	if len(q.Extensions) > 0 {
		exts := make([]string, len(q.Extensions))
		for i, e := range q.Extensions {
			exts[i] = "." + e
		}
		add(pick("副檔名", "extension:"), strings.Join(exts, pick(" 或 ", " or ")))
	}
	for _, c := range q.Contents {
		sens := pick("區分大小寫", "case-sensitive")
		if q.IgnoreCase {
			sens = pick("不分大小寫", "ignore case")
		}
		add(pick("內容包含", "content:"), fmt.Sprintf("%q (%s)", c, sens))
	}
	switch after, before := q.ModifiedAfter, q.ModifiedBefore; {
	case !after.IsZero() && !before.IsZero():
		add(pick("修改時間", "modified:"), after.Format(timeLayout)+" <= mtime < "+before.Format(timeLayout))
	case !after.IsZero():
		add(pick("修改時間", "modified:"), pick("晚於 ", "after ")+after.Format(timeLayout)+" (mtime >= "+after.Format(timeLayout)+")")
	case !before.IsZero():
		add(pick("修改時間", "modified:"), pick("早於 ", "before ")+before.Format(timeLayout)+" (mtime < "+before.Format(timeLayout)+")")
	}
	if t := q.ModifiedAfter; !t.IsZero() || !q.ModifiedBefore.IsZero() {
		if t.IsZero() {
			t = q.ModifiedBefore
		}
		add(pick("時區", "time zone:"), t.Format("MST (-07:00)")+pick("，本機時間", ", local time"))
	}
	for _, s := range q.Sizes {
		add(pick("檔案大小", "size:"), s.Op+" "+HumanBytes(s.Bytes))
	}
	if len(q.ExcludeDirs) > 0 {
		add(pick("排除目錄", "exclude dirs:"), strings.Join(q.ExcludeDirs, ", "))
	}
	switch q.Sort {
	case SortLargest:
		add(pick("排序", "order:"), pick("檔案由大到小", "largest first"))
	case SortNewest:
		add(pick("排序", "order:"), pick("最近修改的在前", "newest first"))
	}
	if q.Limit > 0 {
		add(pick("數量上限", "limit:"), fmt.Sprint(q.Limit))
	}
	return cs
}

// FormatConditions renders conditions as indented, aligned rows.
func FormatConditions(cs []Condition) string {
	var b strings.Builder
	for _, c := range cs {
		fmt.Fprintf(&b, "  %s %s\n", padRight(c.Label, 14), c.Value)
	}
	return b.String()
}

// Platform selects the dialect of the approximate shell command: BSD tools
// on macOS or GNU findutils/coreutils on Linux. The find, grep, sort, cut
// and head parts are the same; only stat differs.
type Platform int

const (
	MacOS Platform = iota
	Linux
)

// PlatformFor maps a GOOS value to the command dialect: GNU on Linux, BSD
// (macOS) everywhere else.
func PlatformFor(goos string) Platform {
	if goos == "linux" {
		return Linux
	}
	return MacOS
}

// Name is the platform as shown to users.
func (p Platform) Name() string {
	if p == Linux {
		return "GNU/Linux"
	}
	return "macOS"
}

// Explain describes how the query was understood, followed by an approximate
// find command for platform p.
func Explain(q *Query, p Platform) string {
	zh := q.Lang == Chinese
	pick := func(c, e string) string {
		if zh {
			return c
		}
		return e
	}
	var b strings.Builder
	b.WriteString(pick("解析結果：\n", "Understood as:\n"))
	b.WriteString(FormatConditions(append([]Condition{{pick("搜尋位置", "root:"), rootOrDot(q.Root)}}, Conditions(q, q.Lang)...)))
	b.WriteString(pick("\n近似的 "+p.Name()+" 指令（僅供參考，nlf 不會執行它）：\n",
		"\nApproximate "+p.Name()+" command for reference; nlf does not execute it:\n"))
	b.WriteString("  " + FindCommand(q, p) + "\n")
	return b.String()
}

// FindCommand renders q as an approximate find pipeline for platform p. It
// is for reference only: ordering, tie-breaking and some edge cases (binary
// detection, case folding) can differ from nlf's own search.
func FindCommand(q *Query, p Platform) string {
	root := findRoot(q)
	parts := []string{"find", shellQuote(root)}
	if len(q.ExcludeDirs) > 0 {
		var ex []string
		for _, d := range q.ExcludeDirs {
			if strings.Contains(d, "/") {
				// find prints paths as root + "/" + rel, so match that form.
				rel := strings.TrimPrefix(path.Clean(d), "./")
				ex = append(ex, "-path", shellQuote(strings.TrimSuffix(root, "/")+"/"+rel))
			} else {
				ex = append(ex, "-iname", shellQuote(d))
			}
		}
		// Only directories are pruned, like nlf; a file named "vendor" is kept.
		parts = append(parts, "-type", "d", `\(`, strings.Join(joinOr(ex, 2), " "), `\)`, "-prune", "-o")
	}
	parts = append(parts, "-type", "f")
	if len(q.Extensions) > 0 {
		var ex []string
		for _, e := range q.Extensions {
			ex = append(ex, "-iname", shellQuote("*."+e))
		}
		if len(q.Extensions) == 1 {
			parts = append(parts, ex...)
		} else {
			parts = append(parts, `\(`, strings.Join(joinOr(ex, 2), " "), `\)`)
		}
	}
	for _, s := range q.Sizes {
		switch s.Op {
		case ">":
			parts = append(parts, "-size", fmt.Sprintf("+%dc", s.Bytes))
		case ">=":
			parts = append(parts, "-size", fmt.Sprintf("+%dc", s.Bytes-1))
		case "<":
			parts = append(parts, "-size", fmt.Sprintf("-%dc", s.Bytes))
		case "<=":
			parts = append(parts, "-size", fmt.Sprintf("-%dc", s.Bytes+1))
		}
	}
	if !q.ModifiedAfter.IsZero() {
		parts = append(parts, "-newermt", shellQuote(q.ModifiedAfter.Format(timeLayout)))
	}
	if !q.ModifiedBefore.IsZero() {
		parts = append(parts, "!", "-newermt", shellQuote(q.ModifiedBefore.Format(timeLayout)))
	}
	for _, c := range q.Contents {
		flags := "-qIF"
		if q.IgnoreCase {
			flags = "-qIiF"
		}
		parts = append(parts, "-exec", "grep", flags, "--", shellQuote(c), "{}", `\;`)
	}
	cmd := strings.Join(parts, " ")
	switch q.Sort {
	case SortLargest, SortNewest:
		// "<size or mtime> <path>" per file: BSD stat -f '%z'/'%m' '%N',
		// GNU stat -c '%s'/'%Y' '%n'.
		format := map[Platform]map[SortOrder]string{
			MacOS: {SortLargest: "%z %N", SortNewest: "%m %N"},
			Linux: {SortLargest: "%s %n", SortNewest: "%Y %n"},
		}[p][q.Sort]
		flag := "-f"
		if p == Linux {
			flag = "-c"
		}
		cmd += " -exec stat " + flag + " '" + format + "' {} + | sort -rn | cut -d' ' -f2-"
	default:
		cmd += " -print"
	}
	if q.Limit > 0 {
		cmd += fmt.Sprintf(" | head -n %d", q.Limit)
	}
	return cmd
}

// padRight pads s to width terminal columns; CJK characters are two columns wide.
func padRight(s string, width int) string {
	w := 0
	for _, r := range s {
		if unicode.Is(unicode.Han, r) {
			w += 2
		} else {
			w++
		}
	}
	return s + strings.Repeat(" ", max(width-w, 1))
}

// joinOr inserts "-o" between groups of n arguments.
func joinOr(args []string, n int) []string {
	var out []string
	for i := 0; i < len(args); i += n {
		if i > 0 {
			out = append(out, "-o")
		}
		out = append(out, args[i:i+n]...)
	}
	return out
}

func rootOrDot(r string) string {
	if r == "" {
		return "."
	}
	return r
}

// findRoot is the starting point for the find command: a symlinked root
// gets a trailing "/" so BSD find descends into it, and a root starting
// with "-" gets "./" so find does not read it as an option.
func findRoot(q *Query) string {
	r := rootOrDot(q.Root)
	if strings.HasPrefix(r, "-") {
		r = "./" + r
	}
	if q.RootSymlink && !strings.HasSuffix(r, "/") {
		r += "/"
	}
	return r
}

func shellQuote(s string) string {
	if s != "" && strings.Trim(s, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./:@%+=,") == "" {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

// HumanBytes formats n using binary units (1 KB = 1024 bytes).
func HumanBytes(n int64) string {
	units := []string{"B", "KB", "MB", "GB", "TB"}
	v := float64(n)
	i := 0
	for v >= 1024 && i < len(units)-1 {
		v /= 1024
		i++
	}
	if i == 0 {
		return fmt.Sprintf("%d B", n)
	}
	if v == float64(int64(v)) {
		return fmt.Sprintf("%d %s", int64(v), units[i])
	}
	return fmt.Sprintf("%.1f %s", v, units[i])
}

// FormatTime is the timestamp format used in listings.
func FormatTime(t time.Time) string { return t.Format("2006-01-02 15:04") }
