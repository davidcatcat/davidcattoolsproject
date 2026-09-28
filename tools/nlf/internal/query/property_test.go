package query

import (
	"errors"
	"fmt"
	"math/rand"
	"reflect"
	"runtime"
	"slices"
	"strings"
	"testing"
	"time"
	"unicode"
	"unicode/utf8"
)

// Property tests for the parser (A7). The package has no t.Parallel tests,
// so TestParseGrowthIsLinear can measure allocations with runtime.MemStats.

// strictQueries are valid documented queries used as bases for insertion
// and determinism checks.
var strictQueries = []string{
	"找 ovpn 檔", "go files", "*.md", "go 或 md 檔", "副檔名 heic", "圖片", "檔名是 application.yml", "檔名包含 config",
	`filename matches "app-*.yml"`, "檔名以 app- 開頭", "name ends with .bak", `containing "text"`, `包含 "文字" 不分大小寫`,
	"內容有「錯誤」", "today", "昨天", "本月", "2026-09-28", "9 月", "9/1 到 9/15", "最近 6 小時", "last 7 days", "3 天前",
	"larger than 10MB", "at most 1KB", "大於 10MB", "10MB 以上", "top 10", "最多 10 個", "10 largest", "最大的 5 個", "newest 3",
	"exclude node_modules and .git", "排除 vendor、build 目錄", "排除 src/gen", "今天或昨天", `包含 "ERROR" 或 "FATAL"`,
	"大於 1GB 或小於 1KB", "find pem files under /etc", "go files containing TODO modified in the last 7 days",
	"10 largest files excluding node_modules and .git", "找今天或昨天修改，而且包含 \"ERROR\" 或 \"FATAL\" 的 log",
	"在 ./src 找 go 檔", `在 "~/My Docs" 找 pdf 檔`, "找昨天 23:00 到今天 02:00 修改的 log", "files modified on or after Sep 1",
	"找 2026-09-01 以前修改的檔案", "檔名包含 prod 或 staging", "log files modified today", "排除 \"a b\" 目錄，找 txt 檔",
}

// insideQuote reports whether byte offset off of s lies inside a quoted
// literal, using the tokenizer's quote pairs: after an opening quote and up
// to (and including the position of) its closing quote.
func insideQuote(s string, off int) bool {
	var closer rune
	for i, r := range s {
		if i >= off {
			return closer != 0
		}
		if closer != 0 {
			if r == closer {
				closer = 0
			}
		} else if c := quotePairs[r]; c != 0 {
			closer = c
		}
	}
	return false
}

// TestParserStrictnessInsertion inserts reserved punctuation at every rune
// boundary of valid queries. Outside quotes it must be an error, except a
// "?" that only whitespace follows (documented as allowed). Inside quotes it
// is literal text, so the Query must change. No insertion may ever give
// back the original Query.
func TestParserStrictnessInsertion(t *testing.T) {
	if len(strictQueries) < 50 {
		t.Fatalf("only %d base queries", len(strictQueries))
	}
	marks := []string{"!", "！", "(", ")", "（", "）", "?", "？"}
	total, identical := 0, 0
	for _, base := range strictQueries {
		orig, err := Parse(base, fuzzNow)
		if err != nil {
			t.Fatalf("base %q: %v", base, err)
		}
		for off := 0; off <= len(base); off++ {
			if off < len(base) && !utf8.RuneStart(base[off]) {
				continue
			}
			quoted := insideQuote(base, off)
			for _, x := range marks {
				for _, form := range []string{x, " " + x + " ", " " + x, x + " "} {
					in := base[:off] + form + base[off:]
					total++
					q, err := Parse(in, fuzzNow)
					_, after, _ := strings.Cut(in[off:], x)
					allowed := !quoted && (x == "?" || x == "？") && strings.TrimFunc(after, unicode.IsSpace) == ""
					switch {
					case allowed:
					case err == nil && reflect.DeepEqual(q, orig):
						identical++
						t.Errorf("insertion gives the original Query: %q", in)
					case !quoted && err == nil:
						t.Errorf("insertion outside quotes accepted: %q -> %+v", in, q)
					}
				}
			}
		}
	}
	if identical != 0 {
		t.Errorf("%d of %d insertions gave the original Query", identical, total)
	}
	t.Logf("%d insertions checked", total)
}

