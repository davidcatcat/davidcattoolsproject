// Command nlf (Natural Language Find) finds files from a plain Chinese or
// English description, without calling any language model.
package main

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/query"
	"github.com/davidcatcat/davidcattoolsproject/tools/nlf/internal/search"
)

const version = "0.1.0"

const usage = `nlf — Natural Language Find (v` + version + `)

Usage:
  nlf [flags] "<query>"

Flags:
  --explain        show how the query is understood and an equivalent find command; do not search
  -l, --long       print size and modification time before each path
  -d, --dir PATH   directory to search (default: current directory)
  -h, --help       show this help
  --version        print version

Examples:
  nlf "go files containing TODO modified in the last 7 days"
  nlf "larger than 100MB excluding node_modules and .git, 10 largest"
  nlf "找出最近 3 天修改、大於 1MB 的 pdf 檔，最多 10 個"
  nlf "在 ~/Documents 包含 \"發票\" 的 txt 檔，排除 Archive"
  nlf --explain "最大的 5 個圖片"

Understood conditions (combined with AND):
  extension  .go  *.md  go files  pdf 檔  go 或 md 檔  圖片  images  副檔名 heic
  content    containing "text"  包含 TODO  內容有「錯誤」  ignore case / 不分大小寫
  modified   last 7 days  within 2 hours  older than 30 days  3 days ago  today  yesterday
             最近 7 天  3 小時內  超過 30 天  3 天前  今天  昨天
  size       larger than 10MB  smaller than 1KB  at least 1GB  大於 10MB  10MB 以上  小於 1KB
  count      top 10  first 5  最多 10 個  前 10 個  10 largest  最大的 5 個  最新的 3 個
  exclude    exclude node_modules and .git  排除 vendor、build 目錄
  location   in ~/Documents  在 ./src  (or --dir)

Sizes use 1 KB = 1024 bytes. Put text containing spaces or Chinese in quotes.
nlf is read-only and never uses sudo; unreadable paths are skipped and reported.

Exit status: 0 files found, 1 no files found, 2 error.
`

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr, time.Now()))
}

type options struct {
	help, version bool
	explain, long bool
	dir           string
	words         []string
}

func run(args []string, stdout, stderr io.Writer, now time.Time) int {
	opts, err := parseArgs(args)
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

	if opts.explain {
		fmt.Fprint(stdout, query.Explain(q))
		return 0
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
	root := q.Root
	if root == "" {
		root = "."
	}
	if err != nil {
		switch {
		case errors.Is(err, fs.ErrPermission):
			fmt.Fprintf(stderr, msg("nlf：沒有權限讀取「%s」。nlf 不會自動使用 sudo。\n%s",
				"nlf: permission denied reading %q. nlf never uses sudo.\n%s"), root, permissionHint(lang))
		case errors.Is(err, fs.ErrNotExist):
			fmt.Fprintf(stderr, msg("nlf：找不到搜尋位置「%s」\n", "nlf: search location %q does not exist\n"), root)
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
		fmt.Fprint(stderr, permissionHint(lang))
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

func permissionHint(lang query.Lang) string {
	if lang == query.Chinese {
		return "提示：可縮小搜尋範圍（例如 --dir ~/Documents）或用「排除」跳過這些目錄。" +
			"macOS 受保護的資料夾（如 ~/Library 的部分內容）需要在「系統設定 › 隱私權與安全性 › 完整磁碟取用權限」授權終端機。\n"
	}
	return "hint: narrow the search (e.g. --dir ~/Documents) or exclude those directories. " +
		"macOS-protected folders (parts of ~/Library, etc.) require granting your terminal Full Disk Access " +
		"in System Settings › Privacy & Security.\n"
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
