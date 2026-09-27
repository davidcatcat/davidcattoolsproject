package query

import (
	"errors"
	"strings"
	"testing"
)

func nameIs(p string) NameCond       { return NameCond{NameExact, p} }
func nameContains(p string) NameCond { return NameCond{NameContains, p} }
func namePrefix(p string) NameCond   { return NameCond{NamePrefix, p} }
func nameSuffix(p string) NameCond   { return NameCond{NameSuffix, p} }
func nameGlob(p string) NameCond     { return NameCond{NameGlob, p} }
func nameGroup(cs ...NameCond) OrGroup {
	return OrGroup{Kind: KindName, Names: cs}
}

func TestParseFilename(t *testing.T) {
	tests := []struct {
		in   string
		want Query
	}{
		// exact
		{"找檔名是 application.yml 的檔案", Query{Names: []NameCond{nameIs("application.yml")}}},
		{"找檔名叫 kubeconfig", Query{Names: []NameCond{nameIs("kubeconfig")}}},
		{"找名稱是 nginx.conf", Query{Names: []NameCond{nameIs("nginx.conf")}}},
		{"找名字叫 kubeconfig", Query{Names: []NameCond{nameIs("kubeconfig")}}},
		{"找檔名等於 .env", Query{Names: []NameCond{nameIs(".env")}}},
		{"找名為 docker-compose.yml 的檔案", Query{Names: []NameCond{nameIs("docker-compose.yml")}}},
		{"filename is application.yml", Query{Names: []NameCond{nameIs("application.yml")}}},
		{"filename is nginx.conf", Query{Names: []NameCond{nameIs("nginx.conf")}}},
		{"file named nginx.conf", Query{Names: []NameCond{nameIs("nginx.conf")}}},
		{"file named .env", Query{Names: []NameCond{nameIs(".env")}}},
		{"files named kubeconfig", Query{Names: []NameCond{nameIs("kubeconfig")}}},
		{"find files named kubeconfig", Query{Names: []NameCond{nameIs("kubeconfig")}}},
		{"name equals .env", Query{Names: []NameCond{nameIs(".env")}}},
		{"find a file named docker-compose.yml", Query{Names: []NameCond{nameIs("docker-compose.yml")}}},
		{"檔名是 .gitignore", Query{Names: []NameCond{nameIs(".gitignore")}}},

		// contains
		{"找檔名包含 config 的檔案", Query{Names: []NameCond{nameContains("config")}}},
		{"找名稱有 backup 的 sql", Query{Extensions: []string{"sql"}, Names: []NameCond{nameContains("backup")}}},
		{"找名字包含 nginx 的檔案", Query{Names: []NameCond{nameContains("nginx")}}},
		{"filename contains config", Query{Names: []NameCond{nameContains("config")}}},
		{"files whose name contains backup", Query{Names: []NameCond{nameContains("backup")}}},
		{"name contains nginx", Query{Names: []NameCond{nameContains("nginx")}}},
		{"name contains backup", Query{Names: []NameCond{nameContains("backup")}}},
		{"filename contains today", Query{Names: []NameCond{nameContains("today")}}}, // explicit context: a word, not a date
		{"檔名包含 2026", Query{Names: []NameCond{nameContains("2026")}}},

		// prefix
		{"找檔名以 app- 開頭的檔案", Query{Names: []NameCond{namePrefix("app-")}}},
		{"檔名以app-開頭", Query{Names: []NameCond{namePrefix("app-")}}},
		{"找名稱開頭是 backup-", Query{Names: []NameCond{namePrefix("backup-")}}},
		{"找名字從 nginx 開始", Query{Names: []NameCond{namePrefix("nginx")}}},
		{"filename starts with app-", Query{Names: []NameCond{namePrefix("app-")}}},
		{"files whose name starts with backup-", Query{Names: []NameCond{namePrefix("backup-")}}},
		{"name starts with api-", Query{Names: []NameCond{namePrefix("api-")}}},
		{"name begins with nginx", Query{Names: []NameCond{namePrefix("nginx")}}},

		// suffix (not an extension: ".tar.gz" is plain text)
		{"找檔名以 .bak 結尾", Query{Names: []NameCond{nameSuffix(".bak")}}},
		{"找名稱結尾是 .old", Query{Names: []NameCond{nameSuffix(".old")}}},
		{"找名字 ending with .disabled", Query{Names: []NameCond{nameSuffix(".disabled")}}},
		{"filename ends with .bak", Query{Names: []NameCond{nameSuffix(".bak")}}},
		{"name ending in .old", Query{Names: []NameCond{nameSuffix(".old")}}},
		{"name ends with .old", Query{Names: []NameCond{nameSuffix(".old")}}},
		{"files whose name ends with .disabled", Query{Names: []NameCond{nameSuffix(".disabled")}}},
		{`filename ends with ".tar.gz"`, Query{Names: []NameCond{nameSuffix(".tar.gz")}}},

		// glob
		{`找檔名符合 "*.conf"`, Query{Names: []NameCond{nameGlob("*.conf")}}},
		{`找名稱符合 "app-*.yml"`, Query{Names: []NameCond{nameGlob("app-*.yml")}}},
		{`找檔名匹配 "core.*"`, Query{Names: []NameCond{nameGlob("core.*")}}},
		{`filename matches "*.conf"`, Query{Names: []NameCond{nameGlob("*.conf")}}},
		{`filename matches "app-*.yml"`, Query{Names: []NameCond{nameGlob("app-*.yml")}}},
		{`name matches "app-*.yml"`, Query{Names: []NameCond{nameGlob("app-*.yml")}}},
		{`filename glob "core.*"`, Query{Names: []NameCond{nameGlob("core.*")}}},
		{"找檔名符合 core.*", Query{Names: []NameCond{nameGlob("core.*")}}}, // unquoted works too
		{`檔名符合 ".*"`, Query{Names: []NameCond{nameGlob(".*")}}},
		{`檔名符合 "*.env"`, Query{Names: []NameCond{nameGlob("*.env")}}},
		{`檔名符合 "[ab]*.conf"`, Query{Names: []NameCond{nameGlob("[ab]*.conf")}}},

		// quoted and special names keep their text and case
		{`檔名是 "foo bar.conf"`, Query{Names: []NameCond{nameIs("foo bar.conf")}}},
		{`檔名是 "中文設定檔.yml"`, Query{Names: []NameCond{nameIs("中文設定檔.yml")}}},
		{`檔名是 "Config.yml"`, Query{Names: []NameCond{nameIs("Config.yml")}}},
		{"檔名是 Config.yml", Query{Names: []NameCond{nameIs("Config.yml")}}},
		{"檔名是 --weird-name", Query{Names: []NameCond{nameIs("--weird-name")}}},
		{"檔名是 name[1].txt", Query{Names: []NameCond{nameIs("name[1].txt")}}},
		{"檔名是 application-prod.yml", Query{Names: []NameCond{nameIs("application-prod.yml")}}},

		// combined with other conditions (AND)
		{"找檔名包含 config 的 yml", Query{Extensions: []string{"yaml", "yml"}, Names: []NameCond{nameContains("config")}}},
		{"find yaml files whose name contains config", Query{Extensions: []string{"yaml", "yml"}, Names: []NameCond{nameContains("config")}}},
		{"找檔名是 application.yml 的 yml", Query{Extensions: []string{"yaml", "yml"}, Names: []NameCond{nameIs("application.yml")}}},
		{"找檔名以 app- 開頭的 log", Query{Extensions: []string{"log"}, Names: []NameCond{namePrefix("app-")}}},
		{"找今天改過且檔名包含 config 的檔案", Query{Names: []NameCond{nameContains("config")},
			ModifiedAfter: ymd(2026, 9, 28), ModifiedBefore: ymd(2026, 9, 29)}},
		{"找 9 月修改、檔名包含 backup 的 sql", Query{Extensions: []string{"sql"}, Names: []NameCond{nameContains("backup")},
			ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 10, 1)}},
		{"找檔名包含 nginx 且大於 1MB 的 log", Query{Extensions: []string{"log"}, Names: []NameCond{nameContains("nginx")},
			Sizes: []SizeCond{{">", 1 << 20}}}},
		{`找檔名符合 "*.conf"，排除 vendor`, Query{Names: []NameCond{nameGlob("*.conf")}, ExcludeDirs: []string{"vendor"}}},
		{`找檔名是 nginx.conf，而且內容包含 "listen 443"`, Query{Names: []NameCond{nameIs("nginx.conf")}, Contents: []string{"listen 443"}}},
		{"檔名包含 config 且檔名以 app- 開頭", Query{Names: []NameCond{nameContains("config"), namePrefix("app-")}}},
		{"在 /etc/nginx 找檔名是 nginx.conf", Query{Root: "/etc/nginx", Names: []NameCond{nameIs("nginx.conf")}}},
		{`檔名包含 config，包含 "x" 不分大小寫`, Query{Names: []NameCond{nameContains("config")}, Contents: []string{"x"}, IgnoreCase: true}},

		// filename OR, with operator inheritance
		{"檔名是 nginx.conf 或 httpd.conf", Query{AnyOf: []OrGroup{nameGroup(nameIs("nginx.conf"), nameIs("httpd.conf"))}}},
		{"檔名是 nginx.conf 或檔名是 httpd.conf", Query{AnyOf: []OrGroup{nameGroup(nameIs("nginx.conf"), nameIs("httpd.conf"))}}},
		{"filename is nginx.conf or httpd.conf", Query{AnyOf: []OrGroup{nameGroup(nameIs("nginx.conf"), nameIs("httpd.conf"))}}},
		{"filename contains prod or staging", Query{AnyOf: []OrGroup{nameGroup(nameContains("prod"), nameContains("staging"))}}},
		{"name contains prod or staging", Query{AnyOf: []OrGroup{nameGroup(nameContains("prod"), nameContains("staging"))}}},
		{"找檔名包含 prod 或 staging 的 yml", Query{Extensions: []string{"yaml", "yml"},
			AnyOf: []OrGroup{nameGroup(nameContains("prod"), nameContains("staging"))}}},
		{`filename matches "*.conf" or "*.cfg"`, Query{AnyOf: []OrGroup{nameGroup(nameGlob("*.conf"), nameGlob("*.cfg"))}}},
		{`檔名符合 "*.conf" 或 "*.cfg"`, Query{AnyOf: []OrGroup{nameGroup(nameGlob("*.conf"), nameGlob("*.cfg"))}}},
		{"檔名以 app- 開頭或 api-", Query{AnyOf: []OrGroup{nameGroup(namePrefix("app-"), namePrefix("api-"))}}},
		{"檔名以 app- 開頭或以 api- 開頭", Query{AnyOf: []OrGroup{nameGroup(namePrefix("app-"), namePrefix("api-"))}}},
		{"檔名以 .bak 結尾或 .old", Query{AnyOf: []OrGroup{nameGroup(nameSuffix(".bak"), nameSuffix(".old"))}}},
		{"檔名包含 prod 或檔名是 staging.yml", Query{AnyOf: []OrGroup{nameGroup(nameContains("prod"), nameIs("staging.yml"))}}},
		{"檔名是 nginx.conf 或檔名包含 apache", Query{AnyOf: []OrGroup{nameGroup(nameIs("nginx.conf"), nameContains("apache"))}}},
		{"檔名是 nginx.conf 或 httpd.conf，今天修改", Query{ModifiedAfter: ymd(2026, 9, 28), ModifiedBefore: ymd(2026, 9, 29),
			AnyOf: []OrGroup{nameGroup(nameIs("nginx.conf"), nameIs("httpd.conf"))}}},

		// unchanged: content, extensions and paths
		{"包含 nginx", Query{Contents: []string{"nginx"}}},
		{"找包含 ERROR 的 log", Query{Extensions: []string{"log"}, Contents: []string{"ERROR"}}},
		{"找檔名包含 ERROR 的 log", Query{Extensions: []string{"log"}, Names: []NameCond{nameContains("ERROR")}}},
		{"找內容包含 ERROR 的 log", Query{Extensions: []string{"log"}, Contents: []string{"ERROR"}}},
		{"go 或 md 檔", Query{Extensions: []string{"go", "md"}}},
		{"find log files", Query{Extensions: []string{"log"}}},
		{"在 /etc/nginx 找 conf", Query{Root: "/etc/nginx", Extensions: []string{"conf"}}},
		{"containing name", Query{Contents: []string{"name"}}},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			got, err := Parse(tt.in, dtNow)
			if err != nil {
				t.Fatalf("Parse(%q): %v", tt.in, err)
			}
			tt.want.Lang, tt.want.Now = DetectLang(tt.in), dtNow
			if !sameQuery(got, &tt.want) || !sameNames(got, &tt.want) {
				t.Errorf("Parse(%q)\n got  %+v\n want %+v", tt.in, *got, tt.want)
			}
		})
	}
}

