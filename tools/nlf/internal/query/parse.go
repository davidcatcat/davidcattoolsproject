package query

import (
	"fmt"
	"math"
	"slices"
	"strings"
	"time"
)

// Parse converts a natural-language request into a Query. now anchors all
// relative times so the result is deterministic.
func Parse(input string, now time.Time) (*Query, error) {
	toks, err := tokenize(input)
	if err != nil {
		return nil, err
	}
	if len(toks) == 0 {
		return nil, &Error{zh: "查詢是空的", en: "empty query",
			hintZh: "例如：nlf \"最近 7 天修改的 go 檔\"", hintEn: `example: nlf "go files modified in the last 7 days"`}
	}
	p := &parser{toks: toks, q: &Query{Lang: DetectLang(input), Now: now}}
	for p.pos < len(p.toks) {
		if err := p.step(); err != nil {
			return nil, err
		}
	}
	q := p.q
	if !q.ModifiedAfter.IsZero() && !q.ModifiedBefore.IsZero() && !q.ModifiedAfter.Before(q.ModifiedBefore) {
		return nil, &Error{zh: "修改時間條件互相矛盾，不可能有檔案符合", en: "modification time conditions contradict each other"}
	}
	q.Extensions = dedupe(q.Extensions)
	q.ExcludeDirs = dedupe(q.ExcludeDirs)
	return q, nil
}

type parser struct {
	toks []token
	pos  int
	q    *Query
}

func (p *parser) cur() (token, bool) {
	if p.pos < len(p.toks) {
		return p.toks[p.pos], true
	}
	return token{}, false
}

// accept consumes the longest phrase from phrases that matches at the
// current position and returns it.
func (p *parser) accept(phrases []string) (string, bool) {
	best, n := p.matchAt(p.pos, phrases)
	p.pos += n
	return best, n > 0
}

// matchAt returns the longest phrase matching at token index pos and its
// length in tokens, without consuming anything.
func (p *parser) matchAt(pos int, phrases []string) (string, int) {
	best, bestLen := "", 0
	for _, ph := range phrases {
		parts := strings.Fields(ph)
		if len(parts) <= bestLen || pos+len(parts) > len(p.toks) {
			continue
		}
		ok := true
		for i, part := range parts {
			t := p.toks[pos+i]
			if (t.kind != tWord && t.kind != tCJK && t.kind != tPunct && t.kind != tOp) || t.text != part {
				ok = false
				break
			}
		}
		if ok {
			best, bestLen = ph, len(parts)
		}
	}
	return best, bestLen
}

func (p *parser) step() error {
	t, _ := p.cur()
	if t.kind == tQuoted {
		// A bare quoted string is text to search for inside files.
		if t.text == "" {
			return errEmptyContent()
		}
		p.q.Contents = append(p.q.Contents, t.text)
		p.pos++
		return nil
	}
	if _, ok := p.accept(ignoreCasePhrases); ok {
		p.q.IgnoreCase = true
		return nil
	}
	if _, ok := p.accept(contentPhrases); ok {
		return p.contentTerm()
	}
	save := p.pos
	if _, ok := p.accept(softContentPhrases); ok {
		if nt, ok := p.cur(); ok && (nt.kind == tQuoted || nt.kind == tHan || isFreeWord(nt)) {
			return p.contentTerm()
		}
		p.pos = save
	}
	if p.path() {
		return nil
	}
	if _, ok := p.accept(excludePhrases); ok {
		return p.excludeNames(false, t.raw)
	}
	if _, ok := p.accept(weakExcludePhrases); ok {
		return p.excludeNames(true, t.raw)
	}
	if _, ok := p.accept(todayPhrases); ok {
		p.after(midnight(p.q.Now))
		return nil
	}
	if _, ok := p.accept(yesterdayPhrases); ok {
		p.after(midnight(p.q.Now).AddDate(0, 0, -1))
		p.before(midnight(p.q.Now))
		return nil
	}
	if ok, err := p.comparator(); ok || err != nil {
		return err
	}
	if t.kind == tNum {
		return p.bareNumber()
	}
	if p.extension() {
		return nil
	}
	if _, ok := p.accept(orPhrases); ok {
		return &Error{Token: t.raw, zh: "v0.1 還不支援「或」條件，所有條件都會同時成立", en: "OR between conditions is not supported in v0.1; all conditions are combined with AND",
			hintZh: "提示：多個副檔名可以寫成「go 或 md 檔」。", hintEn: `hint: several extensions can be written as "go or md files".`}
	}
	if _, ok := p.accept(notPhrases); ok {
		return &Error{Token: t.raw, zh: "v0.1 還不支援否定條件", en: "negative conditions are not supported in v0.1",
			hintZh: "提示：要排除目錄請用「排除 目錄名」。", hintEn: `hint: to skip a directory use "exclude <dir>".`}
	}
	if _, ok := p.accept(fillers); ok {
		return nil
	}
	if t.kind == tPunct {
		p.pos++
		return nil
	}
	return errUnknown(t.raw)
}

