package query

import (
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

// Punctuation outside quotes must never be skipped when skipping it could
// change the meaning of a query: "!" could be NOT, parentheses would group
// conditions, and punctuation glued to an unquoted value may be part of it.

const (
	msgParen   = "parentheses are not supported"
	msgBang    = `"!" may mean NOT, which is not supported`
	msgQuery   = `"?" is only allowed at the end of the query`
	msgGlued   = "punctuation right after an unquoted value is ambiguous"
	msgDotExcl = `cannot exclude "." or ".."`

	zhParen = "目前不支援括號"
	zhBang  = "「!」可能表示否定（NOT），目前不支援"
	zhQuery = "「?」只能放在查詢的最後"
	zhGlued = "標點緊接在沒有引號的值後面，無法確定是否屬於這個值"
)

func parseErr(t *testing.T, in string) *Error {
	t.Helper()
	q, err := Parse(in, now)
	if err == nil {
		t.Fatalf("Parse(%q) = %+v, want an error", in, *q)
	}
	var e *Error
	if !errors.As(err, &e) {
		t.Fatalf("Parse(%q) error %T is not *Error: %v", in, err, err)
	}
	return e
}

// TestPunctuationBugs pins the v0.4.0 misreadings: each used to parse into a
// query with a different meaning and must now be rejected.
func TestPunctuationBugs(t *testing.T) {
	tests := []struct {
		in, en, zh string
	}{
		{`log files !containing "ERROR"`, msgBang, zhBang},
		{`log 檔 ！包含 "ERROR"`, msgBang, zhBang},
		{"larger than 1GB or (smaller than 1KB and modified today)", msgParen, zhParen},
		{"大於 1GB 或（小於 1KB 且今天修改）", msgParen, zhParen},
		{"filename matches core.?", msgGlued + `: "core.?"`, zhGlued + "：「core.?」"},
		{"filename matches app-?.log", msgGlued + `: "app-?"`, zhGlued + "：「app-?」"},
		{"filename starts with app?", msgGlued + `: "app?"`, zhGlued + "：「app?」"},
		{"filename is core.", msgGlued + `: "core."`, zhGlued + "：「core.」"},
		{"包含 錯誤.", msgGlued + `: "錯誤."`, zhGlued + "：「錯誤.」"},
		{"檔名是 設定.", msgGlued + `: "設定."`, zhGlued + "：「設定.」"},
		{"包含 錯誤？", msgGlued + `: "錯誤？"`, zhGlued + "：「錯誤？」"},
		{"files containing TODO?", msgGlued + `: "TODO?"`, zhGlued + "：「TODO?」"},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			e := parseErr(t, tt.in)
			if got := e.Message(English); !strings.Contains(got, tt.en) {
				t.Errorf("English message %q does not contain %q", got, tt.en)
			}
			if got := e.Message(Chinese); !strings.Contains(got, tt.zh) {
				t.Errorf("Chinese message %q does not contain %q", got, tt.zh)
			}
		})
	}
}

// TestReservedPunctuationPositions puts each reserved character first, in
// the middle, last and glued to an unquoted value.
func TestReservedPunctuationPositions(t *testing.T) {
	type pos struct{ name, in string }
	positions := func(c string) []pos {
		return []pos{
			{"first", c + " go files"},
			{"middle", "go files " + c + " modified today"},
			{"last", "go files " + c},
			{"glued", "containing TODO" + c},
		}
	}
	want := map[string]map[string]string{ // char -> position -> English message ("" = allowed)
		"(": {"first": msgParen, "middle": msgParen, "last": msgParen, "glued": msgGlued},
		")": {"first": msgParen, "middle": msgParen, "last": msgParen, "glued": msgGlued},
		"（": {"first": msgParen, "middle": msgParen, "last": msgParen, "glued": msgGlued},
		"）": {"first": msgParen, "middle": msgParen, "last": msgParen, "glued": msgGlued},
		"!": {"first": msgBang, "middle": msgBang, "last": msgBang, "glued": msgGlued},
		"！": {"first": msgBang, "middle": msgBang, "last": msgBang, "glued": msgGlued},
		"?": {"first": msgQuery, "middle": msgQuery, "last": "", "glued": msgGlued},
		"？": {"first": msgQuery, "middle": msgQuery, "last": "", "glued": msgGlued},
	}
	for c, byPos := range want {
		for _, p := range positions(c) {
			t.Run(c+"/"+p.name, func(t *testing.T) {
				wantMsg := byPos[p.name]
				q, err := Parse(p.in, now)
				if wantMsg == "" {
					if err != nil {
						t.Fatalf("Parse(%q) error: %v", p.in, err)
					}
					if !reflect.DeepEqual(q.Extensions, []string{"go"}) {
						t.Errorf("Parse(%q) extensions = %v, want [go]", p.in, q.Extensions)
					}
					return
				}
				if err == nil {
					t.Fatalf("Parse(%q) = %+v, want error %q", p.in, *q, wantMsg)
				}
				if !strings.Contains(err.Error(), wantMsg) {
					t.Errorf("Parse(%q) error %q, want %q", p.in, err, wantMsg)
				}
			})
		}
	}
}

