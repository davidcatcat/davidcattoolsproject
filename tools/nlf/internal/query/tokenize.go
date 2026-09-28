package query

import (
	"regexp"
	"slices"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

type kind int

const (
	tWord   kind = iota // ASCII word, text is lower-cased
	tExt                // ".go" / "*.tar.gz", text is lower-cased with leading dot
	tNum                // number (Arabic or Chinese numerals)
	tQuoted             // quoted literal, text keeps original case
	tCJK                // known Chinese keyword from the dictionary
	tHan                // run of unknown Chinese (or other non-ASCII) characters
	tPunct              // punctuation: separators, sentence marks and reserved ( ) ! ?
	tOp                 // comparison operator: > >= < <=
)

type token struct {
	kind kind
	text string // normalized text used for matching
	raw  string // original text as typed
	num  float64
	// spaceBefore is true when whitespace (or the start of input) precedes
	// the token, so the parser can tell "設定 檔" from "設定檔".
	spaceBefore bool
}

var (
	numRe = regexp.MustCompile(`^(\d+(?:\.\d+)?)([a-z]*)$`)
	extRe = regexp.MustCompile(`^\*?\.[a-z0-9]{1,10}(\.[a-z0-9]{1,10})?$`)
)

var quotePairs = map[rune]rune{'"': '"', '\'': '\'', '“': '”', '‘': '’', '「': '」', '『': '』'}

const punctChars = ",;()!?，、。；：！？（）"

// cjkDict holds every Chinese keyword the parser understands. The tokenizer
// splits Chinese text by longest match against it.
var cjkDict = map[string]bool{}
var cjkMaxLen = 1

func addCJK(words ...string) {
	for _, w := range words {
		cjkDict[w] = true
		if n := utf8.RuneCountInString(w); n > cjkMaxLen {
			cjkMaxLen = n
		}
	}
}

const cnDigits = "零〇一二兩两三四五六七八九十百千"

func tokenize(s string) ([]token, error) {
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
				// Keep a path whole even when it contains non-ASCII
				// characters ("~/文件"), so it is never silently cut short.
				for j < len(rs) && isPathRune(rs[j]) {
					j++
				}
			}
			if w := string(rs[i:j]); !spaced && strings.Trim(w, ".") == "" {
				// Dots glued to the previous token ("x".", 錯誤., 檔..) are
				// sentence periods, never the path "." or ".."; only a
				// spaced "." or ".." ("in ..") names a directory.
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
			// Unknown non-ASCII character: merge into a run, but never
			// across whitespace ("系統 錯誤" is two words). The run is found
			// first and added once, so a long run costs linear time.
			j := hanRunEnd(rs, i+1)
			if n := len(toks); n > 0 && toks[n-1].kind == tHan && !spaced {
				toks[n-1].text += string(rs[i:j])
				toks[n-1].raw += string(rs[i:j])
			} else {
				emit(token{kind: tHan, text: string(rs[i:j]), raw: string(rs[i:j])})
			}
			i = j
		}
	}
	return toks, nil
}

// hanRunEnd returns where a run of unknown non-ASCII characters that
// continues at rs[j] ends: at the first rune the default case of tokenize
// would not merge into it (anything handled by an earlier case, a
// dictionary word, or Chinese digits that form a number up to the end of
// their run). Each run of digits is analyzed once, so the scan is linear.
func hanRunEnd(rs []rune, j int) int {
	digitsAt, numberAt := -1, []bool(nil) // numberAt[k]: rs[digitsAt+k:] up to the run end is a number
	for ; j < len(rs); j++ {
		r := rs[j]
		if r < utf8.RuneSelf || unicode.IsSpace(r) || quotePairs[r] != 0 || strings.ContainsRune("<>≥≤＞＜", r) ||
			strings.ContainsRune(punctChars, r) || longestCJK(rs[j:]) != "" {
			return j
		}
		if strings.ContainsRune(cnDigits, r) {
			if digitsAt < 0 || j >= digitsAt+len(numberAt) {
				end := j
				for end < len(rs) && strings.ContainsRune(cnDigits, rs[end]) {
					end++
				}
				digitsAt, numberAt = j, chineseNumberSuffixes(rs[j:end])
			}
			if numberAt[j-digitsAt] {
				return j
			}
		}
	}
	return j
}

