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
	caseNote := pick("區分大小寫", "case-sensitive")
	for _, c := range q.Names {
		add(pick("檔名", "filename:"), nameText(c, pick)+" ("+caseNote+")")
	}
	for _, g := range q.AnyOf {
		if g.Kind == KindName {
			alts := make([]string, len(g.Names))
			for i, c := range g.Names {
				alts[i] = nameText(c, pick)
			}
			add(pick("檔名", "filename:"), strings.Join(alts, pick(" 或 ", " OR "))+" ("+pick("符合任一個即可，", "any one matches, ")+caseNote+")")
		}
	}
	sens := pick("區分大小寫", "case-sensitive")
	if q.IgnoreCase {
		sens = pick("不分大小寫", "ignore case")
	}
	orWord := pick(" 或 ", " OR ")
	anyOne := pick("符合任一個即可", "any one matches")
	for _, c := range q.Contents {
		add(pick("內容包含", "content:"), fmt.Sprintf("%q (%s)", c, sens))
	}
	for _, g := range q.AnyOf {
		if g.Kind == KindContent {
			terms := make([]string, len(g.Contents))
			for i, c := range g.Contents {
				terms[i] = fmt.Sprintf("%q", c)
			}
			add(pick("內容包含", "content:"), strings.Join(terms, orWord)+" ("+anyOne+pick("，", ", ")+sens+")")
		}
	}
	if !q.ModifiedAfter.IsZero() || !q.ModifiedBefore.IsZero() {
		add(pick("修改時間", "modified:"), rangeText(q.ModifiedAfter, q.ModifiedBefore, pick))
	}
	var zone time.Time
	for _, t := range []time.Time{q.ModifiedAfter, q.ModifiedBefore} {
		if zone.IsZero() {
			zone = t
		}
	}
	for _, g := range q.AnyOf {
		if g.Kind == KindTime {
			lines := []string{pick("任一區間（OR），符合任一個即可：", "any of (OR), any one matches:")}
			for _, r := range g.Times {
				lines = append(lines, "- "+rangeText(r.After, r.Before, pick))
				for _, t := range []time.Time{r.After, r.Before} {
					if zone.IsZero() {
						zone = t
					}
				}
			}
			add(pick("修改時間", "modified:"), strings.Join(lines, "\n"))
		}
	}
	if !zone.IsZero() {
		add(pick("時區", "time zone:"), zone.Format("MST (-07:00)")+pick("，本機時間", ", local time"))
	}
	for _, s := range q.Sizes {
		add(pick("檔案大小", "size:"), s.Op+" "+HumanBytes(s.Bytes))
	}
	for _, g := range q.AnyOf {
		if g.Kind == KindSize {
			sizes := make([]string, len(g.Sizes))
			for i, s := range g.Sizes {
				sizes[i] = s.Op + " " + HumanBytes(s.Bytes)
			}
			add(pick("檔案大小", "size:"), strings.Join(sizes, orWord)+" ("+anyOne+")")
		}
	}
	if len(q.ExcludeDirs) > 0 {
		add(pick("排除目錄", "exclude dirs:"), strings.Join(q.ExcludeDirs, ", "))
	}
	if q.MinDepth > 0 || q.HasMaxDepth {
		add(pick("搜尋深度", "depth:"), depthText(q, pick))
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

// depthText renders the depth limits of q, e.g. "at most 2 levels (...)".
func depthText(q *Query, pick func(c, e string) string) string {
	levels := func(n int) string {
		if n == 1 {
			return "level"
		}
		return "levels"
	}
	var s string
	switch {
	case !q.HasMaxDepth:
		s = pick(fmt.Sprintf("至少 %d 層", q.MinDepth), fmt.Sprintf("at least %d %s", q.MinDepth, levels(q.MinDepth)))
	case q.MinDepth == 0:
		s = pick(fmt.Sprintf("最多 %d 層", q.MaxDepth), fmt.Sprintf("at most %d %s", q.MaxDepth, levels(q.MaxDepth)))
	case q.MinDepth < q.MaxDepth:
		s = pick(fmt.Sprintf("%d 到 %d 層", q.MinDepth, q.MaxDepth), fmt.Sprintf("%d to %d %s", q.MinDepth, q.MaxDepth, levels(q.MaxDepth)))
	default:
		s = pick(fmt.Sprintf("剛好 %d 層", q.MinDepth), fmt.Sprintf("exactly %d %s", q.MinDepth, levels(q.MinDepth)))
	}
	return s + pick(" (搜尋位置本身是第 0 層，直接在裡面的是第 1 層)",
		" (the search location is level 0; files directly inside it are level 1)")
}

// nameText renders a filename condition, e.g. contains "config".
func nameText(c NameCond, pick func(c, e string) string) string {
	op := map[NameMatchKind][2]string{
		NameExact: {"是", "is"}, NameContains: {"包含", "contains"}, NamePrefix: {"開頭是", "starts with"},
		NameSuffix: {"結尾是", "ends with"}, NameGlob: {"符合 glob", "matches glob"},
	}[c.Kind]
	return pick(op[0], op[1]) + " " + fmt.Sprintf("%q", c.Pattern)
}

// namePattern is c as a find -name pattern: literal text has the glob
// characters * ? [ \ escaped, a glob is used as is.
func namePattern(c NameCond) string {
	lit := c.Pattern
	if c.Kind != NameGlob {
		lit = globEscape(lit)
	}
	switch c.Kind {
	case NameContains:
		return "*" + lit + "*"
	case NamePrefix:
		return lit + "*"
	case NameSuffix:
		return "*" + lit
	}
	return lit
}

// globEscape escapes the find glob characters \ * ? [ so that s matches
// only itself in a -name, -iname or -path pattern.
func globEscape(s string) string {
	var b strings.Builder
	for _, r := range s {
		if strings.ContainsRune(`*?[\`, r) {
			b.WriteByte('\\')
		}
		b.WriteRune(r)
	}
	return b.String()
}

// rangeText renders after <= mtime < before; a zero bound is unbounded.
func rangeText(after, before time.Time, pick func(c, e string) string) string {
	switch {
	case !after.IsZero() && !before.IsZero():
		return after.Format(timeLayout) + " <= mtime < " + before.Format(timeLayout)
	case !after.IsZero():
		return pick("晚於 ", "after ") + after.Format(timeLayout) + " (mtime >= " + after.Format(timeLayout) + ")"
	}
	return pick("早於 ", "before ") + before.Format(timeLayout) + " (mtime < " + before.Format(timeLayout) + ")"
}

// FormatConditions renders conditions as indented, aligned rows. A value
// with several lines continues under the first one.
func FormatConditions(cs []Condition) string {
	var b strings.Builder
	for _, c := range cs {
		label := padRight(c.Label, 14)
		lines := strings.Split(c.Value, "\n")
		fmt.Fprintf(&b, "  %s %s\n", label, lines[0])
		for _, l := range lines[1:] {
			fmt.Fprintf(&b, "  %s %s\n", strings.Repeat(" ", displayWidth(label)), l)
		}
	}
	return b.String()
}

// Platform selects the dialect of the approximate shell command: BSD tools
// on macOS or GNU findutils/coreutils on Linux. The find, grep, sort, cut
// and head parts are the same; the stat format and the -newermt time
// format differ. Windows has no find, grep or stat, so no command is shown
// there (see HasFindCommand).
type Platform int

const (
	MacOS Platform = iota
	Linux
	Windows
)

// PlatformFor maps a GOOS value to the command dialect: GNU on Linux, none
// on Windows, BSD (macOS) everywhere else.
func PlatformFor(goos string) Platform {
	switch goos {
	case "linux":
		return Linux
	case "windows":
		return Windows
	}
	return MacOS
}

// Name is the platform as shown to users.
func (p Platform) Name() string {
	switch p {
	case Linux:
		return "GNU/Linux"
	case Windows:
		return "Windows"
	}
	return "macOS"
}

// HasFindCommand reports whether p has the Unix tools FindCommand renders.
// On Windows nlf shows only its own conditions, never a command that would
// not exist or would mean something else there (find.exe searches text).
func (p Platform) HasFindCommand() bool { return p != Windows }

// Explain describes how the query was understood, followed by an approximate
// find command for platform p, or on Windows by a note that the conditions
// are the whole search.
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
	if !p.HasFindCommand() {
		b.WriteString(pick("\n"+NoCommandNote(Chinese)+"\n", "\n"+NoCommandNote(English)+"\n"))
		return b.String()
	}
	b.WriteString(pick("\n近似的 "+p.Name()+" 指令（僅供參考，nlf 不會執行它）：\n",
		"\nApproximate "+p.Name()+" command for reference; nlf does not execute it:\n"))
	b.WriteString("  " + FindCommand(q, p) + "\n")
	return b.String()
}

// NoCommandNote explains, on a platform without find, why no command is
// shown and that the listed conditions are the complete search.
func NoCommandNote(lang Lang) string {
	if lang == Chinese {
		return "Windows 上不顯示近似指令：find、grep、stat 是 Unix 工具（Windows 的 find.exe 是搜尋文字的另一個程式）。" +
			"上面列出的條件就是 nlf 實際搜尋的全部內容；nlf 不會執行任何指令。"
	}
	return "No approximate command on Windows: find, grep and stat are Unix tools (Windows find.exe is a different, text-search program). " +
		"The conditions above are the complete search; nlf never runs a command."
}

// FindCommand renders q as an approximate find pipeline for platform p,
// which must have HasFindCommand. It is for reference only: unsorted
// output order and some edge cases (binary detection, case folding) can
// differ from nlf's own search.
func FindCommand(q *Query, p Platform) string {
	root := findRoot(q)
	parts := []string{"find", shellQuote(root)}
	// Right after the start point: GNU find warns about global options placed later.
	// -mindepth would also stop find from pruning excluded directories above
	// that depth, which nlf prunes at any depth, so with excludes the minimum
	// is a -path test on files instead.
	minPath := q.MinDepth > 0 && len(q.ExcludeDirs) > 0
	if q.MinDepth > 0 && !minPath {
		parts = append(parts, "-mindepth", fmt.Sprint(q.MinDepth))
	}
	if q.HasMaxDepth {
		parts = append(parts, "-maxdepth", fmt.Sprint(q.MaxDepth))
	}
	if len(q.ExcludeDirs) > 0 {
		var ex []string
		guard := false
		base := path.Base(path.Clean(rootOrDot(q.Root)))
		for _, d := range q.ExcludeDirs {
			if strings.Contains(d, "/") {
				// find prints paths as root + "/" + rel, so match that form.
				// Glob characters in either part are literal.
				rel := strings.TrimPrefix(path.Clean(d), "./")
				ex = append(ex, "-path", shellQuote(globEscape(strings.TrimSuffix(root, "/"))+"/"+globEscape(rel)))
			} else {
				ex = append(ex, "-iname", shellQuote(globEscape(d)))
				guard = guard || strings.EqualFold(base, d)
			}
		}
		// Only directories are pruned, like nlf; a file named "vendor" is kept.
		parts = append(parts, "-type", "d")
		if guard {
			// nlf never excludes the search location itself, even when its
			// name is excluded, so find must not prune its start point.
			parts = append(parts, "!", "-path", shellQuote(globEscape(root)))
		}
		parts = append(parts, `\(`, strings.Join(joinOr(ex, 2), " "), `\)`, "-prune", "-o")
	}
	parts = append(parts, "-type", "f")
	if minPath {
		// find prints paths as root + "/" + rel; each "/*" is one more level.
		parts = append(parts, "-path", shellQuote(globEscape(strings.TrimSuffix(root, "/"))+strings.Repeat("/*", q.MinDepth)))
	}
	if len(q.Extensions) > 0 {
		var ex []string
		for _, e := range q.Extensions {
			// "?" keeps a name that is only ".go" from having the extension
			// go, as in nlf.
			ex = append(ex, "-iname", shellQuote("?*."+globEscape(e)))
		}
		if len(q.Extensions) == 1 {
			parts = append(parts, ex...)
		} else {
			parts = append(parts, `\(`, strings.Join(joinOr(ex, 2), " "), `\)`)
		}
	}
	// Filenames are case-sensitive: -name, not -iname.
	for _, c := range q.Names {
		parts = append(parts, "-name", shellQuote(namePattern(c)))
	}
	for _, g := range q.AnyOf {
		if g.Kind == KindName {
			var alts [][]string
			for _, c := range g.Names {
				alts = append(alts, []string{"-name", shellQuote(namePattern(c))})
			}
			parts = append(parts, anyOf(alts)...)
		}
	}
	for _, s := range q.Sizes {
		parts = append(parts, sizeArgs(s)...)
	}
	parts = append(parts, timeArgs(q.ModifiedAfter, q.ModifiedBefore, p)...)
	// OR groups: \( alt1 -o alt2 ... \)
	for _, g := range q.AnyOf {
		var alts [][]string
		switch g.Kind {
		case KindSize:
			for _, s := range g.Sizes {
				alts = append(alts, sizeArgs(s))
			}
		case KindTime:
			for _, r := range g.Times {
				alts = append(alts, timeArgs(r.After, r.Before, p))
			}
		}
		if len(alts) > 0 {
			parts = append(parts, anyOf(alts)...)
		}
	}
	flags := "-qIF"
	if q.IgnoreCase {
		flags = "-qIiF"
	}
	for _, c := range q.Contents {
		parts = append(parts, "-exec", "grep", flags, "--", shellQuote(c), "{}", `\;`)
	}
	for _, g := range q.AnyOf {
		if g.Kind == KindContent { // grep -e A -e B matches when any pattern does
			parts = append(parts, "-exec", "grep", flags)
			for _, c := range g.Contents {
				parts = append(parts, "-e", shellQuote(c))
			}
			parts = append(parts, "{}", `\;`)
		}
	}
	cmd := strings.Join(parts, " ")
	switch q.Sort {
	case SortLargest, SortNewest:
		// "<size or mtime> <path>" per file: BSD stat -f '%z'/'%Fm' '%N',
		// GNU stat -c '%s'/'%.9Y' '%n'; mtimes keep their nanoseconds.
		// Like nlf, ties are ordered by path, byte by byte (LC_ALL=C).
		format := map[Platform]map[SortOrder]string{
			MacOS: {SortLargest: "%z %N", SortNewest: "%Fm %N"},
			Linux: {SortLargest: "%s %n", SortNewest: "%.9Y %n"},
		}[p][q.Sort]
		flag := "-f"
		if p == Linux {
			flag = "-c"
		}
		cmd += " -exec stat " + flag + " '" + format + "' {} + | LC_ALL=C sort -k1,1nr -k2 | cut -d' ' -f2-"
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
	return s + strings.Repeat(" ", max(width-displayWidth(s), 1))
}

func displayWidth(s string) int {
	w := 0
	for _, r := range s {
		if unicode.Is(unicode.Han, r) {
			w += 2
		} else {
			w++
		}
	}
	return w
}

func sizeArgs(s SizeCond) []string {
	switch s.Op {
	case ">":
		return []string{"-size", fmt.Sprintf("+%dc", s.Bytes)}
	case ">=":
		if s.Bytes == 0 { // every file; "+-1c" is not a valid size
			return []string{"!", "-size", "-0c"}
		}
		return []string{"-size", fmt.Sprintf("+%dc", s.Bytes-1)}
	case "<":
		return []string{"-size", fmt.Sprintf("-%dc", s.Bytes)}
	case "<=":
		return []string{"-size", fmt.Sprintf("-%dc", s.Bytes+1)}
	}
	return nil
}

// timeArgs renders after <= mtime < before for find. -newermt X is the
// strict mtime > X, so both bounds are moved back by 1ns: after <= mtime is
// mtime > after-1ns, and mtime < before is ! mtime > before-1ns. GNU find
// compares nanoseconds and accepts them in the date; BSD find compares
// whole seconds, where the truncated after-1ns and before-1ns are exact for
// whole-second bounds.
func timeArgs(after, before time.Time, p Platform) []string {
	layout := timeLayout
	if p == Linux {
		layout += ".000000000"
	}
	var args []string
	if !after.IsZero() {
		args = append(args, "-newermt", shellQuote(after.Add(-time.Nanosecond).Format(layout)))
	}
	if !before.IsZero() {
		args = append(args, "!", "-newermt", shellQuote(before.Add(-time.Nanosecond).Format(layout)))
	}
	return args
}

// anyOf joins find expressions with -o inside parentheses.
func anyOf(alts [][]string) []string {
	out := []string{`\(`}
	for i, a := range alts {
		if i > 0 {
			out = append(out, "-o")
		}
		if len(a) > 2 {
			out = append(out, `\(`)
			out = append(out, a...)
			out = append(out, `\)`)
		} else {
			out = append(out, a...)
		}
	}
	return append(out, `\)`)
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
