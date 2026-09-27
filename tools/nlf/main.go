// Command nlf (Natural Language Find) finds files from a plain Chinese or
// English description, without calling any language model.
package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/search"
)

const version = "0.4.0"

const usage = `nlf — Natural Language Find (v` + version + `)
Find files by describing them in Chinese or English. Deterministic and read-only; no AI calls.

Usage:
  nlf [flags] "<query>"

Examples (中文):
  nlf "找 ovpn 檔"
  nlf --dir ~/Downloads "找 ovpn 檔"
  nlf '找內容包含 "20.2.250.249" 的檔案'
  nlf "找最近 7 天修改的 go 檔"
  nlf "找最大的 5 個檔案"
  nlf "排除 vendor 和 node_modules，找 go 檔"

Examples (English):
  nlf "find pem files under /etc"
  nlf "go files containing TODO modified in the last 7 days"
  nlf "10 largest files excluding node_modules and .git"

Date & time (local time; a range is start <= mtime < end, and an end date is included):
  nlf '找 2026 年 9 月的 pdf 檔'      nlf '找 9 月修改的 yml 檔'   (no year = this year)
  nlf '查 2026 年 9 月 28 日改了什麼檔案'   nlf '找 9 月 1 日到 9 月 15 日修改的 log'
  nlf '找昨天 23:00 到今天 02:00 修改的 log'   nlf '找最近 6 小時修改的 conf'
  nlf 'files modified on 2026-09-28'   nlf 'log files modified from Sep 1 to Sep 15'
  nlf '找本月修改的 yaml'   nlf 'conf files modified in the last 6 hours'
  nlf '找 2026-09-01 以前修改的檔案'   nlf 'files modified on or after Sep 1'

Flags:
  --explain        show how the query is understood; never searches
  --confirm        show the execution plan first; search only if you answer y or Y
  -l, --long       print size and modification time before each path
  -d, --dir PATH   directory to search (default: current directory)
  -h, --help       show this help
  --version        print version

--explain   nlf --explain "找 ovpn 檔"
  Prints the parsed conditions and an approximate find command for this OS (macOS
  or GNU/Linux syntax; reference only, nlf never runs it). Nothing is searched.

--confirm   nlf --confirm "找 ovpn 檔"
  Prints the execution plan and a safety summary, then asks "Execute this search? [y/N]".
  Only y or Y searches; any other answer (n, Enter, yes, EOF, ...) prints "Cancelled.".

Safety:
  Read-only. nlf does not modify, create or delete files, does not run a shell
  or any other program, and never uses sudo. Unreadable paths are skipped.

Supported conditions (combined with AND):
  extension  .go  *.md  go files  找 ovpn 檔  go 或 md 檔  副檔名 heic  圖片  images
  filename   檔名是 application.yml  檔名包含 config  filename matches "app-*.yml"  (case-sensitive)
  content    containing "text"  包含 "文字"  內容有「錯誤」  ignore case / 不分大小寫
  modified   today  昨天  本月  2026-09-28  9 月  9/1 到 9/15  最近 6 小時  last 7 days  3 天前
  size       larger than 10MB  at most 1KB  大於 10MB  10MB 以上   (1 KB = 1024 bytes)
  count      top 10  最多 10 個  10 largest  最大的 5 個  newest 3  最新的 3 個
  exclude    exclude node_modules and .git  排除 vendor、build 目錄  排除 src/gen
  or         今天或昨天  包含 "ERROR" 或 "FATAL"  大於 1GB 或小於 1KB   (same kind; rest is AND)
  Not supported: OR across kinds (大於 1GB 或今天), NOT (不包含 / without), regex.

Search root (default: current directory; use --dir or the query, not both):
  --dir ~/Downloads   in ~/Documents   在 ./src   under /tmp   在 "~/My Docs"

Exit status:
  0  files found, or --confirm was cancelled
  1  valid query, but no files found
  2  error: query not understood, bad flag, missing or unreadable location

Tips:
  - Quote exact content: 包含 "20.2.250.249"    Quote paths with spaces: 在 "~/My Docs"
  - If nlf does not understand a word it stops with an error; it never
    silently widens the search. Check a query with --explain.
`

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr, time.Now()))
}

type options struct {
	help, version bool
	explain, long bool
	confirm       bool
	dir           string
	words         []string
}

