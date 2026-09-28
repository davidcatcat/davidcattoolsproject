package query

import (
	"errors"
	"math"
	"os/exec"
	"path"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"
	"unicode"
	"unicode/utf8"
)

// fuzzNow anchors every relative time in the fuzz and property tests.
var fuzzNow = time.Date(2026, 9, 28, 12, 0, 0, 0, time.FixedZone("UTC+8", 8*3600))

// fuzzSeeds are added to every string fuzz target. The files under
// testdata/fuzz hold the larger reproducers (B4, B5, B6 and friends).
var fuzzSeeds = []string{
	// empty, blank and punctuation only
	"", " ", "  \t\n ", "?", "？", "。", "!", "（", "）", "()", "“", "”", `"`, `""`, "''", "「」",
	// documented examples, Chinese and English
	"找 ovpn 檔", "go files", "*.md", "go 或 md 檔", "副檔名 heic", "圖片", "images",
	"檔名是 application.yml", "檔名包含 config", `filename matches "app-*.yml"`, "檔名以 app- 開頭", "name ends with .bak",
	`containing "text"`, `包含 "文字" 不分大小寫`, "內容有「錯誤」", "today", "昨天", "本月", "2026-09-28", "9 月",
	"9/1 到 9/15", "最近 6 小時", "last 7 days", "3 天前", "larger than 10MB", "at most 1KB", "大於 10MB", "10MB 以上",
	"top 10", "最多 10 個", "10 largest", "最大的 5 個", "newest 3", "最新的 3 個", "exclude node_modules and .git",
	"排除 vendor、build 目錄", "排除 src/gen", "今天或昨天", `包含 "ERROR" 或 "FATAL"`, "大於 1GB 或小於 1KB",
	"find pem files under /etc", "go files containing TODO modified in the last 7 days",
	"10 largest files excluding node_modules and .git", "找今天或昨天修改，而且包含 \"ERROR\" 或 \"FATAL\" 的 log",
	"找昨天 23:00 到今天 02:00 修改的 log", "files modified on or after Sep 1", "找 2026-09-01 以前修改的檔案",
	"昨天 23:00 到今天 02:00 或 今天 05:00 到 06:00", "找 2026 年 9 月 28 日改了什麼檔案", "檔名以 app- 開頭或以 api- 開頭",
	// A1 punctuation cases
	`log files !containing "ERROR"`, "larger than 1GB or (smaller than 1KB and modified today)", "filename matches core.?",
	"filename starts with app?", "包含 錯誤.", "包含 錯誤。", "包含 錯誤？", "files containing TODO?", "找 go 檔？", "find go files?",
	"檔名是 core.", "排除 vendor.", "*.log?", "副檔名 heic?", `包含 "x".`, "找 go 檔..", "在 . 找 go 檔", "在 .. 找 go 檔",
	"排除 .", "排除 ./", "排除 ..", "排除 /", "排除 ./src/.", "exclude . dir", "包含 ./a/.", "go files ! today", "go files ？ today",
	"¿go files", "go files ﹖", "go files ‼",
	// A3 hostile paths and exclude names
	`在 "~/My Docs" 找 pdf 檔`, "在 ~/文件 找 pdf 檔", "in ../..", "in /tmp/-rf", `in "/tmp/a b"`, `排除 "-rf" 目錄`,
	`排除 "a b" 目錄`, `排除 "it's" 目錄`, `排除 "[abc]" 目錄`, `排除 "*" 目錄`, `排除 "中文 目錄" 目錄`, "排除 \"a\nb\" 目錄",
	`排除 "./目錄 dir/子 sub"`, `檔名是 "-rf.txt"`, `檔名包含 "$(id)"`, "檔名包含 \"`id`\"", `檔名符合 "[abc].txt"`,
	// A4 explain edge cases
	"包含 \"a\nb\"", `包含 "it's"`, `包含 "\"`, `包含 "$HOME"`, `檔名符合 "[!a]*"`, `檔名符合 "[^a]*"`, `檔名符合 "a\*"`,
	`filename matches "[abc"`, "top 99999999999", "larger than 1e400 bytes", "最近 99999999999999999999 年",
	"larger than 9223372036854775807 bytes", "larger than 8388608 TB", "top 2147483647", "top 2147483648", "最多 0 個",
	// A5 depth-like and A6 tie/cutoff queries
	"最大的 1 個", "newest 1", "5 largest files", "5 newest files", "最新的 2147483647 個", "top 1 go files",
	// invalid UTF-8 (B1)
	"\xff", "\xff\xfe", "containing \"\xff\"", "containing \xff", "exclude \"\xff\" dir, find go files", "in /tmp/\xff",
	"go files \xc3", "\xed\xa0\x80", "\xc0\xaf", "\xf4\x90\x80\x80", "包含 \"\ufffd\"",
	// NFC / NFD and other Unicode
	"\u00e9", "e\u0301", "找 \u00e9 檔", "找 e\u0301 檔", "檔名包含 \"caf\u00e9\"", "檔名包含 \"cafe\u0301\"", "副檔名 caf\u00e9", "*.cafe\u0301", "caf\u00e9 檔", "cafe\u0301 檔",
	"包含 😀", "找 😀 檔", "包含 e\u0301\u0301\u0301", "go\u200bfiles", "go\u00a0files", "go\u3000files", "go\u0085files",
	"\ufeffgo files", "go files\u200d", "包含 \"\u202e\"",
	// shell metacharacters
	"包含 $(rm -rf /)", "包含 `id`", "包含 a;b", "包含 a|b", "包含 a&b", "檔名包含 *", "in /tmp; rm -rf /",
	// mixed, OR, NOT-like words, numbers, whitespace
	"找 go files 最近 7 days", "今天 or 昨天", "or", "或", "today or", "or today", "today and or yesterday", "today or and yesterday",
	"not go files", "without TODO", "不包含 TODO", "除了 vendor", "except vendor dir", "larger than 1" + strings.Repeat("0", 400) + "MB",
	strings.Repeat("9", 40) + " 年", "最近 " + strings.Repeat("九千", 20) + " 天", "go\x00files", "包含 \"a\x00b\"",
	"go   files", "go\tfiles", "go\nfiles", "go\r\nfiles", "   go files   ", "零零", "鑫零零零", "一零", "零十",
}

