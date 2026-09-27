package query

import (
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

var now = time.Date(2026, 9, 27, 15, 30, 0, 0, time.UTC)

func ago(d time.Duration) time.Time { return now.Add(-d) }

func TestParse(t *testing.T) {
	tests := []struct {
		in   string
		want Query
	}{
		// extensions
		{"find go files", Query{Extensions: []string{"go"}}},
		{"*.md", Query{Extensions: []string{"md"}}},
		{".go and .md files", Query{Extensions: []string{"go", "md"}}},
		{"找出 pdf 檔", Query{Extensions: []string{"pdf"}}},
		{"找出pdf和docx檔案", Query{Extensions: []string{"pdf", "docx"}}},
		{"go 或 md 檔", Query{Extensions: []string{"go", "md"}}},
		{"找圖片", Query{Extensions: imageExts}},
		{"extension heic", Query{Extensions: []string{"heic"}}},
		{"副檔名 .tar.gz", Query{Extensions: []string{"tar.gz"}}},
		{"c files", Query{Extensions: []string{"c", "h"}}},

		// content
		{`containing "TODO"`, Query{Contents: []string{"TODO"}}},
		{"contains FIXME", Query{Contents: []string{"FIXME"}}},
		{"包含 TODO 的 go 檔", Query{Contents: []string{"TODO"}, Extensions: []string{"go"}}},
		{"包含TODO且大於1MB", Query{Contents: []string{"TODO"}, Sizes: []SizeCond{{">", 1 << 20}}}},
		{"內容有「錯誤訊息」", Query{Contents: []string{"錯誤訊息"}}},
		{"包含 \"hello world\" 不分大小寫", Query{Contents: []string{"hello world"}, IgnoreCase: true}},
		{`"TODO" in go files ignore case`, Query{Contents: []string{"TODO"}, Extensions: []string{"go"}, IgnoreCase: true}},
		{"containing 404error", Query{Contents: []string{"404error"}}},

		// size
		{"larger than 10MB", Query{Sizes: []SizeCond{{">", 10 << 20}}}},
		{"smaller than 1.5k", Query{Sizes: []SizeCond{{"<", 1536}}}},
		{"> 2gb", Query{Sizes: []SizeCond{{">", 2 << 30}}}},
		{"大於 100KB 小於 1MB", Query{Sizes: []SizeCond{{">", 100 << 10}, {"<", 1 << 20}}}},
		{"10MB以上", Query{Sizes: []SizeCond{{">=", 10 << 20}}}},
		{"at most 500 bytes", Query{Sizes: []SizeCond{{"<=", 500}}}},
		{"最大 10MB", Query{Sizes: []SizeCond{{"<=", 10 << 20}}}},

		// modification time
		{"modified in the last 7 days", Query{ModifiedAfter: ago(7 * day)}},
		{"最近7天修改", Query{ModifiedAfter: ago(7 * day)}},
		{"最近三天", Query{ModifiedAfter: ago(3 * day)}},
		{"過去 2 小時", Query{ModifiedAfter: ago(2 * time.Hour)}},
		{"24h 內", Query{ModifiedAfter: ago(24 * time.Hour)}},
		{"last week", Query{ModifiedAfter: ago(7 * day)}},
		{"過去一個月", Query{ModifiedAfter: ago(30 * day)}},
		{"older than 30 days", Query{ModifiedBefore: ago(30 * day)}},
		{"超過30天", Query{ModifiedBefore: ago(30 * day)}},
		{"30 days ago", Query{ModifiedBefore: ago(30 * day)}},
		{"3天前修改的", Query{ModifiedBefore: ago(3 * day)}},
		// today is a calendar day [00:00, next 00:00), like yesterday
		{"today", Query{ModifiedAfter: time.Date(2026, 9, 27, 0, 0, 0, 0, time.UTC),
			ModifiedBefore: time.Date(2026, 9, 28, 0, 0, 0, 0, time.UTC)}},
		{"昨天修改的", Query{ModifiedAfter: time.Date(2026, 9, 26, 0, 0, 0, 0, time.UTC),
			ModifiedBefore: time.Date(2026, 9, 27, 0, 0, 0, 0, time.UTC)}},

		// limit and ordering
		{"top 10", Query{Limit: 10}},
		{"最多 5 個", Query{Limit: 5}},
		{"前10個", Query{Limit: 10}},
		{"只要十個", Query{Limit: 10}},
		{"最大的 5 個檔案", Query{Limit: 5, Sort: SortLargest}},
		{"前3大", Query{Limit: 3, Sort: SortLargest}},
		{"10 largest files", Query{Limit: 10, Sort: SortLargest}},
		{"largest files", Query{Sort: SortLargest}},
		{"newest 3 go files", Query{Limit: 3, Sort: SortNewest, Extensions: []string{"go"}}},
		{"最新的 3 個", Query{Limit: 3, Sort: SortNewest}},

		// exclusions and root
		{"exclude node_modules", Query{ExcludeDirs: []string{"node_modules"}}},
		{"excluding vendor and .git", Query{ExcludeDirs: []string{"vendor", ".git"}}},
		{"排除 node_modules 跟 .git 目錄", Query{ExcludeDirs: []string{"node_modules", ".git"}}},
		{"除了 build 以外", Query{ExcludeDirs: []string{"build"}}},
		{"exclude vendor and larger than 1mb", Query{ExcludeDirs: []string{"vendor"}, Sizes: []SizeCond{{">", 1 << 20}}}},
		{"in ~/Documents", Query{Root: "~/Documents"}},
		{"在 ./src 裡找 go 檔", Query{Root: "./src", Extensions: []string{"go"}}},

		{"排除 node_modules dist", Query{ExcludeDirs: []string{"node_modules", "dist"}}},
		{"exclude vendor go files", Query{ExcludeDirs: []string{"vendor"}, Extensions: []string{"go"}}},
		{"list txt files under /tmp", Query{Root: "/tmp", Extensions: []string{"txt"}}},
		{"副檔名是 .log", Query{Extensions: []string{"log"}}},
		{"find swift files with TODO", Query{Extensions: []string{"swift"}, Contents: []string{"TODO"}}},
		{"有 TODO 的檔案", Query{Contents: []string{"TODO"}}},
		{"files with extension md", Query{Extensions: []string{"md"}}},
		{"最近修改的 5 個檔案", Query{Limit: 5, Sort: SortNewest}},
		{"last 5 files", Query{Limit: 5, Sort: SortNewest}},

		// arbitrary extension + file noun (v0.1.1)
		{"找 ovpn 檔", Query{Extensions: []string{"ovpn"}}},
		{"find ovpn files", Query{Extensions: []string{"ovpn"}}},
		{"find pem files", Query{Extensions: []string{"pem"}}},
		{"找 tfstate 檔", Query{Extensions: []string{"tfstate"}}},
		{"找 conf 檔案", Query{Extensions: []string{"conf"}}},
		{"ovpn 和 pem 檔", Query{Extensions: []string{"ovpn", "pem"}}},
		{"find banana files", Query{Extensions: []string{"banana"}}}, // any word + noun is an extension by design
		{".ovpn", Query{Extensions: []string{"ovpn"}}},
		{"副檔名是 ovpn", Query{Extensions: []string{"ovpn"}}},
		// aliases keep working
		{"find python files", Query{Extensions: []string{"py"}}},
		{"word 檔", Query{Extensions: []string{"docx", "doc"}}},
		{"images", Query{Extensions: imageExts}},
		{"go files larger than 1kb", Query{Extensions: []string{"go"}, Sizes: []SizeCond{{">", 1 << 10}}}},

		// exclude must not swallow a following extension
		{"exclude vendor and go files", Query{ExcludeDirs: []string{"vendor"}, Extensions: []string{"go"}}},
		{"排除 vendor 和 go 檔", Query{ExcludeDirs: []string{"vendor"}, Extensions: []string{"go"}}},
		{"exclude vendor and go and md files", Query{ExcludeDirs: []string{"vendor"}, Extensions: []string{"go", "md"}}},
		{"exclude vendor and ovpn files", Query{ExcludeDirs: []string{"vendor"}, Extensions: []string{"ovpn"}}},
		{"except vendor dir", Query{ExcludeDirs: []string{"vendor"}}},
		{"不要 tmp 目錄", Query{ExcludeDirs: []string{"tmp"}}},
		{"exclude logs dir", Query{ExcludeDirs: []string{"logs"}}},
		{`排除 ".go"`, Query{ExcludeDirs: []string{".go"}}},
		{"exclude ./src/gen", Query{ExcludeDirs: []string{"./src/gen"}}},

		// unquoted content is taken whole up to a space or punctuation
		{"包含 錯誤訊息", Query{Contents: []string{"錯誤訊息"}}},
		{"內容包含 系統錯誤", Query{Contents: []string{"系統錯誤"}}},
		{"找內容有 API連線失敗 的檔案", Query{Contents: []string{"API連線失敗"}}},
		{"包含 錯誤訊息，大於 1KB", Query{Contents: []string{"錯誤訊息"}, Sizes: []SizeCond{{">", 1 << 10}}}},

		// unquoted paths keep non-ASCII characters
		{"在 ~/文件 找 go 檔", Query{Root: "~/文件", Extensions: []string{"go"}}},
		{"in ~/Documents/報告 pdf files", Query{Root: "~/Documents/報告", Extensions: []string{"pdf"}}},
		{"在 ./中文路徑 找 txt 檔", Query{Root: "./中文路徑", Extensions: []string{"txt"}}},
		{`在 "/Users/foo/中文 目錄" 找 md 檔`, Query{Root: "/Users/foo/中文 目錄", Extensions: []string{"md"}}},
		{"在 ~/圖片", Query{Root: "~/圖片"}},

		// numeric boundaries
		{"top 2147483647", Query{Limit: 2147483647}},
		{"last 292 years", Query{ModifiedAfter: ago(292 * 365 * day)}},

		// size OR (was rejected before OR groups existed)
		{"larger than 1mb or smaller than 10k", Query{AnyOf: []OrGroup{{Kind: KindSize, Sizes: []SizeCond{{">", 1 << 20}, {"<", 10 << 10}}}}}},

		// combined
		{"找出最近 7 天修改、大於 1MB 的 go 檔，排除 vendor，最多 10 個", Query{
			ModifiedAfter: ago(7 * day), Sizes: []SizeCond{{">", 1 << 20}}, Extensions: []string{"go"},
			ExcludeDirs: []string{"vendor"}, Limit: 10}},
		{`find python files containing "import os" modified within 3 days larger than 1kb excluding .venv, top 5`, Query{
			Extensions: []string{"py"}, Contents: []string{"import os"}, ModifiedAfter: ago(3 * day),
			Sizes: []SizeCond{{">", 1 << 10}}, ExcludeDirs: []string{".venv"}, Limit: 5}},
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

func TestParseErrors(t *testing.T) {
	tests := []struct {
		in, wantMsg string
	}{
		{"", "empty query"},
		{"go files banana", `cannot understand: "banana"`},
		{"larger than 1mb or modified today", "OR currently requires conditions of the same type"},
		{"不包含 TODO", "negative conditions"},
		{`containing "TODO`, "unterminated quote"},
		{"larger than", "must be followed by a number"},
		{"10 go files", "number needs a unit"},
		{"10個 go 檔", ""}, // a count unit makes a bare number a limit
		{"bigger 10", "cannot understand"},
		{"10MB", "file size needs a comparison"},
		{"7 天", "time needs a direction"},
		{"exclude", `"exclude" must be followed`},
		{"containing", `"containing" must be followed`},
		{"最近 7 天 超過 30 天", "contradict"},
		{"top 1.5", "positive whole number"},
		{"more than 10", "number needs a unit"},

		// negative wording is rejected, never turned into an exclusion
		{"files without TODO", "negative conditions are not supported"},
		{"不包括 TODO 的檔案", "negative conditions are not supported"},
		{"不含 TODO", "negative conditions are not supported"},
		{"except vendor", "unclear whether this excludes a directory"},
		{"不要 tmp", "unclear whether this excludes a directory"},
		{"除了 TODO", "unclear whether this excludes a directory"},

		// file types cannot be excluded in v0.1
		{"exclude go files", "can only exclude directories"},
		{"exclude test files", "can only exclude directories"},
		{"除了 go 檔以外", "can only exclude directories"},
		{"排除 .go", "looks like a file type"},
		{"exclude *.log", "looks like a file type"},
		{"exclude images", "looks like a file type"},

		// empty content
		{`""`, "search text must not be empty"},
		{`包含 ""`, "search text must not be empty"},
		{`containing ""`, "search text must not be empty"},
		{`go files ""`, "search text must not be empty"},

		// unquoted content glued to a keyword is ambiguous
		{"包含 設定檔", "ambiguous"},
		{"包含 10個", "ambiguous"},
		{"包含 系統有問題", "ambiguous"},
		{"包含 系統 錯誤", `cannot understand: "錯誤"`}, // not merged into "系統錯誤"

		// overflow
		{"top 99999999999999999999", "count is too large"},
		{"top 2147483648", "count is too large"},
		{"last 1000 years", "time span is too large"},
		{"last 99999999999999999999 days", "time span is too large"},
		{"larger than 8388608 TB", "file size is too large"},
		{"larger than 99999999999999999999 GB", "file size is too large"},

		// ordinary words are not extensions
		{"large files", `cannot understand: "large"`},
		{"delete files", `cannot understand: "delete"`},
		{"find ovpn", `cannot understand: "ovpn"`}, // no noun, no trigger
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			_, err := Parse(tt.in, now)
			if tt.wantMsg == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				return
			}
			var pe *Error
			if !errors.As(err, &pe) {
				t.Fatalf("Parse(%q) error = %v, want *Error", tt.in, err)
			}
			if !strings.Contains(pe.Message(English), tt.wantMsg) {
				t.Errorf("Parse(%q) error = %q, want it to contain %q", tt.in, pe.Message(English), tt.wantMsg)
			}
		})
	}
}

