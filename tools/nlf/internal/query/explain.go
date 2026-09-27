package query

import (
	"fmt"
	"strings"
	"time"
	"unicode"
)

const timeLayout = "2006-01-02 15:04:05"

// Explain describes how the query was understood, followed by an equivalent
// macOS find command for reference.
func Explain(q *Query) string {
	zh := q.Lang == Chinese
	pick := func(c, e string) string {
		if zh {
			return c
		}
		return e
	}
	var b strings.Builder
	row := func(label, value string) { fmt.Fprintf(&b, "  %s %s\n", padRight(label, 14), value) }

	b.WriteString(pick("解析結果：\n", "Understood as:\n"))
	row(pick("搜尋位置", "root:"), rootOrDot(q.Root))
	if len(q.Extensions) > 0 {
		exts := make([]string, len(q.Extensions))
		for i, e := range q.Extensions {
			exts[i] = "." + e
		}
		row(pick("副檔名", "extension:"), strings.Join(exts, pick(" 或 ", " or ")))
	}
	for _, c := range q.Contents {
		cs := pick("區分大小寫", "case-sensitive")
		if q.IgnoreCase {
			cs = pick("不分大小寫", "ignore case")
		}
		row(pick("內容包含", "content:"), fmt.Sprintf("%q (%s)", c, cs))
	}
	if !q.ModifiedAfter.IsZero() {
		row(pick("修改時間", "modified:"), pick("晚於 ", "after ")+q.ModifiedAfter.Format(timeLayout))
	}
	if !q.ModifiedBefore.IsZero() {
		row(pick("修改時間", "modified:"), pick("早於 ", "before ")+q.ModifiedBefore.Format(timeLayout))
	}
	for _, s := range q.Sizes {
		row(pick("檔案大小", "size:"), s.Op+" "+HumanBytes(s.Bytes))
	}
	if len(q.ExcludeDirs) > 0 {
		row(pick("排除目錄", "exclude dirs:"), strings.Join(q.ExcludeDirs, ", "))
	}
	switch q.Sort {
	case SortLargest:
		row(pick("排序", "order:"), pick("檔案由大到小", "largest first"))
	case SortNewest:
		row(pick("排序", "order:"), pick("最近修改的在前", "newest first"))
	}
	if q.Limit > 0 {
		row(pick("數量上限", "limit:"), fmt.Sprint(q.Limit))
	}
	b.WriteString(pick("\n等效的 macOS 指令（僅供參考，nlf 不會執行它）：\n", "\nEquivalent macOS command (for reference; nlf does not run it):\n"))
	b.WriteString("  " + FindCommand(q) + "\n")
	return b.String()
}

// FindCommand renders q as a BSD find (macOS) pipeline.
func FindCommand(q *Query) string {
	parts := []string{"find", shellQuote(rootOrDot(q.Root))}
	if len(q.ExcludeDirs) > 0 {
		var ex []string
		for _, d := range q.ExcludeDirs {
			if strings.Contains(d, "/") {
				ex = append(ex, "-path", shellQuote(strings.TrimSuffix(rootOrDot(q.Root), "/")+"/"+d))
			} else {
				ex = append(ex, "-iname", shellQuote(d))
			}
		}
		parts = append(parts, `\(`, strings.Join(joinOr(ex, 2), " "), `\)`, "-prune", "-o")
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
	case SortLargest:
		cmd += ` -exec stat -f '%z %N' {} + | sort -rn | cut -d' ' -f2-`
	case SortNewest:
		cmd += ` -exec stat -f '%m %N' {} + | sort -rn | cut -d' ' -f2-`
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
