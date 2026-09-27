package query

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Date and time conditions.
//
// Every date or time expression becomes a half-open range on the file's
// modification time, applied through p.after / p.before:
//
//	start <= mtime < end
//
// Calendar units use the local calendar (time.Date and AddDate in the
// location of Query.Now), never fixed 24-hour durations, so a DST change
// cannot move a day boundary:
//
//	2026 年, in 2026           [2026-01-01, 2027-01-01)
//	2026 年 9 月, 2026-09       [2026-09-01, 2026-10-01)
//	2026-09-28, 9 月 28 日      [2026-09-28, 2026-09-29)
//
// A date without a year uses the year of Query.Now; "MM/DD" is always
// month/day. A range ("A 到 B", "from A to B", "between A and B") starts at
// the start of A and ends at the end of B, so a date end is inclusive
// ("9/1 到 9/15" is [9/1, 9/16)), while an end with a clock time is exact
// ("1 點到 5 點" is [01:00, 05:00)), and an end of "now" / "現在" is the exact
// instant Query.Now ("從 9 月 1 日到現在" is [9/1, now), so files with a future
// mtime are excluded). "since" / "從 A" / "A 以來" have no end at all
// ([A, unbounded)). Relative times ("last 6 hours", "最近 7 天") are
// durations and are handled by comparator, not here.

// span is a parsed date or time: a calendar unit [lo, hi), or an instant
// (a date with a clock time, lo == hi), or "now".
type span struct {
	lo, hi  time.Time
	instant bool
	now     bool
	day     bool   // a calendar day, which a clock time may refine
	month   bool   // a calendar month, which "15 日" in a range end may refine
	period  string // meridiem of the clock time ("am", "下午", ...), inherited by a range end
}

var (
	datePreps  = []string{"in", "on", "at", "during", "of", "from", "between", "since", "從", "从", "自從", "自从"}
	sincePreps = map[string]bool{"since": true, "從": true, "从": true, "自從": true, "自从": true}
	rangeTo    = []string{"to", "through", "thru", "until", "till", "-", "~", "到", "至"}
	betweenAnd = []string{"and", "to", "和", "與", "与", "到", "至", "-"}
	sincePost  = []string{"以來", "以来", "開始", "开始", "至今"}
	nowWords   = []string{"now", "現在", "现在", "目前"}

	dayWords = map[string]int{"today": 0, "今天": 0, "今日": 0, "yesterday": -1, "昨天": -1, "昨日": -1,
		"day before yesterday": -2, "the day before yesterday": -2, "前天": -2}
	monthWords = map[string]int{"this month": 0, "本月": 0, "這個月": 0, "这个月": 0,
		"last month": -1, "上個月": -1, "上个月": -1, "上月": -1}
	yearWords = map[string]int{"this year": 0, "今年": 0, "last year": -1, "去年": -1}

	monthNames = buildMonthNames()

	// Words matched as unknown Han tokens (tHan) rather than dictionary
	// entries, so that text such as "帳號" or "重點" still tokenizes as one word.
	dayMarks   = map[string]bool{"日": true, "號": true, "号": true}
	hourMarks  = map[string]bool{"點": true, "点": true, "點鐘": true, "点钟": true, "點半": true, "点半": true}
	periodMark = map[string]string{"凌晨": "凌晨", "早上": "上午", "上午": "上午", "中午": "中午", "下午": "下午", "晚上": "晚上"}

	ymdRe       = regexp.MustCompile(`^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$`)
	ymRe        = regexp.MustCompile(`^(\d{4})[-/](\d{1,2})$`)
	mdRe        = regexp.MustCompile(`^(\d{1,2})/(\d{1,2})$`)
	ambiguousRe = regexp.MustCompile(`^(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}\.\d{1,2}(\.\d{1,2})?|\d{2}[-/]\d{1,2}[-/]\d{1,2})$`)
	clockRe     = regexp.MustCompile(`^(\d{1,2}):(\d{2})(am|pm)?$`)
	hourAmPmRe  = regexp.MustCompile(`^(\d{1,2})(am|pm)$`)
)