func FuzzParse(f *testing.F) {
	for _, s := range fuzzSeeds {
		f.Add(s)
	}
	f.Fuzz(func(t *testing.T, in string) {
		if len(in) > 8192 {
			return
		}
		q1, err1 := Parse(in, fuzzNow)
		q2, err2 := Parse(in, fuzzNow)
		if (err1 == nil) != (err2 == nil) {
			t.Fatalf("Parse(%q) is not deterministic: %v vs %v", in, err1, err2)
		}
		if err1 != nil {
			var pe1, pe2 *Error
			if !errors.As(err1, &pe1) || !errors.As(err2, &pe2) {
				t.Fatalf("Parse(%q) error %T is not a *query.Error", in, err1)
			}
			if err1.Error() != err2.Error() || pe1.Message(Chinese) != pe2.Message(Chinese) {
				t.Fatalf("Parse(%q) error is not deterministic:\n%q\n%q", in, err1, err2)
			}
			return
		}
		if !reflect.DeepEqual(q1, q2) {
			t.Fatalf("Parse(%q) is not deterministic:\n%+v\n%+v", in, q1, q2)
		}
		checkQueryInvariants(t, in, q1)
	})
}

// checkQueryInvariants checks what every successful Parse guarantees.
// Not checked, because it does not hold: "IgnoreCase implies a content
// condition" ("go files ignore case" is accepted); only a filename
// condition with ignore case and no content is rejected.
func checkQueryInvariants(t *testing.T, in string, q *Query) {
	t.Helper()
	if !utf8.ValidString(in) {
		t.Fatalf("Parse accepted invalid UTF-8 %q: %+v", in, q)
	}
	if q.Lang != DetectLang(in) || !q.Now.Equal(fuzzNow) {
		t.Fatalf("%q: Lang %v / Now %v", in, q.Lang, q.Now)
	}
	seen := map[string]bool{}
	for _, e := range q.Extensions {
		if e == "" || strings.HasPrefix(e, ".") || e != strings.ToLower(e) || seen[e] {
			t.Fatalf("%q: bad extension %q in %q", in, e, q.Extensions)
		}
		seen[e] = true
	}
	seen = map[string]bool{}
	for _, d := range q.ExcludeDirs {
		if d == "" || seen[d] {
			t.Fatalf("%q: bad exclude %q in %q", in, d, q.ExcludeDirs)
		}
		seen[d] = true
	}
	checkNames := func(ns []NameCond) {
		for _, n := range ns {
			if n.Pattern == "" {
				t.Fatalf("%q: empty filename pattern", in)
			}
			if n.Kind == NameGlob {
				for _, name := range []string{"", "a", "a.txt", n.Pattern} {
					if _, err := path.Match(n.Pattern, name); err != nil {
						t.Fatalf("%q: invalid glob %q accepted", in, n.Pattern)
					}
				}
			}
		}
	}
	checkNames(q.Names)
	if slices.Contains(q.Contents, "") {
		t.Fatalf("%q: empty content term", in)
	}
	hasContent, hasName := len(q.Contents) > 0, len(q.Names) > 0
	for _, g := range q.AnyOf {
		switch g.Kind {
		case KindContent:
			hasContent = true
			if len(g.Contents) == 0 || slices.Contains(g.Contents, "") {
				t.Fatalf("%q: bad content group %q", in, g.Contents)
			}
		case KindName:
			hasName = true
			if len(g.Names) == 0 {
				t.Fatalf("%q: empty filename group", in)
			}
			checkNames(g.Names)
		case KindTime:
			if len(g.Times) == 0 {
				t.Fatalf("%q: empty time group", in)
			}
		case KindSize:
			if len(g.Sizes) == 0 {
				t.Fatalf("%q: empty size group", in)
			}
		default:
			t.Fatalf("%q: OR group of kind %v", in, g.Kind)
		}
	}
	if q.IgnoreCase && hasName && !hasContent {
		t.Fatalf("%q: ignore case accepted with only filename conditions", in)
	}
	if q.Limit < 0 || q.Limit > math.MaxInt32 {
		t.Fatalf("%q: limit %d", in, q.Limit)
	}
	if q.Sort != SortNone && q.Sort != SortLargest && q.Sort != SortNewest {
		t.Fatalf("%q: sort %v", in, q.Sort)
	}
	if q.MinDepth != 0 || q.MaxDepth != 0 || q.HasMaxDepth || q.RootSymlink {
		t.Fatalf("%q: Parse set a field only the CLI sets: %+v", in, q)
	}
	if !q.ModifiedAfter.IsZero() && !q.ModifiedBefore.IsZero() && !q.ModifiedAfter.Before(q.ModifiedBefore) {
		t.Fatalf("%q: contradictory time bounds accepted", in)
	}
	if !timeSatisfiable(q) {
		t.Fatalf("%q: unsatisfiable time groups accepted", in)
	}
	for _, p := range []Platform{PlatformFor("darwin"), PlatformFor("linux")} {
		_ = Explain(q, p)
		_ = FindCommand(q, p)
	}
	_ = FormatConditions(Conditions(q, English))
	_ = FormatConditions(Conditions(q, Chinese))
}