// TestPunctuationAllowed pins the punctuation that keeps working.
func TestPunctuationAllowed(t *testing.T) {
	sep28 := time.Date(2026, 9, 28, 0, 0, 0, 0, time.UTC)
	tests := []struct {
		in   string
		want Query
	}{
		{"find go files?", Query{Extensions: []string{"go"}}},
		{"找 go 檔？", Query{Extensions: []string{"go"}}},
		{"找 ovpn 檔 ?", Query{Extensions: []string{"ovpn"}}},
		{`files named "x"?`, Query{Names: []NameCond{{NameExact, "x"}}}},
		{`包含 "x"。`, Query{Contents: []string{"x"}}},
		{`包含 "錯誤？"`, Query{Contents: []string{"錯誤？"}}},
		{"find go files.", Query{Extensions: []string{"go"}}},
		{"go files modified today.", Query{Extensions: []string{"go"},
			ModifiedAfter: time.Date(2026, 9, 27, 0, 0, 0, 0, time.UTC), ModifiedBefore: sep28}},
		{"files changed on Sep 28, 2026", Query{ModifiedAfter: sep28, ModifiedBefore: sep28.AddDate(0, 0, 1)}},
		{"包含 錯誤訊息，大於 1KB", Query{Contents: []string{"錯誤訊息"}, Sizes: []SizeCond{{">", 1 << 10}}}},
		{"包含 錯誤。", Query{Contents: []string{"錯誤"}}},
		{"包含TODO且大於1MB", Query{Contents: []string{"TODO"}, Sizes: []SizeCond{{">", 1 << 20}}}},
		{`filename matches "app-?.log"`, Query{Names: []NameCond{{NameGlob, "app-?.log"}}}},
		{`filename matches "core.?"`, Query{Names: []NameCond{{NameGlob, "core.?"}}}},
		{`filename is "core."`, Query{Names: []NameCond{{NameExact, "core."}}}},
		{`containing "(x)"`, Query{Contents: []string{"(x)"}}},
		{`containing "!x"`, Query{Contents: []string{"!x"}}},
		{"go files in .", Query{Root: ".", Extensions: []string{"go"}}},
		{"在 . 找 go 檔", Query{Root: ".", Extensions: []string{"go"}}},
		{"go files .", Query{Root: ".", Extensions: []string{"go"}}},
		// a lone "." glued to a quoted value is a sentence period, not the root
		{`containing "x".`, Query{Contents: []string{"x"}}},
		{`包含 "x".`, Query{Contents: []string{"x"}}},
		{`exclude "vendor".`, Query{ExcludeDirs: []string{"vendor"}}},
		{"排除 vendor，大於 1KB", Query{ExcludeDirs: []string{"vendor"}, Sizes: []SizeCond{{">", 1 << 10}}}},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			got, err := Parse(tt.in, now)
			if err != nil {
				t.Fatalf("Parse(%q) error: %v", tt.in, err)
			}
			tt.want.Lang = DetectLang(tt.in)
			tt.want.Now = now
			if !reflect.DeepEqual(*got, tt.want) {
				t.Errorf("Parse(%q)\n got  %+v\n want %+v", tt.in, *got, tt.want)
			}
		})
	}
}