func (p *parser) contentTerm() error {
	t, ok := p.cur()
	if !ok || !(t.kind == tQuoted || t.kind == tWord || t.kind == tExt || t.kind == tNum || t.kind == tHan) {
		return &Error{zh: "「包含」後面需要要搜尋的文字", en: `"containing" must be followed by the text to search for`,
			hintZh: "例如：包含 \"TODO\"", hintEn: `example: containing "TODO"`}
	}
	p.pos++
	if t.kind == tQuoted {
		if t.text == "" {
			return errEmptyContent()
		}
		p.q.Contents = append(p.q.Contents, t.text)
		return nil
	}
	// Unquoted text runs until whitespace or punctuation, so "API連線失敗"
	// stays whole. A known word glued to it is ambiguous and must be quoted.
	term := t.raw
	for {
		nt, ok := p.cur()
		if !ok || nt.spaceBefore || nt.kind == tPunct || nt.kind == tOp || nt.kind == tQuoted {
			break
		}
		if nt.kind == tHan || nt.kind == tNum || nt.kind == tExt || (nt.kind == tWord && !reserved[nt.text]) {
			term += nt.raw
			p.pos++
			continue
		}
		if nt.kind == tCJK && slices.Contains(contentBoundaries, nt.text) {
			break
		}
		return &Error{Token: term + nt.raw, zh: "要搜尋的文字與關鍵字連在一起，無法確定範圍", en: "search text runs into a keyword; its extent is ambiguous",
			hintZh: fmt.Sprintf("提示：請用引號標出要搜尋的文字，例如 包含 \"%s\" 或 包含 \"%s\"", term, term+nt.raw),
			hintEn: fmt.Sprintf(`hint: quote the text to search for, e.g. containing "%s"`, term+nt.raw)}
	}
	p.q.Contents = append(p.q.Contents, term)
	return nil
}

func errEmptyContent() *Error {
	return &Error{zh: "要搜尋的內容不能是空字串", en: "search text must not be empty"}
}