func FuzzTokenize(f *testing.F) {
	for _, s := range fuzzSeeds {
		f.Add(s)
	}
	f.Fuzz(func(t *testing.T, in string) {
		t1, e1 := tokenize(in)
		t2, e2 := tokenize(in)
		if (e1 == nil) != (e2 == nil) || !reflect.DeepEqual(t1, t2) || (e1 != nil && e1.Error() != e2.Error()) {
			t.Fatalf("tokenize(%q) is not deterministic", in)
		}
		if len(in) <= 4096 {
			// B5/B7: the linear tokenizer must agree with the old one exactly.
			old, eOld := tokenizeOld(in)
			if (eOld == nil) != (e1 == nil) || !reflect.DeepEqual(old, t1) || (eOld != nil && eOld.Error() != e1.Error()) {
				t.Fatalf("tokenize(%q) differs from the old tokenizer:\n old %+v %v\n new %+v %v", in, old, eOld, t1, e1)
			}
		}
		if len(in) <= 8192 {
			_, _ = Parse(in, fuzzNow) // must not panic
		}
		if e1 != nil || !utf8.ValidString(in) {
			return // Parse rejects invalid UTF-8 before tokenizing
		}
		if len(t1) > len(in) {
			t.Fatalf("tokenize(%q): %d tokens from %d bytes", in, len(t1), len(in))
		}
		pos := 0
		for i, tk := range t1 {
			if tk.raw == "" {
				t.Fatalf("tokenize(%q): token %d has empty raw text: %+v", in, i, tk)
			}
			if tk.kind == tPunct && (utf8.RuneCountInString(tk.text) != 1 || !strings.Contains(punctChars+".", tk.text)) {
				t.Fatalf("tokenize(%q): punctuation token %q", in, tk.text)
			}
			rest := in[pos:]
			gap := len(rest) - len(strings.TrimLeftFunc(rest, unicode.IsSpace))
			if !strings.HasPrefix(rest[gap:], tk.raw) {
				t.Fatalf("tokenize(%q): token %d raw %q is not next in the input at byte %d (%q)", in, i, tk.raw, pos, rest)
			}
			if want := i == 0 || gap > 0; tk.spaceBefore != want {
				t.Fatalf("tokenize(%q): token %d %q spaceBefore=%v, gap=%d", in, i, tk.raw, tk.spaceBefore, gap)
			}
			pos += gap + len(tk.raw)
		}
		if strings.TrimFunc(in[pos:], unicode.IsSpace) != "" {
			t.Fatalf("tokenize(%q) dropped trailing input %q", in, in[pos:])
		}
	})
}