// TestGluedPunctuation covers punctuation glued to unquoted filename,
// content and exclude values, including soft content and OR alternatives.
func TestGluedPunctuation(t *testing.T) {
	tests := []struct {
		in, token, hintEn string
	}{
		{"filename is core.", "core.", `filename is "core" or filename is "core."`},
		{"filename is nginx.conf.", "nginx.conf.", `filename is "nginx.conf" or filename is "nginx.conf."`},
		{"filename matches app-?.log", "app-?", `filename matches "app-" or filename matches "app-?.log"`},
		{"name contains a!b", "a!", `name contains "a" or name contains "a!b"`},
		{"files named core(1)", "core(", `named "core" or named "core(1)"`},
		{"檔名是 設定.", "設定.", `檔名是 "設定" 或 檔名是 "設定."`},
		{"檔名包含 prod 或 staging?", "staging?", `"staging" 或 "staging?"`},
		{"containing TODO.", "TODO.", `containing "TODO" or containing "TODO."`},
		{"containing TODO?", "TODO?", `containing "TODO" or containing "TODO?"`},
		{"包含 錯誤.", "錯誤.", `包含 "錯誤" 或 包含 "錯誤."`},
		{"包含 ERROR 或 FATAL?", "FATAL?", `包含 "FATAL" 或 包含 "FATAL?"`},
		{"有 錯誤？ 的檔案", "錯誤？", `包含 "錯誤" 或 包含 "錯誤？"`},
		{"excluding vendor.", "vendor.", `excluding "vendor" or excluding "vendor."`},
		{"排除 vendor！", "vendor！", `排除 "vendor" 或 排除 "vendor！"`},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			e := parseErr(t, tt.in)
			if !strings.Contains(e.en, msgGlued) || !strings.Contains(e.zh, zhGlued) {
				t.Fatalf("Parse(%q) error %q, want %q", tt.in, e.Message(English), msgGlued)
			}
			if e.Token != tt.token {
				t.Errorf("token = %q, want %q", e.Token, tt.token)
			}
			if !strings.Contains(e.hintEn, tt.hintEn) && !strings.Contains(e.hintZh, tt.hintEn) {
				t.Errorf("hints %q / %q do not offer %q", e.hintEn, e.hintZh, tt.hintEn)
			}
		})
	}
}

// TestPunctuationEdgeCases pins what the design yields for the less
// obvious combinations.
func TestPunctuationEdgeCases(t *testing.T) {
	tests := []struct {
		in, wantMsg string
	}{
		{"find go files?.", msgQuery},    // "?" is no longer the last token
		{"find go files??", msgQuery},    // only one trailing "?"
		{"找 go 檔？。", msgQuery},           // a period after "？" makes it not last
		{`"x" ? go files`, msgQuery},     // spaced, not last
		{"exclude .", msgDotExcl},        // spaced "." is a word, but not an exclude name
		{"exclude ..", msgDotExcl},       //
		{"exclude vendor .", msgDotExcl}, // space-separated list
		{`exclude "."`, msgDotExcl},      // quoting does not make it a directory name
		{"排除 ..", msgDotExcl},            //
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			e := parseErr(t, tt.in)
			if got := e.Message(English); !strings.Contains(got, tt.wantMsg) {
				t.Errorf("Parse(%q) error %q, want %q", tt.in, got, tt.wantMsg)
			}
		})
	}
}

// TestGluedDotToken checks the tokenizer: a lone "." glued to the previous
// token is punctuation, a spaced one is still a word (the path ".").
func TestGluedDotToken(t *testing.T) {
	tests := []struct {
		in   string
		want kind
	}{
		{`"x".`, tPunct},
		{"錯誤.", tPunct},
		{"in .", tWord},
		{"在 .", tWord},
		{".", tWord},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			toks, err := tokenize(tt.in)
			if err != nil {
				t.Fatal(err)
			}
			last := toks[len(toks)-1]
			if last.text != "." || last.kind != tt.want {
				t.Errorf("last token = %+v, want kind %d text %q", last, tt.want, ".")
			}
		})
	}
}