// excludeNames reads the directory names after an exclude phrase. weak
// phrases ("except", "除了") only count as exclusion when a directory word
// follows the names.
func (p *parser) excludeNames(weak bool, phrase string) error {
	var names []string
	sawTail := false
	for {
		t, ok := p.cur()
		if !ok {
			break
		}
		isName := t.kind == tQuoted || t.kind == tExt || t.kind == tHan || t.kind == tWord
		// After a separator, a keyword ends the list ("exclude vendor and larger than 1MB").
		if len(names) > 0 && t.kind == tWord && reserved[t.text] {
			isName = false
		}
		if !isName {
			break
		}
		if len(names) > 0 && t.kind != tQuoted && p.extNounAhead(true) {
			break // "exclude vendor and go files": the rest is an extension condition
		}
		name := t.raw
		if t.kind == tQuoted {
			name = t.text
		}
		p.pos++
		_, tail := p.accept(excludeTail)
		sawTail = sawTail || tail
		if _, n := p.matchAt(p.pos, fileNouns); n > 0 && !tail && t.kind != tQuoted {
			// "exclude go files", "exclude test files": files, not a directory.
			return &Error{Token: phrase + " " + t.raw, zh: "v0.1 只能排除目錄，不能排除某種檔案類型", en: "v0.1 can only exclude directories, not file types",
				hintZh: "提示：要排除目錄請寫「排除 名稱 目錄」。", hintEn: `hint: to skip a directory write "exclude <name> dir".`}
		}
		if t.kind != tQuoted && !tail && looksLikeFileType(t) {
			// "排除 .go", "exclude *.log", "exclude images": a file type, not a directory.
			return &Error{Token: t.raw, zh: "這看起來是檔案類型；v0.1 只能排除目錄", en: "this looks like a file type; v0.1 can only exclude directories",
				hintZh: "提示：如果真的是目錄名稱，請加上「目錄」或引號，例如 排除 \"" + name + "\" 目錄。",
				hintEn: `hint: if it really is a directory, add "dir" or quotes, e.g. exclude "` + name + `" dir.`}
		}
		names = append(names, strings.TrimSuffix(name, "/"))
		save := p.pos
		if _, ok := p.accept(listSeps); !ok {
			// Space-separated list: "排除 node_modules dist".
			if nt, ok := p.cur(); ok && !p.extNounAhead(true) && (isFreeWord(nt) || nt.kind == tQuoted ||
				(nt.kind == tExt && strings.HasPrefix(nt.raw, "."))) {
				continue
			}
			break
		}
		if nt, ok := p.cur(); !ok || nt.kind == tCJK || nt.kind == tNum || (nt.kind == tWord && reserved[nt.text]) || p.extNounAhead(true) {
			p.pos = save
			break
		}
	}
	if len(names) == 0 {
		return &Error{zh: "「排除」後面需要目錄名稱", en: `"exclude" must be followed by a directory name`,
			hintZh: "例如：排除 node_modules 和 .git", hintEn: "example: exclude node_modules and .git"}
	}
	if weak && !sawTail {
		return &Error{Token: phrase, zh: "無法確定是要排除目錄還是否定條件（v0.1 不支援否定條件）", en: "unclear whether this excludes a directory or negates a condition (negation is not supported in v0.1)",
			hintZh: "提示：要排除目錄請寫「排除 " + names[0] + "」或「除了 " + names[0] + " 目錄以外」。",
			hintEn: `hint: to skip a directory write "exclude ` + names[0] + `" or "except ` + names[0] + ` dir".`}
	}
	p.q.ExcludeDirs = append(p.q.ExcludeDirs, names...)
	return nil
}