// sameNames compares filename conditions in Names and in KindName groups.
func sameNames(a, b *Query) bool {
	eq := func(x, y []NameCond) bool {
		if len(x) != len(y) {
			return false
		}
		for i := range x {
			if x[i] != y[i] {
				return false
			}
		}
		return true
	}
	if !eq(a.Names, b.Names) || len(a.AnyOf) != len(b.AnyOf) {
		return false
	}
	for i := range a.AnyOf {
		if !eq(a.AnyOf[i].Names, b.AnyOf[i].Names) {
			return false
		}
	}
	return true
}

func TestParseFilenameErrors(t *testing.T) {
	tests := []struct{ in, en, zh string }{
		{"檔名是", "", "檔名條件需要名稱"},
		{"filename is", "a filename condition needs a name", ""},
		{"檔名包含", "", "檔名條件需要名稱"},
		{"filename contains", "a filename condition needs a name", ""},
		{"檔名符合", "", "檔名條件需要名稱"},
		{"filename matches", "a filename condition needs a name", ""},
		{"files named", "a filename condition needs a name", ""},
		{`檔名符合 "[abc"`, "", "檔名 glob pattern 不合法"},
		{`filename matches "[abc"`, "invalid filename glob pattern", ""},
		{`filename matches "a\"`, "invalid filename glob pattern", ""},
		{`檔名包含 ""`, "", "檔名不能是空字串"},
		{"檔名 config", "", "檔名後面需要比對方式"},
		{"name config", "a filename condition needs an operator", ""},
		{"檔名以 app-", "", "後面需要「開頭」或「結尾」"},
		{"檔名是 中文設定檔.yml", "", "請加引號"}, // unquoted non-ASCII names must be quoted
		{"檔名包含 prod 或", "", "「或」後面需要條件"},
		{"檔名包含 prod 或今天", "", "目前 OR 只能連接同類條件"},
		{"name contains prod or today", "OR currently requires conditions of the same type", ""},
		{"檔名包含 prod 或 大於 1MB", "", "目前 OR 只能連接同類條件"},
		{"檔名以 app- 開頭或 api- 結尾", "", "比對方式不同"},
		{"檔名包含 prod 且 或 staging", "", "OR 和 AND 不能連續使用"},
		// ignore case is content-only: never silently applied to filenames
		{"檔名包含 config，不分大小寫", "", "「不分大小寫」只適用於內容搜尋"},
		{"filename contains config ignore case", "ignore case applies to content only", ""},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			q, err := Parse(tt.in, dtNow)
			var pe *Error
			if !errors.As(err, &pe) {
				t.Fatalf("Parse(%q) = %+v, %v; want an error", tt.in, q, err)
			}
			if tt.en != "" && !strings.Contains(pe.Message(English), tt.en) {
				t.Errorf("error %q, want %q", pe.Message(English), tt.en)
			}
			if tt.zh != "" && !strings.Contains(pe.Message(Chinese), tt.zh) {
				t.Errorf("error %q, want %q", pe.Message(Chinese), tt.zh)
			}
		})
	}
}