func run(args []string, stdin io.Reader, stdout, stderr io.Writer, now time.Time) int {
	opts, err := parseArgs(args)
	if err == nil && opts.explain && opts.confirm {
		err = errors.New("--explain only shows the plan and --confirm asks before searching; use one of them")
	}
	if err != nil {
		fmt.Fprintf(stderr, "nlf: %v\nRun 'nlf --help' for usage.\n", err)
		return 2
	}
	if opts.help {
		fmt.Fprint(stdout, usage)
		return 0
	}
	if opts.version {
		fmt.Fprintln(stdout, "nlf "+version)
		return 0
	}
	input := strings.Join(opts.words, " ")
	lang := query.DetectLang(input)
	msg := func(zh, en string) string {
		if lang == query.Chinese {
			return zh
		}
		return en
	}
	if strings.TrimSpace(input) == "" {
		fmt.Fprint(stderr, msg("nlf：請描述要找的檔案，例如 nlf \"最近 7 天修改的 go 檔\"\n",
			"nlf: describe the files to find, e.g. nlf \"go files modified in the last 7 days\"\n"))
		return 2
	}

	q, err := query.Parse(input, now)
	if err != nil {
		var pe *query.Error
		if errors.As(err, &pe) {
			fmt.Fprintf(stderr, "nlf: %s\n", pe.Message(lang))
		} else {
			fmt.Fprintf(stderr, "nlf: %v\n", err)
		}
		return 2
	}
	if opts.dir != "" {
		if q.Root != "" {
			fmt.Fprint(stderr, msg("nlf：查詢中已指定搜尋位置，不能同時使用 --dir\n",
				"nlf: the query already names a location; do not combine it with --dir\n"))
			return 2
		}
		q.Root = opts.dir
	}
	q.Root = expandHome(q.Root)
	for i, d := range q.ExcludeDirs {
		q.ExcludeDirs[i] = expandHome(d)
	}
	if q.ExcludeDirs, err = search.NormalizeExcludes(q.Root, q.ExcludeDirs); err != nil {
		var ee *search.ExcludeError
		if errors.As(err, &ee) && ee.Self {
			fmt.Fprintf(stderr, msg("nlf：不能排除搜尋位置本身：「%s」\n", "nlf: cannot exclude the search location itself: %q\n"), ee.Path)
		} else if errors.As(err, &ee) {
			fmt.Fprintf(stderr, msg("nlf：排除路徑不在搜尋位置之內：「%s」\n提示：排除路徑請寫成相對於搜尋位置的路徑，例如 排除 src/generated\n",
				"nlf: exclude path is outside the search location: %q\nhint: write exclude paths relative to the search location, e.g. exclude src/generated\n"), ee.Path)
		} else {
			fmt.Fprintf(stderr, "nlf: %v\n", err)
		}
		return 2
	}

	if opts.explain || opts.confirm {
		// Let the displayed find command follow a symlinked root (e.g. /tmp)
		// the same way the search does.
		if li, err := os.Lstat(rootOrDot(q.Root)); err == nil && li.Mode()&fs.ModeSymlink != 0 {
			if fi, err := os.Stat(rootOrDot(q.Root)); err == nil && fi.IsDir() {
				q.RootSymlink = true
			}
		}
	}
	if opts.explain {
		fmt.Fprint(stdout, query.Explain(q, query.PlatformFor(runtime.GOOS)))
		return 0
	}
	if opts.confirm {
		// The plan and prompt go to stderr so stdout stays a clean list of paths.
		fmt.Fprint(stderr, executionPlan(q, search.RunEffects(), query.PlatformFor(runtime.GOOS)))
		fmt.Fprint(stderr, "Execute this search? [y/N]: ")
		if !approved(stdin) {
			fmt.Fprint(stderr, "\nCancelled.\n")
			return 0
		}
	}

	found := 0
	rep, err := search.Run(q, func(r search.Result) {
		found++
		if opts.long {
			fmt.Fprintf(stdout, "%10s  %s  %s\n", query.HumanBytes(r.Size), query.FormatTime(r.ModTime), r.Path)
		} else {
			fmt.Fprintln(stdout, r.Path)
		}
	})
	root := rootOrDot(q.Root)
	if err != nil {
		switch {
		case errors.Is(err, fs.ErrPermission):
			fmt.Fprintf(stderr, msg("nlf：沒有權限讀取「%s」。nlf 不會自動使用 sudo。\n%s",
				"nlf: permission denied reading %q. nlf never uses sudo.\n%s"), root, permissionHint(lang, runtime.GOOS))
		case errors.Is(err, fs.ErrNotExist):
			fmt.Fprintf(stderr, msg("nlf：找不到搜尋位置「%s」\n", "nlf: search location %q does not exist\n"), root)
			if strings.ContainsFunc(root, func(r rune) bool { return r >= utf8.RuneSelf }) {
				fmt.Fprint(stderr, msg("提示：路徑與後面的文字之間請加空白，或把路徑加上引號，例如 在 \"~/我的文件\" 找 pdf 檔\n",
					"hint: separate the path from the following words with a space, or quote it, e.g. in \"~/My Docs\"\n"))
			}
		default:
			fmt.Fprintf(stderr, "nlf: %v\n", err)
		}
		return 2
	}

	if n := len(rep.PermissionDenied); n > 0 {
		fmt.Fprintf(stderr, msg("\nnlf：有 %d 個位置因權限不足而略過（nlf 不會自動使用 sudo）：\n",
			"\nnlf: skipped %d location(s) because permission was denied (nlf never uses sudo):\n"), n)
		for _, p := range rep.PermissionDenied[:min(n, 5)] {
			fmt.Fprintf(stderr, "  %s\n", p)
		}
		if n > 5 {
			fmt.Fprintf(stderr, msg("  …還有 %d 個\n", "  …and %d more\n"), n-5)
		}
		fmt.Fprint(stderr, permissionHint(lang, runtime.GOOS))
	}
	if n := len(rep.OtherErrors); n > 0 {
		fmt.Fprintf(stderr, msg("nlf：有 %d 個位置無法讀取而略過，例如：%v\n",
			"nlf: skipped %d unreadable location(s), e.g. %v\n"), n, rep.OtherErrors[0])
	}
	if found == 0 {
		fmt.Fprint(stderr, msg("nlf：沒有找到符合條件的檔案。可加上 --explain 檢查 nlf 如何理解你的查詢。\n",
			"nlf: no matching files. Add --explain to check how the query was understood.\n"))
		return 1
	}
	return 0
}