// extNounAhead reports whether the tokens at the current position form an
// extension followed by a file noun ("go files"). With list, a separated
// list counts too ("go 和 md 檔").
func (p *parser) extNounAhead(list bool) bool {
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

// looksLikeFileType reports whether an exclude name is really a file
// pattern or type: a glob ("*.log"), a known extension (".go") or an
// alias ("images").
func looksLikeFileType(t token) bool {
	switch t.kind {
	case tExt:
		return strings.HasPrefix(t.text, "*") || strings.HasPrefix(t.raw, "*") ||
			extAliases[strings.TrimPrefix(t.text, ".")] != nil
	case tWord, tCJK:
		return strings.Contains(t.raw, "*") || extAliases[t.text] != nil
	}
	return false
}

// maxInt64Float is 2^63, the first float64 that no longer fits in int64
// (float64(math.MaxInt64) rounds up to it).
const maxInt64Float = float64(1 << 63)

// maxLimit bounds result counts so they always fit in an int.
const maxLimit = math.MaxInt32

type qtyKind int

const (
	qNone qtyKind = iota
	qSize
	qTime
	qCount
)

type quantity struct {
	n    float64
	kind qtyKind
	size int64
	dur  time.Duration
	raw  string
}

// comparator handles phrases like "larger than 10MB", "最近 7 天", "top 10",
// "最大的 5 個" and "older than 30 days".
func (p *parser) comparator() (bool, error) {
	save := p.pos
	start, _ := p.cur()
	var op string
	strict := false // a quantity is mandatory after this phrase
	if ph, ok := p.accept(mapKeys(cmpPhrases)); ok {
		op, strict = cmpPhrases[ph], true
	} else if start.kind == tOp {
		op, strict = start.text, true
		p.pos++
	} else if _, ok := p.accept(withinPhrases); ok {
		op = "within"
	} else if _, ok := p.accept(olderPhrases); ok {
		op, strict = "older", true
	} else if _, ok := p.accept(limitPhrases); ok {
		op = "limit"
	} else if _, ok := p.accept(largestPhrases); ok {
		op = "largest"
	} else if _, ok := p.accept(newestPhrases); ok {
		op = "newest"
	} else {
		return false, nil
	}
	qty, ok := p.quantity(op == "within" || op == "older")
	if !ok {
		switch {
		case op == "largest":
			p.q.Sort = SortLargest
			return true, nil
		case op == "newest":
			p.q.Sort = SortNewest
			return true, nil
		case strict:
			return true, &Error{Token: start.raw, zh: "後面需要數字與單位", en: "must be followed by a number and unit",
				hintZh: "例如：大於 10MB、超過 30 天", hintEn: "example: larger than 10MB, older than 30 days"}
		}
		p.pos = save
		return false, nil
	}
	return true, p.apply(op, qty, start.raw)
}

func (p *parser) bareNumber() error {
	start, _ := p.cur()
	qty, _ := p.quantity(false)
	op := ""
	if qty.kind == qNone || qty.kind == qCount {
		// "10 largest files", "5 newest"
		p.accept(countUnits)
		if _, ok := p.accept(postNumLargest); ok {
			op = "largest"
		} else if _, ok := p.accept(postNumNewest); ok {
			op = "newest"
		}
	}
	return p.apply(op, qty, start.raw)
}

func (p *parser) quantity(implicitOne bool) (quantity, bool) {
	var q quantity
	t, ok := p.cur()
	if ok && t.kind == tNum {
		q.n, q.raw = t.num, t.raw
		p.pos++
	} else if !implicitOne {
		return q, false
	} else {
		q.n = 1 // "last week", "過去一個月" without a number
	}
	unitTok, _ := p.cur()
	if u, ok := p.accept(mapKeys(timeUnits)); ok {
		q.kind, q.dur = qTime, timeUnits[u]
	} else if q.raw == "" {
		return q, false
	} else if u, ok := p.accept(mapKeys(sizeUnits)); ok {
		q.kind, q.size = qSize, sizeUnits[u]
	} else if _, ok := p.accept(countUnits); ok {
		q.kind = qCount
	}
	if q.kind != qNone && q.kind != qCount {
		q.raw += unitTok.raw
	}
	return q, true
}

func (p *parser) apply(op string, qty quantity, near string) error {
	// A postfix ("以上", "以內", "ago") decides the direction unless a prefix already did.
	save := p.pos
	if post, ok := p.accept(mapKeys(postfixPhrases)); ok {
		if nt, ok := p.cur(); ok && nt.text == "than" {
			p.pos = save // "1mb or smaller than 10k" is an OR, not a postfix
		} else if op == "" || op == "limit" {
			op = postfixPhrases[post]
		}
	}
	switch qty.kind {
	case qSize:
		switch op {
		case "largest", "limit", "within": // "最大 10MB", "最多 10MB" mean at most
			op = "<="
		case ">", ">=", "<", "<=":
		case "":
			return &Error{Token: qty.raw, zh: "檔案大小需要比較方式", en: "file size needs a comparison",
				hintZh: "例如：大於 10MB、10MB 以下", hintEn: "example: larger than 10MB, smaller than 1KB"}
		default:
			return &Error{Token: qty.raw, zh: "這個比較方式不能用在檔案大小", en: "this comparison does not apply to file size"}
		}
		b := qty.n * float64(qty.size)
		if b >= maxInt64Float {
			return &Error{Token: qty.raw, zh: "檔案大小太大", en: "file size is too large"}
		}
		p.q.Sizes = append(p.q.Sizes, SizeCond{Op: op, Bytes: int64(b)})
	case qTime:
		d := qty.n * float64(qty.dur)
		if d >= maxInt64Float {
			return &Error{Token: qty.raw, zh: "時間範圍太大", en: "time span is too large",
				hintZh: "提示：最多約 292 年", hintEn: "hint: the maximum is about 292 years"}
		}
		t := p.q.Now.Add(-time.Duration(d))
		switch op {
		case "within", "limit", "<", "<=":
			p.after(t)
		case "older", ">", ">=":
			p.before(t)
		default:
			return &Error{Token: qty.raw, zh: "時間需要說明是「最近」還是「超過」", en: "time needs a direction",
				hintZh: "例如：最近 7 天、7 天內、超過 30 天、30 天前", hintEn: "example: in the last 7 days, older than 30 days, 30 days ago"}
		}
	case qCount, qNone:
		if qty.kind == qNone && op != "limit" && op != "<=" && op != "largest" && op != "newest" {
			return &Error{Token: near, zh: "數字需要單位", en: "number needs a unit",
				hintZh: "例如：10MB、7 天、10 個", hintEn: "example: 10MB, 7 days, top 10"}
		}
		switch op {
		case "limit", "<=", "largest", "newest":
		case "<", "within", "":
		default:
			return &Error{Token: near, zh: "不支援這種數量條件", en: "this count condition is not supported",
				hintZh: "例如：最多 10 個", hintEn: "example: top 10"}
		}
		if qty.n < 1 || qty.n != math.Trunc(qty.n) {
			return &Error{Token: qty.raw, zh: "數量必須是正整數", en: "count must be a positive whole number"}
		}
		if qty.n > maxLimit {
			return &Error{Token: qty.raw, zh: "數量太大", en: "count is too large",
				hintZh: fmt.Sprintf("提示：最多 %d", maxLimit), hintEn: fmt.Sprintf("hint: the maximum is %d", maxLimit)}
		}
		p.q.Limit = int(qty.n)
		switch op {
		case "within": // "最近 5 個", "last 5 files"
			p.q.Sort = SortNewest
		case "largest":
			p.q.Sort = SortLargest
		case "newest":
			p.q.Sort = SortNewest
		}
		p.accept(countUnits)
		if _, ok := p.accept(postNumLargest); ok {
			p.q.Sort = SortLargest
		} else if _, ok := p.accept(postNumNewest); ok {
			p.q.Sort = SortNewest
		}
	}
	return nil
}

func (p *parser) after(t time.Time) {
	if p.q.ModifiedAfter.IsZero() || t.After(p.q.ModifiedAfter) {
		p.q.ModifiedAfter = t
	}
}

func (p *parser) before(t time.Time) {
	if p.q.ModifiedBefore.IsZero() || t.Before(p.q.ModifiedBefore) {
		p.q.ModifiedBefore = t
	}
}

// extension handles ".go", "*.md", "go files", "pdf 和 docx 檔", "圖片",
// "extension heic".
func (p *parser) extension() bool {
	save := p.pos
	_, trig := p.accept(extTriggers)
	if trig {
		p.accept(extCopula)
	}
	// Any alphanumeric word may name an extension when a file noun follows
	// ("ovpn 檔", "tfstate files") or a trigger precedes it ("副檔名 heic").
	// Otherwise only known extensions and aliases are accepted.
	start := p.pos
	cands, noun := p.extList(true, trig)
	if !noun && !trig {
		p.pos = start
		cands, noun = p.extList(false, trig)
	}
	if len(cands) == 0 {
		p.pos = save
		return false
	}
	for _, c := range cands {
		if c.kind == tWord && aliasNeedsNoun[c.text] && !noun && !trig {
			p.pos = save
			return false
		}
	}
	for _, c := range cands {
		switch {
		case c.kind == tExt:
			p.q.Extensions = append(p.q.Extensions, strings.TrimPrefix(c.text, "."))
		case extAliases[c.text] != nil:
			p.q.Extensions = append(p.q.Extensions, extAliases[c.text]...)
		default:
			p.q.Extensions = append(p.q.Extensions, strings.TrimPrefix(c.text, "."))
		}
	}
	return true
}

// isFreeWord reports whether t is an ASCII word with no meaning to the parser.
func isFreeWord(t token) bool {
	return t.kind == tWord && !reserved[t.text] && extAliases[t.text] == nil
}

// extList reads a list of extension candidates ("go", "go 和 md") and an
// optional trailing file noun.
func (p *parser) extList(literalOK, trig bool) (cands []token, noun bool) {
	for {
		t, ok := p.cur()
		if !ok || !isExtCandidate(t, literalOK, trig) {
			break
		}
		cands = append(cands, t)
		p.pos++
		sep := p.pos
		if _, ok := p.accept(listSeps); !ok {
			break
		}
		if nt, ok := p.cur(); !ok || !isExtCandidate(nt, literalOK, trig) {
			p.pos = sep
			break
		}
	}
	if len(cands) > 0 {
		_, noun = p.accept(fileNouns)
	}
	return cands, noun
}

// isExtCandidate reports whether t can name an extension. literalOK allows
// any short alphanumeric word; after an explicit trigger ("extension")
// keywords are allowed too, otherwise they keep their meaning.
func isExtCandidate(t token, literalOK, trig bool) bool {
	switch t.kind {
	case tExt:
		return true
	case tWord, tCJK:
		if extAliases[t.text] != nil {
			return true
		}
		return literalOK && t.kind == tWord && isAlnum(t.text) && len(t.text) <= 10 &&
			!notExtWords[t.text] && (trig || !reserved[t.text])
	}
	return false
}

func (p *parser) path() bool {
	save := p.pos
	_, trig := p.accept(pathTriggers)
	t, ok := p.cur()
	if ok && (t.kind == tWord || t.kind == tQuoted) {
		raw := t.raw
		if t.kind == tQuoted {
			raw = t.text
		}
		if looksLikePath(raw) && p.q.Root == "" {
			p.q.Root = raw
			p.pos++
			return true
		}
	}
	if trig {
		p.pos = save
	}
	return false
}

func looksLikePath(s string) bool {
	if s == "." || s == ".." || s == "~" {
		return true
	}
	for _, pre := range []string{"/", "~/", "./", "../"} {
		if strings.HasPrefix(s, pre) {
			return true
		}
	}
	return false
}

func midnight(t time.Time) time.Time {
	y, m, d := t.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, t.Location())
}

func isAlnum(s string) bool {
	for _, r := range s {
		if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9') {
			return false
		}
	}
	return s != ""
}

func mapKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

func dedupe(in []string) []string {
	var out []string
	for _, s := range in {
		if !slices.Contains(out, s) {
			out = append(out, s)
		}
	}
	return out
}

// reserved holds every single word that has a meaning to the parser.
var reserved = map[string]bool{}

func init() {
	lists := [][]string{ignoreCasePhrases, contentPhrases, excludePhrases, weakExcludePhrases, listSeps, limitPhrases,
		largestPhrases, newestPhrases, withinPhrases, olderPhrases, countUnits, extTriggers,
		todayPhrases, yesterdayPhrases, orPhrases, notPhrases, mapKeys(cmpPhrases), mapKeys(postfixPhrases),
		fillers, fileNouns, pathTriggers, extCopula, softContentPhrases,
		mapKeys(timeUnits), mapKeys(sizeUnits)}
	for _, l := range lists {
		for _, ph := range l {
			for _, w := range strings.Fields(ph) {
				reserved[w] = true
			}
		}
	}
}