// buildMonthNames maps full and three-letter English month names (and
// "sept") to months.
func buildMonthNames() map[string]time.Month {
	names := map[string]time.Month{"sept": time.September}
	for m := time.January; m <= time.December; m++ {
		n := strings.ToLower(m.String())
		names[n] = m
		names[n[:3]] = m
	}
	return names
}

// dateExpr parses a date, a date range or a time range at the current
// position. It reports false, consuming nothing, when there is none.
func (p *parser) dateExpr() (bool, error) {
	save := p.pos
	prep, _ := p.accept(datePreps)
	a, ok, err := p.point(nil, prep != "", false)
	if err != nil {
		return true, err
	}
	if !ok {
		p.pos = save
		return false, nil
	}
	// "today between 2am and 4am", "yesterday from 23:00 to today 02:00":
	// a day followed by a range of times on it.
	if a.day && !a.instant {
		inner := p.pos
		if w, ok := p.accept([]string{"between", "from", "從", "从"}); ok {
			if b, ok, err := p.point(&a, false, false); err != nil {
				return true, err
			} else if ok && b.instant {
				a, prep = b, w
			} else {
				p.pos = inner
			}
		}
	}

	var end *span
	switch {
	case prep == "between":
		if _, ok := p.accept(betweenAnd); !ok {
			return true, &Error{Token: p.rawFrom(save), zh: "「between」需要「and」連接結束時間", en: `"between" needs "and" before the end`,
				hintEn: "example: between Sep 1 and Sep 15"}
		}
		b, err := p.rangeEnd(&a, save)
		if err != nil {
			return true, err
		}
		end = &b
	default:
		if _, ok := p.accept(rangeTo); ok {
			b, err := p.rangeEnd(&a, save)
			if err != nil {
				return true, err
			}
			end = &b
		}
	}

	if end == nil {
		_, post := p.accept(sincePost)
		if sincePreps[prep] || post {
			p.after(a.lo) // "since Sep 1", "從 9 月 1 日", "9 月 1 日以來": no upper bound
			return true, nil
		}
		if a.instant {
			return true, &Error{Token: p.rawFrom(save), zh: "單一時間點不能當作條件，請寫成時間範圍", en: "a single point in time is not a range",
				hintZh: "例如：今天 1 點到 5 點、2026-09-28 01:00 到 03:30", hintEn: "example: today from 01:00 to 03:30"}
		}
		p.after(a.lo)
		p.before(a.hi)
		return true, nil
	}
	stop := end.hi // a date end is inclusive: "9/1 到 9/15" ends at 9/16 00:00
	switch {
	case end.now:
		stop = p.q.Now // "到現在", "until now": up to, not including, now
	case end.instant:
		stop = end.lo // a clock end is exact: "1 點到 5 點" ends at 05:00
	}
	if !a.lo.Before(stop) {
		return true, &Error{Token: p.rawFrom(save), zh: "開始時間必須早於結束時間", en: "start time must be before end time",
			hintZh: fmt.Sprintf("提示：目前解讀為 %s 到 %s", a.lo.Format(timeLayout), stop.Format(timeLayout)),
			hintEn: fmt.Sprintf("hint: understood as %s to %s", a.lo.Format(timeLayout), stop.Format(timeLayout))}
	}
	p.after(a.lo)
	p.before(stop)
	return true, nil
}

func (p *parser) rangeEnd(start *span, from int) (span, error) {
	b, ok, err := p.point(start, true, true)
	if err != nil {
		return span{}, err
	}
	if !ok {
		return span{}, &Error{Token: p.rawFrom(from), zh: "時間範圍需要結束日期或時間", en: "the range needs an end date or time",
			hintZh: "例如：9 月 1 日到 9 月 15 日、1 點到 5 點、從 9 月 1 日到現在", hintEn: "example: from Sep 1 to Sep 15, from 01:00 to 03:30, since Sep 1"}
	}
	return b, nil
}