func TestErrorMessageIsChineseForChineseInput(t *testing.T) {
	_, err := Parse("找 香蕉", now)
	var pe *Error
	if !errors.As(err, &pe) {
		t.Fatalf("want *Error, got %v", err)
	}
	if got := pe.Message(DetectLang("找 香蕉")); !strings.Contains(got, "看不懂這個詞：「香蕉」") {
		t.Errorf("message = %q", got)
	}
}

func TestFindCommand(t *testing.T) {
	tests := []struct {
		name string
		p    Platform
		q    Query
		want string
	}{
		{"symlink root gets a trailing slash", MacOS, Query{Root: "/tmp", RootSymlink: true},
			"find /tmp/ -type f -print"},
		{"root starting with a dash", MacOS, Query{Root: "-dir"},
			"find ./-dir -type f -print"},
		{"only directories are pruned", MacOS, Query{ExcludeDirs: []string{"vendor", ".git"}},
			`find . -type d \( -iname vendor -o -iname .git \) -prune -o -type f -print`},
		{"relative exclude path", MacOS, Query{ExcludeDirs: []string{"./src/gen"}},
			`find . -type d \( -path ./src/gen \) -prune -o -type f -print`},
		{"unnormalized exclude path", MacOS, Query{ExcludeDirs: []string{"./src//gen/"}},
			`find . -type d \( -path ./src/gen \) -prune -o -type f -print`},
		{"exclude path under a symlink root", MacOS, Query{Root: "/tmp", RootSymlink: true, ExcludeDirs: []string{"./a b"}},
			`find /tmp/ -type d \( -path '/tmp/a b' \) -prune -o -type f -print`},
		{"quoting", MacOS, Query{Root: "/x/it's", Contents: []string{"-e"}},
			`find '/x/it'\''s' -type f -exec grep -qIF -- -e {} \; -print`},

		// BSD stat on macOS, GNU stat on Linux; everything else is shared.
		{"largest on macOS", MacOS, Query{Sort: SortLargest, Limit: 5},
			`find . -type f -exec stat -f '%z %N' {} + | sort -rn | cut -d' ' -f2- | head -n 5`},
		{"newest on macOS", MacOS, Query{Sort: SortNewest},
			`find . -type f -exec stat -f '%m %N' {} + | sort -rn | cut -d' ' -f2-`},
		{"largest on Linux", Linux, Query{Sort: SortLargest, Limit: 5},
			`find . -type f -exec stat -c '%s %n' {} + | sort -rn | cut -d' ' -f2- | head -n 5`},
		{"newest on Linux", Linux, Query{Sort: SortNewest},
			`find . -type f -exec stat -c '%Y %n' {} + | sort -rn | cut -d' ' -f2-`},
		{"unsorted is identical on Linux", Linux, Query{Root: "/var/log", Extensions: []string{"log"}, Contents: []string{"x"},
			ExcludeDirs: []string{"old"}, Sizes: []SizeCond{{">", 1024}}, Limit: 3},
			`find /var/log -type d \( -iname old \) -prune -o -type f -iname '*.log' -size +1024c -exec grep -qIF -- x {} \; -print | head -n 3`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := FindCommand(&tt.q, tt.p); got != tt.want {
				t.Errorf("FindCommand()\n got  %s\n want %s", got, tt.want)
			}
		})
	}
}