// TestNoStaleVersionInMessages keeps old version numbers out of errors.
func TestNoStaleVersionInMessages(t *testing.T) {
	for _, in := range []string{"files without TODO", "except vendor", "exclude go files", "排除 .go"} {
		e := parseErr(t, in)
		for _, l := range []Lang{English, Chinese} {
			if msg := e.Message(l); strings.Contains(msg, "v0.") {
				t.Errorf("Parse(%q) message still names a version: %q", in, msg)
			}
		}
	}
}

// QA round 1 regressions.

// TestGluedDotRuns: a run of dots glued to the previous token is sentence
// punctuation, never the path "." or "..", and never part of a value.
func TestGluedDotRuns(t *testing.T) {
	ok := []struct {
		in   string
		want Query
	}{
		{"找 go 檔..", Query{Extensions: []string{"go"}}},
		{"找 go 檔...", Query{Extensions: []string{"go"}}},
		{`"x"..`, Query{Contents: []string{"x"}}},
		{"「錯誤」..", Query{Contents: []string{"錯誤"}}},
		{"今天..", Query{ModifiedAfter: time.Date(2026, 9, 27, 0, 0, 0, 0, time.UTC),
			ModifiedBefore: time.Date(2026, 9, 28, 0, 0, 0, 0, time.UTC)}},
		{"in ..", Query{Root: ".."}},
		{"go files in ..", Query{Root: "..", Extensions: []string{"go"}}},
		{"在 .. 找 go 檔", Query{Root: "..", Extensions: []string{"go"}}},
	}
	for _, tt := range ok {
		t.Run(tt.in, func(t *testing.T) {
			got, err := Parse(tt.in, now)
			if err != nil {
				t.Fatalf("Parse(%q) error: %v", tt.in, err)
			}
			tt.want.Lang = DetectLang(tt.in)
			tt.want.Now = now
			if !reflect.DeepEqual(*got, tt.want) {
				t.Errorf("Parse(%q)\n got  %+v\n want %+v", tt.in, *got, tt.want)
			}
		})
	}
	bad := []struct{ in, token string }{
		{"包含 錯誤..", "錯誤.."},
		{"檔名是 設定..", "設定.."},
		{"containing TODO..", "TODO.."},
		{"excluding vendor..", "vendor.."},
	}
	for _, tt := range bad {
		t.Run(tt.in, func(t *testing.T) {
			e := parseErr(t, tt.in)
			if !strings.Contains(e.en, msgGlued) || e.Token != tt.token {
				t.Errorf("Parse(%q) error %q token %q, want %q with token %q", tt.in, e.Message(English), e.Token, msgGlued, tt.token)
			}
		})
	}
	t.Run("在..", func(t *testing.T) {
		if e := parseErr(t, "在.."); !strings.Contains(e.en, "cannot understand") {
			t.Errorf("error %q", e.Message(English))
		}
	})
	t.Run("tokens", func(t *testing.T) {
		toks, err := tokenize("檔..")
		if err != nil {
			t.Fatal(err)
		}
		if len(toks) != 3 || toks[1].kind != tPunct || toks[2].kind != tPunct || toks[1].spaceBefore || toks[2].spaceBefore {
			t.Errorf("tokens = %+v, want 檔 then two glued \".\" punctuation tokens", toks)
		}
	})
}

// TestExcludeNothing: exclude names that would exclude nothing (or the
// whole search) are rejected; real relative paths keep working.
func TestExcludeNothing(t *testing.T) {
	for in, want := range map[string]string{
		"exclude ./":     msgDotExcl,
		`exclude "./"`:   msgDotExcl,
		"exclude ../":    msgDotExcl,
		"排除 ./":          msgDotExcl,
		"exclude ./ dir": msgDotExcl,
		"exclude .//":    msgDotExcl,
		"exclude /":      "an exclude name must not be empty",
		`exclude "/"`:    "an exclude name must not be empty",
		`exclude ""`:     "an exclude name must not be empty",
	} {
		t.Run(in, func(t *testing.T) {
			if e := parseErr(t, in); !strings.Contains(e.Message(English), want) {
				t.Errorf("Parse(%q) error %q, want %q", in, e.Message(English), want)
			}
		})
	}
	t.Run("排除 /", func(t *testing.T) {
		if e := parseErr(t, "排除 /"); !strings.Contains(e.Message(Chinese), "排除名稱不能是空的") {
			t.Errorf("error %q", e.Message(Chinese))
		}
	})
	for in, want := range map[string][]string{
		"exclude ./src/gen": {"./src/gen"},
		"exclude src/gen/":  {"src/gen"},
		"exclude .git":      {".git"},
	} {
		t.Run(in, func(t *testing.T) {
			q, err := Parse(in, now)
			if err != nil {
				t.Fatalf("Parse(%q) error: %v", in, err)
			}
			if !reflect.DeepEqual(q.ExcludeDirs, want) {
				t.Errorf("ExcludeDirs = %v, want %v", q.ExcludeDirs, want)
			}
		})
	}
}