// TestParseDeterminism parses Unicode, OR and date queries repeatedly.
func TestParseDeterminism(t *testing.T) {
	ins := append(slices.Clone(strictQueries),
		"找今天或昨天或前天修改的 log 或 md 檔", "檔名包含 \u00e9 或 e\u0301", "包含 \"中文\" 或 \"😀\" 或 \"e\u0301\"",
		"9/1 到 9/15 或 10/1 到 10/15 或 11/1 到 11/2", "大於 1GB 或小於 1KB 或 at least 5MB",
		"exclude node_modules and .git and vendor and dist", "最近 3 天或最近 2 天或最近 4 天 and 今天或本月")
	for _, in := range ins {
		q0, err0 := Parse(in, fuzzNow)
		for i := 0; i < 20; i++ {
			q, err := Parse(in, fuzzNow)
			if (err == nil) != (err0 == nil) || (err != nil && err.Error() != err0.Error()) || !reflect.DeepEqual(q, q0) {
				t.Fatalf("Parse(%q) run %d differs: %+v %v vs %+v %v", in, i, q, err, q0, err0)
			}
		}
	}
}

// excludeList returns "go files exclude d0 and d1 and …" of about n bytes (B6).
func excludeList(n int) string {
	var b strings.Builder
	b.WriteString("go files exclude d0")
	for i := 1; b.Len() < n; i++ {
		fmt.Fprintf(&b, " and d%d", i)
	}
	return b.String()
}

// timeGroups returns k time OR groups of three overlapping ranges joined by
// "and" (B4): every combination is satisfiable.
func timeGroups(k int) string {
	return strings.TrimSuffix(strings.Repeat("最近 3 天或最近 2 天或最近 4 天 and ", k), " and ")
}

// parseWithin parses in on another goroutine and reports whether it
// finished within d. It always waits for the parse to end (a real hang is
// caught by go test -timeout), so no goroutine keeps allocating while the
// allocation tests measure.
func parseWithin(in string, d time.Duration) (q *Query, err error, elapsed time.Duration, ok bool) {
	type res struct {
		q   *Query
		err error
	}
	done := make(chan res, 1)
	start := time.Now()
	go func() {
		q, err := Parse(in, fuzzNow)
		done <- res{q, err}
	}()
	select {
	case r := <-done:
		return r.q, r.err, time.Since(start), true
	case <-time.After(d):
		r := <-done
		return r.q, r.err, time.Since(start), false
	}
}

var errInvalidUTF8Msg = "the query contains bytes that are not valid UTF-8"

// TestPathologicalInputs feeds very large or repetitive queries. Each must
// finish (with a result or an error) instead of hanging or panicking.
func TestPathologicalInputs(t *testing.T) {
	const n = 64 << 10
	cases := []struct {
		name    string
		in      string
		invalid bool // must be rejected as invalid UTF-8
	}{
		{"han run", "包含 " + strings.Repeat("鑫", n/3), false},
		{"bare han run", strings.Repeat("鑫", n/3), false},
		{"combining marks", "包含 e" + strings.Repeat("\u0301", n/2), false},
		{"invalid bytes", strings.Repeat("\xff", n), true},
		{"invalid tail", "go files " + strings.Repeat("\xe4\xb8", n/2), true},
		{"quoted 64 KiB", `containing "` + strings.Repeat("a", n) + `"`, false},
		{"unterminated quote", `containing "` + strings.Repeat("a", n), false},
		{"fullwidth commas", "go files " + strings.Repeat("，", 20000), false},
		{"bangs", strings.Repeat("!", n), false},
		{"or yesterday", "today" + strings.Repeat(" or yesterday", 5000), false},
		{"exclude list", excludeList(n), false},
		{"exclude spaces", "exclude " + strings.Repeat("dd ", n/3), false},
		{"huge size", "larger than " + strings.Repeat("9", n) + "MB", false},
		{"huge years", strings.Repeat("9", n) + " 年", false},
		{"chinese number", "最近 " + strings.Repeat("九千", n/6) + " 天", false},
		{"long path", "in /" + strings.Repeat("a/", n/2) + " go files", false},
		{"long han path", "in ~/" + strings.Repeat("鑫", n/3), false},
		{"and contents", strings.Repeat(`"a" `, 16<<10), false},
		{"time groups k=16", timeGroups(16), false},
		{"zero digits", "包含 鑫" + strings.Repeat("零", n/3), false},
		{"zero digit pairs", "包含 鑫" + strings.Repeat("一零", n/6), false},
		{"glued content", "包含 x" + strings.Repeat("鑫x", n/4), false},
		{"glued filename", "檔名包含 x" + strings.Repeat("鑫x", n/4), false},
		{"glued punctuation", "包含 x" + strings.Repeat("?", n), false},
		{"glued rest", "包含 x?" + strings.Repeat("x鑫", n/4), false},
	}
	for _, c := range cases {
		_, err, el, ok := parseWithin(c.in, 20*time.Second)
		if !ok {
			t.Errorf("%s (%d bytes): Parse took %v (limit 20s)", c.name, len(c.in), el.Round(time.Millisecond))
			continue
		}
		t.Logf("%-20s %6d bytes %8v err=%v", c.name, len(c.in), el.Round(time.Millisecond), firstLine(err))
		if c.invalid && (err == nil || err.Error() != errInvalidUTF8Msg) {
			t.Errorf("%s: err = %v, want %q", c.name, err, errInvalidUTF8Msg)
		}
	}
}

