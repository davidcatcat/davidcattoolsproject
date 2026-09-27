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
	tPunct              // separator punctuation
	tOp                 // comparison operator: > >= < <=
)

type token struct {
	kind kind
	text string // normalized text used for matching
	raw  string // original text as typed
	num  float64
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
	rs := []rune(s)
	for i := 0; i < len(rs); {
		r := rs[i]
		switch {
		case unicode.IsSpace(r):
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
			toks = append(toks, token{kind: tQuoted, text: lit, raw: string(rs[i : j+1])})
			i = j + 1
		case strings.ContainsRune("<>≥≤＞＜", r):
			op := map[rune]string{'<': "<", '>': ">", '≥': ">=", '≤': "<=", '＞': ">", '＜': "<"}[r]
			j := i + 1
			if (r == '<' || r == '>') && j < len(rs) && rs[j] == '=' {
				op += "="
				j++
			}
			toks = append(toks, token{kind: tOp, text: op, raw: string(rs[i:j])})
			i = j
		case strings.ContainsRune(punctChars, r):
			toks = append(toks, token{kind: tPunct, text: string(r), raw: string(r)})
			i++
		case r < utf8.RuneSelf:
			j := i
			for j < len(rs) && isWordRune(rs[j]) {
				j++
			}
			toks = append(toks, classifyASCII(string(rs[i:j]))...)
			i = j
		default:
			if w := longestCJK(rs[i:]); w != "" {
				toks = append(toks, token{kind: tCJK, text: w, raw: w})
				i += utf8.RuneCountInString(w)
				continue
			}
			if strings.ContainsRune(cnDigits, r) {
				j := i
				for j < len(rs) && strings.ContainsRune(cnDigits, rs[j]) {
					j++
				}
				if n, ok := parseChineseNumber(string(rs[i:j])); ok {
					toks = append(toks, token{kind: tNum, text: string(rs[i:j]), raw: string(rs[i:j]), num: float64(n)})
					i = j
					continue
				}
			}
			// Unknown non-ASCII character: merge into a run.
			if n := len(toks); n > 0 && toks[n-1].kind == tHan {
				toks[n-1].text += string(r)
				toks[n-1].raw += string(r)
			} else {
				toks = append(toks, token{kind: tHan, text: string(r), raw: string(r)})
			}
			i++
		}
	}
	return toks, nil
}

func isWordRune(r rune) bool {
	return r < utf8.RuneSelf && !unicode.IsSpace(r) &&
		!strings.ContainsRune(punctChars, r) && !strings.ContainsRune(`"<>`, r)
}

func classifyASCII(raw string) []token {
	var trailing []token
	// A sentence-ending period is punctuation, not part of the word.
	if len(raw) > 1 && strings.HasSuffix(raw, ".") && strings.Trim(raw, ".") != "" {
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