// point parses one date, date + clock time, clock time or (nowOK) "now".
// ctx is the start of a range: its year, month, day and meridiem fill in
// what a range end leaves out ("到 15 日", "到 03:30", "下午 1 點到 5 點").
func (p *parser) point(ctx *span, yearOK, nowOK bool) (span, bool, error) {
	start := p.pos
	if nowOK {
		if _, ok := p.accept(nowWords); ok {
			return span{now: true}, true, nil
		}
	}
	d, ok, err := p.calendar(ctx, yearOK)
	if err != nil {
		return span{}, false, err
	}
	if !ok {
		base := p.today()
		inherit := ""
		if ctx != nil {
			base = midnight(ctx.lo)
			inherit = ctx.period
		}
		c, ok, err := p.clock(base, inherit)
		if err != nil || !ok {
			p.pos = start
			return span{}, false, err
		}
		return c, true, nil
	}
	if d.day {
		inherit := ""
		if ctx != nil && ctx.instant {
			inherit = ctx.period
		}
		at := p.pos
		p.accept([]string{"at"})
		c, ok, err := p.clock(d.lo, inherit)
		if err != nil {
			return span{}, false, err
		}
		if ok {
			return c, true, nil
		}
		p.pos = at
	}
	return d, true, nil
}

// calendar parses a year, month or day.
func (p *parser) calendar(ctx *span, yearOK bool) (span, bool, error) {
	start := p.pos
	now := p.q.Now
	year := now.Year()
	if ctx != nil {
		year = ctx.lo.Year()
	}

	if w, n := p.matchAt(p.pos, mapKeys(dayWords)); n > 0 {
		p.pos += n
		return p.daySpan(p.today().AddDate(0, 0, dayWords[w])), true, nil
	}
	if w, n := p.matchAt(p.pos, mapKeys(monthWords)); n > 0 {
		p.pos += n
		first := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, now.Location()).AddDate(0, monthWords[w], 0)
		return p.monthSpan(first.Year(), first.Month()), true, nil
	}
	if w, n := p.matchAt(p.pos, mapKeys(yearWords)); n > 0 {
		p.pos += n
		y := now.Year() + yearWords[w]
		// "今年 9 月", "去年 9 月 28 日"
		if s, ok, err := p.chineseMonthDay(y, start); ok || err != nil {
			return s, ok, err
		}
		return p.yearSpan(y), true, nil
	}

	t, ok := p.cur()
	if !ok {
		return span{}, false, nil
	}
	switch t.kind {
	case tWord:
		if ambiguousRe.MatchString(t.text) {
			return span{}, false, &Error{Token: t.raw, zh: "日期格式有歧義", en: "ambiguous date format",
				hintZh: "提示：請使用 YYYY-MM-DD、YYYY/MM/DD 或 MM/DD（月/日），例如 2026-09-28、9/28",
				hintEn: "hint: use YYYY-MM-DD, YYYY/MM/DD or MM/DD (month/day), e.g. 2026-09-28, 9/28"}
		}
		if m := ymdRe.FindStringSubmatch(t.text); m != nil {
			p.pos++
			return p.validDay(atoi(m[1]), atoi(m[2]), atoi(m[3]), t.raw)
		}
		if m := ymRe.FindStringSubmatch(t.text); m != nil {
			p.pos++
			return p.validMonth(atoi(m[1]), atoi(m[2]), t.raw)
		}
		if m := mdRe.FindStringSubmatch(t.text); m != nil {
			p.pos++
			return p.validDay(year, atoi(m[1]), atoi(m[2]), t.raw)
		}
		if m, ok := monthNames[t.text]; ok {
			return p.englishMonth(m, year, yearOK || ctx != nil, start)
		}
	case tNum:
		if t.num != float64(int(t.num)) {
			return span{}, false, nil
		}
		// "2026 年 [9 月 [28 日]]"
		if isYearNumber(t) && p.markAt(p.pos+1, "年") {
			p.pos += 2
			y := int(t.num)
			if s, ok, err := p.chineseMonthDay(y, start); ok || err != nil {
				return s, ok, err
			}
			if p.postfixAhead() {
				p.pos = start // "1000 年內": a duration, not a year
				return span{}, false, nil
			}
			return p.yearSpan(y), true, nil
		}
		// "9 月 [28 日]" in the current year (or the range start's year)
		if p.markAt(p.pos+1, "月") {
			return p.chineseMonthDay(year, start)
		}
		// "到 15 日": a day in the range start's month
		if ctx != nil && (ctx.day || ctx.month || ctx.instant) && p.dayMarkAt(p.pos+1) {
			d := int(t.num)
			p.pos += 2
			return p.validDay(ctx.lo.Year(), int(ctx.lo.Month()), d, p.rawFrom(start))
		}
		// "in 2026", "from 2025": a bare year needs a date preposition
		if yearOK && isYearNumber(t) && !p.unitAt(p.pos+1) {
			p.pos++
			return p.yearSpan(int(t.num)), true, nil
		}
	}
	return span{}, false, nil
}