func firstLine(err error) string {
	if err == nil {
		return "<nil>"
	}
	s, _, _ := strings.Cut(err.Error(), "\n")
	if len(s) > 80 {
		s = strings.ToValidUTF8(s[:80], "") + "…"
	}
	return s
}

// allocBytes is the smallest number of bytes allocated by one Parse(in)
// over three runs.
func allocBytes(in string) uint64 {
	best := uint64(0)
	for i := 0; i < 3; i++ {
		var a, b runtime.MemStats
		runtime.ReadMemStats(&a)
		_, _ = Parse(in, fuzzNow)
		runtime.ReadMemStats(&b)
		if d := b.TotalAlloc - a.TotalAlloc; i == 0 || d < best {
			best = d
		}
	}
	return best
}

// growthGenerators build queries of about n bytes. Allocation must grow
// linearly: doubling n may not triple the bytes allocated.
var growthGenerators = []struct {
	name string
	gen  func(n int) string
}{
	{"han", func(n int) string { return "包含 " + strings.Repeat("鑫", n/3) }},             // B5
	{"combining", func(n int) string { return "包含 e" + strings.Repeat("\u0301", n/2) }}, // B5
	{"exclude-list", excludeList}, // B6
	{"punct", func(n int) string { return "go files " + strings.Repeat("，", n/3) }},                 // L5
	{"or-content", func(n int) string { return `containing "a"` + strings.Repeat(` or "a"`, n/7) }}, // L5
	{"or-time", func(n int) string { return "today" + strings.Repeat(" or yesterday", n/13) }},
	{"quoted", func(n int) string { return `containing "` + strings.Repeat("a", n) + `"` }},
	{"and-contents", func(n int) string { return strings.Repeat(`"a" `, n/4) }},
	{"zero-digits", func(n int) string { return "包含 鑫" + strings.Repeat("零", n/3) }},       // B7
	{"digit-pairs", func(n int) string { return "包含 鑫" + strings.Repeat("一零", n/6) }},      // B7
	{"glued-content", func(n int) string { return "包含 x" + strings.Repeat("鑫x", n/4) }},    // B8
	{"glued-filename", func(n int) string { return "檔名包含 x" + strings.Repeat("鑫x", n/4) }}, // B8
	{"glued-punct", func(n int) string { return "包含 x" + strings.Repeat("?", n) }},         // B8
	{"glued-rest", func(n int) string { return "包含 x?" + strings.Repeat("x鑫", n/4) }},      // B8
}

func TestParseGrowthIsLinear(t *testing.T) {
	for _, g := range growthGenerators {
		for _, n := range []int{4096, 8192} {
			b1, b2 := allocBytes(g.gen(n)), allocBytes(g.gen(2*n))
			ratio := float64(b2) / float64(max(b1, 1))
			t.Logf("%-15s %5d -> %5d bytes: %9d -> %10d allocated, x%.2f", g.name, n, 2*n, b1, b2, ratio)
			if ratio >= 3.0 {
				t.Errorf("%s: doubling the query from %d to %d bytes multiplied allocation by %.2f (want < 3)", g.name, n, 2*n, ratio)
			}
		}
	}
}

// timeSatisfiableOld is the exponential cross-product version replaced in
// A7 (B4), kept to prove the new one gives the same answers.
func timeSatisfiableOld(q *Query) bool {
	cur := []TimeRange{{After: q.ModifiedAfter, Before: q.ModifiedBefore}}
	for _, g := range q.AnyOf {
		if g.Kind != KindTime {
			continue
		}
		var next []TimeRange
		for _, a := range cur {
			for _, b := range g.Times {
				if r, ok := a.intersect(b); ok {
					next = append(next, r)
				}
			}
		}
		if len(next) == 0 {
			return false
		}
		cur = next
	}
	return true
}