// permissionHint suggests what to do after permission errors. goos picks
// the platform-specific advice: macOS privacy protection (TCC / Full Disk
// Access) or Linux file permissions and security modules.
func permissionHint(lang query.Lang, goos string) string {
	zh := lang == query.Chinese
	switch {
	case goos == "darwin" && zh:
		return "提示：可縮小搜尋範圍（例如 --dir ~/Documents）或用「排除」跳過這些目錄。" +
			"macOS 受保護的資料夾（如 ~/Library 的部分內容）需要在「系統設定 › 隱私權與安全性 › 完整磁碟取用權限」授權終端機。\n"
	case goos == "darwin":
		return "hint: narrow the search (e.g. --dir ~/Documents) or exclude those directories. " +
			"macOS-protected folders (parts of ~/Library, etc.) require granting your terminal Full Disk Access " +
			"in System Settings › Privacy & Security.\n"
	case zh:
		return "提示：可縮小搜尋範圍（例如 --dir /var/log）或用「排除」跳過這些目錄。" +
			"可用 ls -ld 檢查目錄的擁有者與權限；需要時請改用有權限的帳號執行（nlf 不會自動使用 sudo）。" +
			"SELinux 或 AppArmor 也可能拒絕存取。\n"
	default:
		return "hint: narrow the search (e.g. --dir /var/log) or exclude those directories. " +
			"Check the owner and mode with ls -ld; if needed, run nlf as an account that has access " +
			"(nlf never uses sudo itself). SELinux or AppArmor can also deny access.\n"
	}
}

func parseArgs(args []string) (*options, error) {
	o := &options{}
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch {
		case a == "--":
			o.words = append(o.words, args[i+1:]...)
			return o, nil
		case a == "-h" || a == "--help":
			o.help = true
		case a == "--version":
			o.version = true
		case a == "--explain":
			o.explain = true
		case a == "--confirm":
			o.confirm = true
		case a == "-l" || a == "--long":
			o.long = true
		case a == "-d" || a == "--dir":
			if i+1 >= len(args) {
				return nil, fmt.Errorf("%s needs a directory", a)
			}
			i++
			o.dir = args[i]
		case strings.HasPrefix(a, "--dir="):
			o.dir = strings.TrimPrefix(a, "--dir=")
		case len(a) > 1 && strings.HasPrefix(a, "-") && !strings.ContainsAny(a, " \t"):
			return nil, fmt.Errorf("unknown flag %s", a)
		default:
			o.words = append(o.words, a)
		}
	}
	return o, nil
}

// executionPlan describes what a search will do before it runs. The find
// command is shown for understanding only; nlf never executes it.
func executionPlan(q *query.Query, e search.Effects, p query.Platform) string {
	yn := func(b bool) string {
		if b {
			return "YES"
		}
		return "NO"
	}
	var b strings.Builder
	b.WriteString("NLF execution plan\n\n")
	b.WriteString("Search root:\n  " + rootOrDot(q.Root) + "\n\n")
	b.WriteString("Conditions:\n")
	if cs := query.Conditions(q, query.English); len(cs) > 0 {
		b.WriteString(query.FormatConditions(cs))
	} else {
		b.WriteString("  (none: every regular file)\n")
	}
	b.WriteString("\nApproximate " + p.Name() + " command (for reference only; nlf does not execute it):\n")
	b.WriteString("  " + query.FindCommand(q, p) + "\n\n")
	fmt.Fprintf(&b, `Safety:
  Read files:        %s
  Modify files:      %s
  Create files:      %s
  Delete files:      %s
  Execute shell:     %s
  sudo required:     %s

Risk:
  %s

`, yn(e.ReadFiles), yn(e.ModifyFiles), yn(e.CreateFiles), yn(e.DeleteFiles), yn(e.ExecuteShell), yn(e.Sudo), e.Risk())
	return b.String()
}

// approved reads one line and reports whether it is exactly "y" or "Y".
// Anything else, including an empty line, "yes" and EOF, cancels.
func approved(r io.Reader) bool {
	line, err := bufio.NewReader(r).ReadString('\n')
	if err != nil && err != io.EOF {
		return false
	}
	line = strings.TrimSuffix(strings.TrimSuffix(line, "\n"), "\r")
	return line == "y" || line == "Y"
}

func rootOrDot(r string) string {
	if r == "" {
		return "."
	}
	return r
}

func expandHome(p string) string {
	if p != "~" && !strings.HasPrefix(p, "~/") {
		return p
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return p
	}
	return filepath.Join(home, strings.TrimPrefix(p, "~"))
}