// TestGluedExtensionPunctuation: punctuation glued to an extension is
// ambiguous (a glob or a sentence mark) and is rejected, not dropped.
func TestGluedExtensionPunctuation(t *testing.T) {
	const msgExt = "punctuation right after an extension is ambiguous"
	tests := []struct{ in, token, hint string }{
		{"*.log?", "*.log?", `filename matches "*.log?"`},
		{".log?", ".log?", `filename matches "*.log?"`},
		{"*.log!", "*.log!", `filename matches "*.log!"`},
		{".go or .md?", ".md?", `filename matches "*.md?"`},
		{"副檔名 heic?", "heic?", `檔名符合 "*.heic?"`},
		{"go? files", "go?", `filename matches "*.go?"`},
		{"圖片?", "圖片?", "請拿掉標點"},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			e := parseErr(t, tt.in)
			if !strings.Contains(e.en, msgExt) || !strings.Contains(e.zh, "標點緊接在副檔名後面") {
				t.Fatalf("Parse(%q) error %q, want %q", tt.in, e.Message(English), msgExt)
			}
			if e.Token != tt.token {
				t.Errorf("token = %q, want %q", e.Token, tt.token)
			}
			if !strings.Contains(e.hintEn, tt.hint) && !strings.Contains(e.hintZh, tt.hint) {
				t.Errorf("hints %q / %q do not contain %q", e.hintEn, e.hintZh, tt.hint)
			}
		})
	}
	for in, want := range map[string][]string{
		"find go files?": {"go"},
		"找 go 檔？":        {"go"},
		".log ?":         {"log"},
		"*.log。":         {"log"},
		"找 pdf 檔。":       {"pdf"},
	} {
		t.Run(in, func(t *testing.T) {
			q, err := Parse(in, now)
			if err != nil {
				t.Fatalf("Parse(%q) error: %v", in, err)
			}
			if !reflect.DeepEqual(q.Extensions, want) {
				t.Errorf("Extensions = %v, want %v", q.Extensions, want)
			}
		})
	}
}

// TestPunctuationOnlyQuery: punctuation alone is an empty query, like no
// input; reserved punctuation keeps its own error.
func TestPunctuationOnlyQuery(t *testing.T) {
	for in, want := range map[string]string{
		"?": "empty query", "？": "empty query", ",": "empty query", "。": "empty query",
		"。 ?": "empty query", "? 。": msgQuery, "、，": "empty query",
		"!": msgBang, "(": msgParen, "？ ?": msgQuery,
	} {
		t.Run(in, func(t *testing.T) {
			if e := parseErr(t, in); !strings.Contains(e.Message(English), want) {
				t.Errorf("Parse(%q) error %q, want %q", in, e.Message(English), want)
			}
		})
	}
	// Filler-only queries ("find", "找") already search everything; a
	// trailing "?" does not change that, and a spaced "." is still a root.
	for in, root := range map[string]string{"find": "", "找": "", "and?": "", "find files?": "", ".?": "."} {
		t.Run(in, func(t *testing.T) {
			q, err := Parse(in, now)
			if err != nil {
				t.Fatalf("Parse(%q) error: %v", in, err)
			}
			if q.Root != root {
				t.Errorf("Root = %q, want %q", q.Root, root)
			}
		})
	}
}