func TestTimeSatisfiableIsPolynomial(t *testing.T) {
	// Differential: random bounds on a small grid (so ranges touch, nest
	// and are empty often), zero meaning unbounded.
	r := rand.New(rand.NewSource(1))
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	tm := func() time.Time {
		if r.Intn(4) == 0 {
			return time.Time{}
		}
		return base.Add(time.Duration(r.Intn(10)) * time.Hour)
	}
	agree := map[bool]int{}
	for it := 0; it < 100000; it++ {
		q := &Query{ModifiedAfter: tm(), ModifiedBefore: tm()}
		for g := r.Intn(5); g > 0; g-- {
			og := OrGroup{Kind: KindTime}
			if r.Intn(8) == 0 {
				og.Kind = KindSize // other kinds are ignored
			}
			for k := 1 + r.Intn(3); k > 0; k-- {
				og.Times = append(og.Times, TimeRange{After: tm(), Before: tm()})
			}
			q.AnyOf = append(q.AnyOf, og)
		}
		want, got := timeSatisfiableOld(q), timeSatisfiable(q)
		if want != got {
			t.Fatalf("timeSatisfiable = %v, old = %v for %+v", got, want, q)
		}
		agree[got]++
	}
	if agree[true] < 1000 || agree[false] < 1000 {
		t.Fatalf("differential cases are one-sided: %v", agree)
	}
	// Growth: 12 groups of 3 alternatives may cost at most 3x the
	// allocation of 6 groups (the cross product was 3^12 vs 3^6).
	b6, b12 := allocBytes(timeGroups(6)), allocBytes(timeGroups(12))
	ratio := float64(b12) / float64(max(b6, 1))
	t.Logf("time groups k=6 -> k=12: %d -> %d bytes, x%.2f", b6, b12, ratio)
	if ratio >= 3.0 {
		t.Errorf("time groups: doubling the groups multiplied allocation by %.2f (want < 3)", ratio)
	}
	for _, k := range []int{1, 6, 12} {
		if _, err := Parse(timeGroups(k), fuzzNow); err != nil {
			t.Errorf("timeGroups(%d): %v", k, err)
		}
	}
}

// TestInvalidUTF8QueryRejected pins B1: bytes that are not valid UTF-8 are
// rejected before tokenizing, instead of silently becoming U+FFFD.
func TestInvalidUTF8QueryRejected(t *testing.T) {
	for _, in := range []string{"\xff", "exclude \"\xff\" dir, find go files", "exclude \xff dir, find go files", "containing \xff",
		"containing \"\xff\"", "in /tmp/\xff", "in \"/tmp/\xff\" find go files", "go files \xc3", "\xed\xa0\x80", "\xc0\xaf",
		"\xf4\x90\x80\x80", "找 go 檔\xe4\xb8", "檔名包含 \"a\xffb\"", "\x80go files"} {
		q, err := Parse(in, fuzzNow)
		var pe *Error
		if err == nil || !errors.As(err, &pe) {
			t.Errorf("Parse(%q) = %+v, %v; want the invalid UTF-8 error", in, q, err)
			continue
		}
		if got := pe.Message(English); got != errInvalidUTF8Msg {
			t.Errorf("Parse(%q) English message %q", in, got)
		}
		if got := pe.Message(Chinese); got != "查詢含有無效的 UTF-8 位元組" {
			t.Errorf("Parse(%q) Chinese message %q", in, got)
		}
	}
	// A literal U+FFFD is valid UTF-8 and stays ordinary text.
	q, err := Parse("包含 \"\ufffd\"", fuzzNow)
	if err != nil || !slices.Equal(q.Contents, []string{"\ufffd"}) {
		t.Errorf("literal U+FFFD: %+v, %v", q, err)
	}
}