// chineseMonthDay parses "9 月" or "9 月 28 日" in year y. It consumes
// nothing and reports false when no month follows.
func (p *parser) chineseMonthDay(y, from int) (span, bool, error) {
	t, ok := p.cur()
	if !ok || t.kind != tNum || t.num != float64(int(t.num)) || !p.markAt(p.pos+1, "月") {
		return span{}, false, nil
	}
	save := p.pos
	p.pos += 2
	m := int(t.num)
	if d, ok := p.cur(); ok && d.kind == tNum && d.num == float64(int(d.num)) && p.dayMarkAt(p.pos+1) {
		p.pos += 2
		return p.validDay(y, m, int(d.num), p.rawFrom(from))
	}
	if p.postfixAhead() {
		p.pos = save // "3 月前", "3 月內": months as a duration
		if from != save {
			return span{}, false, &Error{Token: p.rawFrom(from), zh: "看不懂這個時間", en: "cannot understand this time"}
		}
		return span{}, false, nil
	}
	return p.validMonth(y, m, p.rawFrom(from))
}

// englishMonth parses "September", "Sep 28", "Sep 28, 2026", "September 2026",
// "September this year" after the month name at the current position.
func (p *parser) englishMonth(m time.Month, year int, prepSeen bool, from int) (span, bool, error) {
	name, _ := p.cur()
	p.pos++
	day := 0
	if t, ok := p.cur(); ok && t.kind == tNum && t.num == float64(int(t.num)) && !isYearNumber(t) && !p.unitAt(p.pos+1) &&
		!p.hourMarkAt(p.pos+1) {
		day = int(t.num)
		p.pos++
		if nt, ok := p.cur(); ok && nt.kind == tPunct && nt.text == "," {
			p.pos++
		}
	}
	if t, ok := p.cur(); ok && t.kind == tNum && isYearNumber(t) {
		year = int(t.num)
		p.pos++
	} else if w, n := p.matchAt(p.pos, mapKeys(yearWords)); n > 0 {
		year = p.q.Now.Year() + yearWords[w]
		p.pos += n
	} else if day == 0 && name.text == "may" && !prepSeen {
		p.pos = from // "may" alone is too often the verb
		return span{}, false, nil
	}
	if day > 0 {
		return p.validDay(year, int(m), day, p.rawFrom(from))
	}
	return p.monthSpan(year, m), true, nil
}