// TestWrappedFilenameHints: hints for "以 … 開頭/結尾" keep the closing word
// and never glue a keyword into the suggested value.
func TestWrappedFilenameHints(t *testing.T) {
	tests := []struct{ in, hint string }{
		{"檔名以 app.開頭", `檔名以 "app" 開頭 或 檔名以 "app." 開頭`},
		{"檔名以 app? 開頭", `檔名以 "app" 開頭 或 檔名以 "app?" 開頭`},
		{"檔名以 .bak? 結尾", `檔名以 ".bak" 結尾 或 檔名以 ".bak?" 結尾`},
		{"檔名以 app.結尾", `檔名以 "app" 結尾 或 檔名以 "app." 結尾`},
		{"filename is core.", `filename is "core" or filename is "core."`},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			e := parseErr(t, tt.in)
			if !strings.Contains(e.hintZh, tt.hint) && !strings.Contains(e.hintEn, tt.hint) {
				t.Errorf("hints %q / %q do not contain %q", e.hintZh, e.hintEn, tt.hint)
			}
		})
	}
}

// TestReservedPunctuationHints: each error's hint fits its character.
func TestReservedPunctuationHints(t *testing.T) {
	tests := []struct{ in, en, zh string }{
		{`log files !containing "ERROR"`, `containing "!important"`, `包含 "!important"`},
		{"log 檔 ！包含 錯誤", `containing "!important"`, `包含 "!important"`},
		{"larger than 1GB or (smaller than 1KB)", `containing "(x)"`, `包含 "(x)"`},
		{"find ? go files", `if it is part of a value, quote it, e.g. filename matches "core.?"`,
			`「?」只能放在查詢的最後；如果它是值的一部分，請加引號，例如 檔名符合 "core.?"`},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			e := parseErr(t, tt.in)
			if !strings.Contains(e.hintEn, tt.en) || !strings.Contains(e.hintZh, tt.zh) {
				t.Errorf("hints %q / %q, want %q / %q", e.hintEn, e.hintZh, tt.en, tt.zh)
			}
		})
	}
}

// TestPathDotComponents: a path ending in a "." or ".." component keeps it.
// Before, "../.." lost its last dot and searched one level too shallow.
func TestPathDotComponents(t *testing.T) {
	for _, c := range []struct{ in, root string }{
		{"go files in ../..", "../.."},
		{"在 ../.. 找 go 檔", "../.."},
		{"in ~/..", "~/.."},
		{"in ./src/..", "./src/.."},
		{"in ./src/.", "./src/."},
		{"in ..", ".."},
		{"in ./src", "./src"},
		{"find go files in ./src.", "./src"}, // a sentence period after a path is still dropped
	} {
		q, err := Parse(c.in, dtNow)
		if err != nil {
			t.Fatalf("Parse(%q): %v", c.in, err)
		}
		if q.Root != c.root {
			t.Errorf("Parse(%q).Root = %q, want %q", c.in, q.Root, c.root)
		}
	}
}

// TestDotComponentOnlyForPaths: only path words keep a trailing "." / ".."
// component; in values a glued period is ambiguous (review findings S1, S2).
func TestDotComponentOnlyForPaths(t *testing.T) {
	for _, in := range []string{"包含 a/.", "containing http://x/.", "檔名包含 a/.", "containing a/..",
		"包含 ./a/.", "檔名包含 ./a/..", "exclude vendor/.", "排除 vendor/."} {
		_, err := Parse(in, dtNow)
		var pe *Error
		if !errors.As(err, &pe) || !strings.Contains(pe.Message(English), "punctuation right after an unquoted value is ambiguous") {
			t.Errorf("Parse(%q) = %v; want the glued-punctuation error", in, err)
		}
	}
	for _, c := range []struct{ in, exclude string }{
		{"exclude ./vendor/.", "./vendor"}, // a path exclude keeps its "." component and is normalized later
		{"exclude ./src/gen", "./src/gen"},
	} {
		q, err := Parse(c.in, dtNow)
		if err != nil || len(q.ExcludeDirs) != 1 || strings.TrimSuffix(q.ExcludeDirs[0], "/.") != c.exclude {
			t.Errorf("Parse(%q) = %+v, %v; want exclude %q", c.in, q, err, c.exclude)
		}
	}
}