// TestLexiconPhrasesUnambiguous checks the phrase maps: the parser picks
// the longest phrase whose words match, so two keys with the same words but
// different meanings would depend on map order. Every key must also be
// reachable: typed on its own it tokenizes to exactly its words.
func TestLexiconPhrasesUnambiguous(t *testing.T) {
	maps := map[string]map[string]any{}
	add := func(name string, m map[string]any) { maps[name] = m }
	add("cmpPhrases", anyMap(cmpPhrases))
	add("postfixPhrases", anyMap(postfixPhrases))
	add("timeUnits", anyMap(timeUnits))
	add("sizeUnits", anyMap(sizeUnits))
	add("dayWords", anyMap(dayWords))
	add("monthWords", anyMap(monthWords))
	add("yearWords", anyMap(yearWords))
	add("monthNames", anyMap(monthNames))
	add("absPrefix", anyMap(absPrefix))
	add("absPostfix", anyMap(absPostfix))
	add("nameOps", anyMap(nameOps))
	add("nameClosing", anyMap(nameClosing))
	add("extAliases", anyMap(extAliases))
	for name, m := range maps {
		if len(m) == 0 {
			t.Errorf("%s is empty", name)
		}
		seen := map[string]string{}
		for k, v := range m {
			words := strings.Join(strings.Fields(k), " ")
			if o, ok := seen[words]; ok && !reflect.DeepEqual(m[o], v) {
				t.Errorf("%s: %q and %q have the same words but different meanings", name, k, o)
			}
			seen[words] = k
			toks, err := tokenize(k)
			if err != nil {
				t.Errorf("%s: %q does not tokenize: %v", name, k, err)
				continue
			}
			var texts []string
			for _, tk := range toks {
				texts = append(texts, tk.text)
			}
			if strings.Join(texts, " ") != words {
				t.Errorf("%s: %q tokenizes to %q, so it can never match", name, k, texts)
			}
		}
	}
}

func anyMap[V any](m map[string]V) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

// tokenizeOld is the tokenizer before A7 (B5, B7), kept to prove the linear
// version produces exactly the same tokens.
func tokenizeOld(s string) ([]token, error) {
	var toks []token
	spaced := true
	emit := func(ts ...token) {
		for k := range ts {
			ts[k].spaceBefore = spaced && k == 0
		}
		toks = append(toks, ts...)
		spaced = false
	}
	rs := []rune(s)
	for i := 0; i < len(rs); {
		r := rs[i]
		switch {
		case unicode.IsSpace(r):
			spaced = true
			i++
		case quotePairs[r] != 0:
			closer := quotePairs[r]
			j := i + 1
			for j < len(rs) && rs[j] != closer {
				j++
			}
			if j >= len(rs) {
				return nil, &Error{Token: string(rs[i:]), zh: "引號沒有結束", en: "unterminated quote"}
			}
			lit := string(rs[i+1 : j])
			emit(token{kind: tQuoted, text: lit, raw: string(rs[i : j+1])})
			i = j + 1
		case strings.ContainsRune("<>≥≤＞＜", r):
			op := map[rune]string{'<': "<", '>': ">", '≥': ">=", '≤': "<=", '＞': ">", '＜': "<"}[r]
			j := i + 1
			if (r == '<' || r == '>') && j < len(rs) && rs[j] == '=' {
				op += "="
				j++
			}
			emit(token{kind: tOp, text: op, raw: string(rs[i:j])})
			i = j
		case strings.ContainsRune(punctChars, r):
			emit(token{kind: tPunct, text: string(r), raw: string(r)})
			i++
		case r < utf8.RuneSelf:
			j := i
			for j < len(rs) && isWordRune(rs[j]) {
				j++
			}
			if hasPathPrefix(string(rs[i:j])) {
				for j < len(rs) && isPathRune(rs[j]) {
					j++
				}
			}
			if w := string(rs[i:j]); !spaced && strings.Trim(w, ".") == "" {
				for range w {
					emit(token{kind: tPunct, text: ".", raw: "."})
				}
			} else {
				emit(classifyASCII(string(rs[i:j]))...)
			}
			i = j
		default:
			if w := longestCJK(rs[i:]); w != "" {
				emit(token{kind: tCJK, text: w, raw: w})
				i += utf8.RuneCountInString(w)
				continue
			}
			if strings.ContainsRune(cnDigits, r) {
				j := i
				for j < len(rs) && strings.ContainsRune(cnDigits, rs[j]) {
					j++
				}
				if n, ok := parseChineseNumber(string(rs[i:j])); ok {
					emit(token{kind: tNum, text: string(rs[i:j]), raw: string(rs[i:j]), num: float64(n)})
					i = j
					continue
				}
			}
			if n := len(toks); n > 0 && toks[n-1].kind == tHan && !spaced {
				toks[n-1].text += string(r)
				toks[n-1].raw += string(r)
			} else {
				emit(token{kind: tHan, text: string(r), raw: string(r)})
			}
			i++
		}
	}
	return toks, nil
}