// clock parses a time of day on the date of base: "23:00", "2:30pm", "2am",
// "2 pm", "凌晨 1 點", "下午 3 點 30 分". inherit is the meridiem of a range
// start, applied to an end that has none ("下午 1 點到 5 點").
func (p *parser) clock(base time.Time, inherit string) (span, bool, error) {
	start := p.pos
	period := ""
	if t, ok := p.cur(); ok && (t.kind == tHan || t.kind == tCJK) && periodMark[t.text] != "" {
		period = periodMark[t.text]
		p.pos++
	}
	h, m, found := -1, 0, false
	t, ok := p.cur()
	switch {
	case !ok:
	case t.kind == tWord && clockRe.MatchString(t.text):
		g := clockRe.FindStringSubmatch(t.text)
		h, m, found = atoi(g[1]), atoi(g[2]), true
		if g[3] != "" {
			period = g[3]
		}
		p.pos++
	case t.kind == tWord && hourAmPmRe.MatchString(t.text):
		g := hourAmPmRe.FindStringSubmatch(t.text)
		h, found, period = atoi(g[1]), true, g[2]
		p.pos++
	case t.kind == tNum && t.num == float64(int(t.num)) && p.hourMarkAt(p.pos+1):
		h, found = int(t.num), true
		half := strings.HasSuffix(p.toks[p.pos+1].text, "半") // "11 點半" = 11:30
		p.pos += 2
		if half {
			m = 30
		} else if mt, ok := p.cur(); ok && mt.kind == tNum && mt.num == float64(int(mt.num)) && p.markAt(p.pos+1, "分") {
			m = int(mt.num)
			p.pos += 2
		}
	case t.kind == tNum && t.num == float64(int(t.num)) && p.wordAt(p.pos+1, "am", "pm"):
		h, found, period = int(t.num), true, p.toks[p.pos+1].text
		p.pos += 2
	}
	if found && period == "" && p.wordAt(p.pos, "am", "pm") {
		period = p.toks[p.pos].text // "11:30 pm"
		p.pos++
	}
	if !found {
		if period != "" {
			return span{}, false, &Error{Token: p.rawFrom(start), zh: "「" + period + "」後面需要時間，例如 凌晨 1 點", en: "a time of day must follow",
				hintZh: "例如：凌晨 1 點到 5 點、下午 2 點到 4 點"}
		}
		p.pos = start
		return span{}, false, nil
	}
	if period == "" && inherit != "" && h <= 12 {
		period = inherit
	}
	hour, ok := to24(h, period)
	if !ok || m < 0 || m > 59 {
		return span{}, false, &Error{Token: p.rawFrom(start), zh: "時間不存在", en: "invalid time of day",
			hintZh: "提示：時間請用 00:00–23:59、1–12 點配合上午/下午，或 1am–12pm",
			hintEn: "hint: use 00:00-23:59, or 1-12 with am/pm"}
	}
	y, mo, d := base.Date()
	at := time.Date(y, mo, d, hour, m, 0, 0, base.Location())
	return span{lo: at, hi: at, instant: true, period: period}, true, nil
}

// to24 converts an hour with an optional meridiem to 0-23.
func to24(h int, period string) (int, bool) {
	switch period {
	case "":
		return h, h >= 0 && h <= 23
	case "am", "凌晨":
		if h < 0 || h > 12 || (period == "am" && h == 0) {
			return 0, false
		}
		return h % 12, true // 12am / 凌晨 12 點 = 00:00
	case "上午":
		return h, h >= 0 && h <= 12
	case "中午": // around noon: 中午 11 點, 12 點, 1 點 (13:00), 2 點 (14:00)
		switch {
		case h == 11 || h == 12:
			return h, true
		case h == 1 || h == 2:
			return h + 12, true
		}
		return 0, false
	case "pm", "下午":
		if h < 1 || h > 12 {
			return 0, false
		}
		if h == 12 {
			return 12, true
		}
		return h + 12, true
	case "晚上": // evening: 6-11 → 18:00-23:00; 晚上 12 點 (midnight of which day?) is rejected
		if h < 6 || h > 11 {
			return 0, false
		}
		return h + 12, true
	}
	return 0, false
}