func TestNameCondMatch(t *testing.T) {
	for _, c := range []struct {
		cond NameCond
		name string
		want bool
	}{
		{nameIs("application.yml"), "application.yml", true},
		{nameIs("application.yml"), "Application.yml", false}, // case-sensitive
		{nameIs(".env"), ".env", true},
		{nameIs("name[1].txt"), "name[1].txt", true}, // exact is literal, not a glob
		{nameContains("config"), "app-config.yml", true},
		{nameContains("config"), "中文設定檔.yml", false},
		{nameContains("Config"), "config.yml", false},
		{namePrefix("app-"), "app-api.log", true},
		{namePrefix("app-"), "api-app.log", false},
		{nameSuffix(".bak"), "nginx.conf.bak", true},
		{nameSuffix(".tar.gz"), "logs.tar.gz", true},
		{nameGlob("core.*"), "core.1234", true},
		{nameGlob("core.*"), "core", false},
		{nameGlob("*.conf"), "foo bar.conf", true},
		{nameGlob(".*"), ".gitignore", true},
		{nameGlob("*"), ".env", true}, // Go glob: * also matches a leading dot
		{nameGlob("app-?.log"), "app-1.log", true},
		{nameGlob("app-?.log"), "app-12.log", false},
		{nameGlob("[ab]*.conf"), "b.conf", true},
		{nameGlob("name[1].txt"), "name[1].txt", false}, // [1] is a class in a glob
		{nameGlob("[abc"), "a", false},                  // invalid patterns never match (and are rejected when parsed)
	} {
		if got := c.cond.Match(c.name); got != c.want {
			t.Errorf("%+v.Match(%q) = %v, want %v", c.cond, c.name, got, c.want)
		}
	}
}