// TestTokenizeMatchesOldTokenizer compares the tokenizer with tokenizeOld
// on random mixes of the characters its branches care about.
func TestTokenizeMatchesOldTokenizer(t *testing.T) {
	alphabet := []string{"鑫", "錯", "誤", "零", "〇", "一", "十", "百", "千", "兩", "九", "包含", "檔", "檔名", "最近", "天",
		"或", "和", "以上", "前", "個", "年", "月", "日", " ", "  ", "\t", "\u3000", "a", "go", "x", "1", "10mb", ".", "..",
		"/", "~/", "./", "，", "、", "。", "?", "？", "!", "(", "）", "\"", "「", "」", "'", "<", ">=", "≥", "＜", "\u00e9",
		"\u0301", "😀", "\ufffd", "\u200b", "k", "\u212a", "\u0130"}
	r := rand.New(rand.NewSource(5))
	for it := 0; it < 20000; it++ {
		var b strings.Builder
		for k := r.Intn(24); k > 0; k-- {
			b.WriteString(alphabet[r.Intn(len(alphabet))])
		}
		in := b.String()
		want, werr := tokenizeOld(in)
		got, gerr := tokenize(in)
		if (werr == nil) != (gerr == nil) || (werr != nil && werr.Error() != gerr.Error()) || !reflect.DeepEqual(got, want) {
			t.Fatalf("tokenize(%q):\n got  %+v %v\n want %+v %v", in, got, gerr, want, werr)
		}
	}
	for _, in := range fuzzSeeds {
		want, werr := tokenizeOld(in)
		got, gerr := tokenize(in)
		if (werr == nil) != (gerr == nil) || !reflect.DeepEqual(got, want) {
			t.Fatalf("tokenize(%q):\n got  %+v %v\n want %+v %v", in, got, gerr, want, werr)
		}
	}
}

// extNounAheadOld is extNounAhead before A7 (B6), without memoization.
func extNounAheadOld(p *parser, list bool) bool {
	for i := p.pos; i < len(p.toks) && isExtCandidate(p.toks[i], true, false); {
		i++
		if _, n := p.matchAt(i, fileNouns); n > 0 {
			return true
		}
		_, n := p.matchAt(i, listSeps)
		if n == 0 || !list {
			return false
		}
		i += n
	}
	return false
}

// TestExtNounAheadMemoMatchesOld asks extNounAhead at every position, in a
// random order, and compares with the unmemoized version.
func TestExtNounAheadMemoMatchesOld(t *testing.T) {
	ins := []string{"exclude a and b and go files", "go 和 md 和 txt 檔", "go, md, txt", "a b c d files", "排除 x 和 y 和 z 目錄",
		"exclude vendor and go files and md", "go or md or txt files", "go 和 和 md 檔", excludeList(600), "images and go files"}
	r := rand.New(rand.NewSource(3))
	for _, in := range ins {
		toks, err := tokenize(in)
		if err != nil {
			t.Fatal(err)
		}
		p := &parser{toks: toks, q: &Query{}}
		for round := 0; round < 3; round++ {
			for _, pos := range r.Perm(len(toks) + 1) {
				p.pos = pos
				for _, list := range []bool{true, false} {
					want := extNounAheadOld(p, list)
					if got := p.extNounAhead(list); got != want {
						t.Fatalf("%q pos %d list=%v: got %v, want %v", in, pos, list, got, want)
					}
				}
			}
		}
	}
}

// TestChineseNumberSuffixesMatchParse checks the one-pass suffix analysis
// (B7) against parseChineseNumber: every run of Chinese digits up to four
// long, and random longer runs.
func TestChineseNumberSuffixesMatchParse(t *testing.T) {
	digits := []rune(cnDigits)
	check := func(run []rune) {
		got := chineseNumberSuffixes(run)
		for i := range run {
			if _, want := parseChineseNumber(string(run[i:])); got[i] != want {
				t.Fatalf("chineseNumberSuffixes(%q)[%d] = %v, parseChineseNumber(%q) ok = %v", string(run), i, got[i], string(run[i:]), want)
			}
		}
	}
	var gen func(prefix []rune, n int)
	gen = func(prefix []rune, n int) {
		if len(prefix) > 0 {
			check(prefix)
		}
		if n == 0 {
			return
		}
		for _, d := range digits {
			gen(append(slices.Clip(prefix), d), n-1)
		}
	}
	gen(nil, 4)
	r := rand.New(rand.NewSource(11))
	for it := 0; it < 20000; it++ {
		run := make([]rune, 1+r.Intn(30))
		for i := range run {
			run[i] = digits[r.Intn(len(digits))]
		}
		check(run)
	}
}