func TestPlatformFor(t *testing.T) {
	for goos, want := range map[string]Platform{"darwin": MacOS, "linux": Linux, "freebsd": MacOS, "": MacOS} {
		if got := PlatformFor(goos); got != want {
			t.Errorf("PlatformFor(%q) = %v, want %v", goos, got, want)
		}
	}
	if MacOS.Name() != "macOS" || Linux.Name() != "GNU/Linux" {
		t.Errorf("names: %q %q", MacOS.Name(), Linux.Name())
	}
}

func TestExplainSaysApproximate(t *testing.T) {
	for _, p := range []Platform{MacOS, Linux} {
		for _, in := range []string{"largest go files", "最大的 go 檔"} {
			q, err := Parse(in, now)
			if err != nil {
				t.Fatal(err)
			}
			out := Explain(q, p)
			if strings.Contains(out, "等效") || strings.Contains(out, "Equivalent") {
				t.Errorf("Explain(%q) claims equivalence:\n%s", in, out)
			}
			if !strings.Contains(out, "近似的 "+p.Name()+" 指令") && !strings.Contains(out, "Approximate "+p.Name()+" command") {
				t.Errorf("Explain(%q, %s) should call the command approximate for %s:\n%s", in, p.Name(), p.Name(), out)
			}
			if !strings.Contains(out, FindCommand(q, p)) {
				t.Errorf("Explain(%q, %s) does not show the %s command:\n%s", in, p.Name(), p.Name(), out)
			}
			other := map[Platform]string{MacOS: "stat -c", Linux: "stat -f"}[p]
			if strings.Contains(out, other) {
				t.Errorf("Explain(%q, %s) uses the other platform's %q:\n%s", in, p.Name(), other, out)
			}
		}
	}
}