func (p *parser) validDay(y, m, d int, raw string) (span, bool, error) {
	if m < 1 || m > 12 || d < 1 || d > daysIn(y, time.Month(m)) {
		return span{}, false, &Error{Token: raw, zh: "日期不存在", en: "invalid date",
			hintZh: "提示：月份是 1–12，日期不能超過該月的天數", hintEn: "hint: months are 1-12 and the day must exist in that month"}
	}
	return p.daySpan(time.Date(y, time.Month(m), d, 0, 0, 0, 0, p.q.Now.Location())), true, nil
}

func (p *parser) validMonth(y, m int, raw string) (span, bool, error) {
	if m < 1 || m > 12 {
		return span{}, false, &Error{Token: raw, zh: "月份不存在", en: "invalid month", hintZh: "提示：月份是 1–12", hintEn: "hint: months are 1-12"}
	}
	return p.monthSpan(y, time.Month(m)), true, nil
}

// daysIn is the number of days in month m of year y; time.Date normalizes
// day 0 of the next month to the last day of this one.
func daysIn(y int, m time.Month) int {
	return time.Date(y, m+1, 0, 0, 0, 0, 0, time.UTC).Day()
}

func (p *parser) daySpan(d time.Time) span {
	return span{lo: d, hi: d.AddDate(0, 0, 1), day: true}
}

func (p *parser) monthSpan(y int, m time.Month) span {
	first := time.Date(y, m, 1, 0, 0, 0, 0, p.q.Now.Location())
	return span{lo: first, hi: first.AddDate(0, 1, 0), month: true}
}

func (p *parser) yearSpan(y int) span {
	first := time.Date(y, time.January, 1, 0, 0, 0, 0, p.q.Now.Location())
	return span{lo: first, hi: first.AddDate(1, 0, 0)}
}

func (p *parser) today() time.Time { return midnight(p.q.Now) }

// isYearNumber reports whether t is written as four ASCII digits.
func isYearNumber(t token) bool {
	if t.kind != tNum || len(t.raw) != 4 {
		return false
	}
	for _, r := range t.raw {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// markAt reports whether the token at i is the dictionary word w.
func (p *parser) markAt(i int, w string) bool {
	return i < len(p.toks) && (p.toks[i].kind == tCJK || p.toks[i].kind == tHan) && p.toks[i].text == w
}

func (p *parser) dayMarkAt(i int) bool {
	return i < len(p.toks) && (p.toks[i].kind == tCJK || p.toks[i].kind == tHan) && dayMarks[p.toks[i].text]
}

func (p *parser) hourMarkAt(i int) bool {
	return i < len(p.toks) && p.toks[i].kind == tHan && hourMarks[p.toks[i].text]
}

func (p *parser) wordAt(i int, words ...string) bool {
	if i >= len(p.toks) || p.toks[i].kind != tWord {
		return false
	}
	for _, w := range words {
		if p.toks[i].text == w {
			return true
		}
	}
	return false
}

// unitAt reports whether a size, time or count unit follows at i, which
// makes the number before it a quantity ("2026 files", "10 days").
func (p *parser) unitAt(i int) bool {
	if i >= len(p.toks) {
		return false
	}
	units := append(append(mapKeys(sizeUnits), mapKeys(timeUnits)...), countUnits...)
	_, n := p.matchAt(i, units)
	return n > 0
}

// postfixAhead reports whether a duration postfix ("前", "內", "ago") follows.
func (p *parser) postfixAhead() bool {
	_, n := p.matchAt(p.pos, mapKeys(postfixPhrases))
	return n > 0
}

// rawFrom returns the input tokens from index i up to the current position.
func (p *parser) rawFrom(i int) string {
	var parts []string
	for k := i; k < p.pos && k < len(p.toks); k++ {
		parts = append(parts, p.toks[k].raw)
	}
	return strings.Join(parts, " ")
}

func atoi(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}