// chineseNumberSuffixes reports, for each i, whether
// parseChineseNumber(string(run[i:])) succeeds, for a run of Chinese digits,
// in one pass from the right. parseChineseNumber's total is positive exactly
// when the value starts with a unit (十 == 一十), a unit follows a unit or a
// non-zero digit, or the value ends with a non-zero digit; a lone 零 or 〇
// succeeds too.
func chineseNumberSuffixes(run []rune) []bool {
	zero := func(r rune) bool { return r == '零' || r == '〇' }
	unit := func(r rune) bool { return r == '十' || r == '百' || r == '千' }
	ok := make([]bool, len(run))
	last := len(run) - 1
	endsNonZero := !unit(run[last]) && !zero(run[last])
	unitAfter := false // a unit at some m > i follows a unit or a non-zero digit
	for i := last; i >= 0; i-- {
		ok[i] = unit(run[i]) || unitAfter || endsNonZero || i == last && zero(run[i])
		if i > 0 && unit(run[i]) && !zero(run[i-1]) {
			unitAfter = true
		}
	}
	return ok
}

func isWordRune(r rune) bool {
	return r < utf8.RuneSelf && !unicode.IsSpace(r) &&
		!strings.ContainsRune(punctChars, r) && !strings.ContainsRune(`"<>`, r)
}

// hasPathPrefix reports whether an ASCII word starts like a path.
func hasPathPrefix(w string) bool {
	for _, pre := range []string{"/", "~/", "./", "../"} {
		if strings.HasPrefix(w, pre) {
			return true
		}
	}
	return false
}

// isPathRune reports whether r may continue an unquoted path. Unlike
// isWordRune it accepts non-ASCII letters; spaces, separator punctuation
// and quotes still end the path.
func isPathRune(r rune) bool {
	if r < utf8.RuneSelf {
		return isWordRune(r)
	}
	return !unicode.IsSpace(r) && !strings.ContainsRune(punctChars, r) &&
		!strings.ContainsRune("”’」』", r) && quotePairs[r] == 0
}

func classifyASCII(raw string) []token {
	var trailing []token
	// A sentence-ending period is punctuation, not part of the word. A path
	// ("/", "~/", "./", "../" prefix) that ends in a "." or ".." component
	// ("../..", "~/..", "./src/.") keeps it: that dot names a directory.
	if len(raw) > 1 && strings.HasSuffix(raw, ".") && strings.Trim(raw, ".") != "" &&
		!(hasPathPrefix(raw) && (strings.HasSuffix(raw, "/.") || strings.HasSuffix(raw, "/.."))) {
		raw = raw[:len(raw)-1]
		trailing = append(trailing, token{kind: tPunct, text: ".", raw: "."})
	}
	lower := strings.ToLower(raw)
	var out []token
	switch {
	case numRe.MatchString(lower) && isUnitSuffix(numRe.FindStringSubmatch(lower)[2]):
		m := numRe.FindStringSubmatch(lower)
		n, _ := strconv.ParseFloat(m[1], 64)
		out = append(out, token{kind: tNum, text: m[1], raw: raw[:len(m[1])], num: n})
		if m[2] != "" {
			out = append(out, token{kind: tWord, text: m[2], raw: raw[len(m[1]):]})
		}
	case extRe.MatchString(lower):
		out = append(out, token{kind: tExt, text: strings.TrimPrefix(lower, "*"), raw: raw})
	default:
		out = append(out, token{kind: tWord, text: lower, raw: raw})
	}
	return append(out, trailing...)
}

// isUnitSuffix reports whether s (as in "10mb", "7d") is empty or a known unit,
// so that words like "404error" stay intact.
func isUnitSuffix(s string) bool {
	_, size := sizeUnits[s]
	_, dur := timeUnits[s]
	return s == "" || size || dur || slices.Contains(countUnits, s)
}

func longestCJK(rs []rune) string {
	for n := min(cjkMaxLen, len(rs)); n > 0; n-- {
		if w := string(rs[:n]); cjkDict[w] {
			return w
		}
	}
	return ""
}

// parseChineseNumber handles values such as 三, 十五, 二十, 一百二十, 兩千.
func parseChineseNumber(s string) (int, bool) {
	digits := map[rune]int{'零': 0, '〇': 0, '一': 1, '二': 2, '兩': 2, '两': 2, '三': 3, '四': 4,
		'五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
	units := map[rune]int{'十': 10, '百': 100, '千': 1000}
	total, cur := 0, -1
	for _, r := range s {
		if d, ok := digits[r]; ok {
			cur = d
			continue
		}
		u := units[r]
		if cur == -1 {
			cur = 1 // 十 == 一十
		}
		total += cur * u
		cur = -1
	}
	if cur > 0 {
		total += cur
	}
	return total, total > 0 || s == "零" || s == "〇"
}