func TestExplainShowsFilename(t *testing.T) {
	q, err := Parse("找今天修改，而且檔名包含 config 的 yml", dtNow)
	if err != nil {
		t.Fatal(err)
	}
	out := Explain(q, MacOS)
	for _, want := range []string{"副檔名         .yaml 或 .yml", `檔名           包含 "config" (區分大小寫)`,
		`\( -iname '*.yaml' -o -iname '*.yml' \) -name '*config*'`} {
		if !strings.Contains(out, want) {
			t.Errorf("Explain missing %q:\n%s", want, out)
		}
	}
	q, _ = Parse("filename is nginx.conf or httpd.conf", dtNow)
	out = Explain(q, Linux)
	for _, want := range []string{`filename:      is "nginx.conf" OR is "httpd.conf" (any one matches, case-sensitive)`,
		`\( -name nginx.conf -o -name httpd.conf \)`} {
		if !strings.Contains(out, want) {
			t.Errorf("Explain missing %q:\n%s", want, out)
		}
	}
	if strings.Count(out, "filename:") != 1 || strings.Contains(out, "-iname nginx") {
		t.Errorf("filename OR shown wrong:\n%s", out)
	}
}

func TestFilenameFindPattern(t *testing.T) {
	for _, c := range []struct {
		cond NameCond
		want string
	}{
		{nameIs("nginx.conf"), "-name nginx.conf"},
		{nameContains("config"), "-name '*config*'"},
		{namePrefix("app-"), "-name 'app-*'"},
		{nameSuffix(".bak"), "-name '*.bak'"},
		{nameGlob("app-*.yml"), "-name 'app-*.yml'"},
		{nameIs("name[1].txt"), `-name 'name\[1].txt'`}, // literal text is escaped for find
		{nameContains("a*b"), `-name '*a\*b*'`},
		{nameIs("it's.conf"), `-name 'it'\''s.conf'`},
		{nameIs("foo bar.conf"), "-name 'foo bar.conf'"},
	} {
		q := &Query{Names: []NameCond{c.cond}}
		if got := FindCommand(q, MacOS); !strings.Contains(got, " "+c.want+" ") {
			t.Errorf("%+v: %s, want %s", c.cond, got, c.want)
		}
	}
}