// shellSafe is the set shellQuote leaves unquoted.
const shellSafe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./:@%+=,"

// shellQuoteSeeds are strings a POSIX shell treats specially.
var shellQuoteSeeds = []string{"", "a", "'", "''", "a'b", `"`, `\`, "$HOME", "$(id)", "`id`", " ", "a b", "\t", "\n", "\r",
	"-rf", "--help", "*", "?", "[abc]", ";", "|", "&", "!", "~", "#x", "\u00e9", "e\u0301", "\xff\xfe", "中文", "😀", `'\''`,
	strings.Repeat("'", 50)}

func FuzzShellQuote(f *testing.F) {
	for _, s := range shellQuoteSeeds {
		f.Add(s)
	}
	f.Fuzz(func(t *testing.T, s string) {
		if strings.IndexByte(s, 0) >= 0 {
			// Unsupported: no argv entry or shell word can hold a NUL byte, and
			// file names cannot contain one. TestShellQuoteNUL pins that
			// shellQuote makes no claim about it.
			return
		}
		checkShellQuote(t, s)
	})
}

// checkShellQuote checks that shellQuote(s) is either s itself (only safe
// bytes) or '…' segments joined by \' that decode back to s.
func checkShellQuote(t *testing.T, s string) {
	t.Helper()
	q := shellQuote(s)
	if q == s {
		if s == "" || strings.Trim(s, shellSafe) != "" {
			t.Fatalf("shellQuote(%q) left an unsafe string unquoted", s)
		}
		return
	}
	var out strings.Builder
	for i := 0; i < len(q); {
		switch {
		case q[i] == '\'':
			j := strings.IndexByte(q[i+1:], '\'')
			if j < 0 {
				t.Fatalf("shellQuote(%q) = %q: unterminated quote", s, q)
			}
			out.WriteString(q[i+1 : i+1+j])
			i += j + 2
		case strings.HasPrefix(q[i:], `\'`):
			out.WriteByte('\'')
			i += 2
		default:
			t.Fatalf("shellQuote(%q) = %q: unexpected byte %q at %d", s, q, q[i], i)
		}
	}
	if out.String() != s {
		t.Fatalf("shellQuote(%q) = %q decodes to %q", s, q, out.String())
	}
}

// TestShellQuoteNUL records that NUL is outside shellQuote's contract: the
// result still contains the NUL, which no shell word can carry.
func TestShellQuoteNUL(t *testing.T) {
	if q := shellQuote("a\x00b"); !strings.Contains(q, "\x00") {
		t.Errorf("shellQuote(%q) = %q; expected the NUL to be kept (unsupported, not escaped)", "a\x00b", q)
	}
}

// TestShellQuoteShellRoundTrip runs the quoted words through a real
// /bin/sh: printf '%s' <quoted> must print the original bytes exactly.
func TestShellQuoteShellRoundTrip(t *testing.T) {
	for _, s := range shellQuoteSeeds {
		out, err := exec.Command("/bin/sh", "-c", "printf '%s' "+shellQuote(s)).Output()
		if err != nil || string(out) != s {
			t.Errorf("sh printf %s: got %q (err %v), want %q", shellQuote(s), out, err, s)
		}
	}
}
