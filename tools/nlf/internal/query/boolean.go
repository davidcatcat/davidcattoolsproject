package query

import "time"

// Boolean OR.
//
// A query is a sequence of conditions combined with AND. "A 或 B" / "A or B"
// joins the condition just before the OR word with the one just after it
// into an OR group, and further "或 C" extend that group. A group is still
// ANDed with every other condition, so
//
//	找今天或昨天修改，而且包含 "ERROR" 或 "FATAL" 的 log
//
// is (today OR yesterday) AND ("ERROR" OR "FATAL") AND extension .log.
// There is no precedence to guess: OR only ever binds the two neighbouring
// conditions. Both sides must be of the same kind (extension, content, time
// or size); anything else is rejected rather than reinterpreted.
//
// Words such as "or more", "or smaller", "or older" are comparator phrases
// consumed inside a condition (longest match), and "go 或 md 檔" is an
// extension list, so neither ever reaches the OR handling here.

// item is one parsed condition, or an OR group of conditions of one kind.
type item struct {
	alts []*Query // the condition, or the alternatives of an OR group
	kind CondKind // KindNone for a single condition
	from int      // token index where the item starts, for error messages
}

// parseAll parses the whole input into p.q.
func (p *parser) parseAll() error {
	var items []*item
	for p.pos < len(p.toks) {
		if _, n := p.matchAt(p.pos, orPhrases); n > 0 {
			orAt := p.pos
			p.pos += n
			if len(items) == 0 {
				return &Error{Token: p.toks[orAt].raw, zh: "「或」前面需要條件", en: `"or" needs a condition before it`,
					hintZh: "例如：今天或昨天、包含 \"ERROR\" 或 \"FATAL\"", hintEn: `example: today or yesterday, containing "ERROR" or "FATAL"`}
			}
			if err := p.alternative(items[len(items)-1], orAt); err != nil {
				return err
			}
			continue
		}
		from := p.pos
		d, err := p.parseInto(p.step)
		if err != nil {
			return err
		}
		if !d.empty() {
			items = append(items, &item{alts: []*Query{d}, from: from})
		}
	}
	for _, it := range items {
		if err := p.merge(it); err != nil {
			return err
		}
	}
	return nil
}

// alternative parses the condition after an OR word and adds it to left.
func (p *parser) alternative(left *item, orAt int) error {
	orWord := p.toks[orAt].raw
	lk := left.kind
	if lk == KindNone {
		var ok bool
		if lk, ok = classify(left.alts[0]); !ok {
			return errOrKind(p.rawFrom(left.from))
		}
	}
	// "昨天 1 點到 3 點或 5 點到 6 點": a clock-only alternative stays on the
	// same day as the one before it.
	if lk == KindTime {
		p.clockBase = sameDay(left.alts[len(left.alts)-1])
		defer func() { p.clockBase = time.Time{} }()
	}
	for {
		t, ok := p.cur()
		if !ok {
			return &Error{Token: orWord, zh: "「或」後面需要條件", en: `"or" needs a condition after it`,
				hintZh: "例如：今天或昨天", hintEn: "example: today or yesterday"}
		}
		if _, n := p.matchAt(p.pos, orPhrases); n > 0 {
			return &Error{Token: p.rawFrom(orAt), zh: "「或」重複了", en: `"or" is repeated`}
		}
		parse := p.step
		if lk == KindContent && (t.kind == tHan || isFreeWord(t)) {
			parse = p.contentTerm // `包含 ERROR 或 FATAL`: a bare word is another search text
		}
		d, err := p.parseInto(parse)
		if err != nil {
			return err
		}
		if d.empty() {
			continue // fillers and punctuation between the OR word and the condition
		}
		if rk, ok := classify(d); !ok || rk != lk {
			return errOrKind(p.rawFrom(left.from))
		}
		left.alts = append(left.alts, d)
		left.kind = lk
		return nil
	}
}

func errOrKind(raw string) *Error {
	return &Error{Token: raw, zh: "目前 OR 只能連接同類條件", en: "OR currently requires conditions of the same type",
		hintZh: "提示：「或」可以連接副檔名、內容、修改時間或檔案大小，但兩邊必須同一類，例如 今天或昨天、包含 \"ERROR\" 或 \"FATAL\"、大於 1GB 或小於 1KB；不同類的條件請分成兩次查詢。",
		hintEn: `hint: "or" joins extensions, contents, modification times or sizes of the same kind, e.g. today or yesterday, containing "ERROR" or "FATAL", larger than 1GB or smaller than 1KB; run separate queries for different kinds.`}
}

// parseInto runs parse on a fresh Query and returns what it produced, so a
// condition can be inspected before it is merged.
func (p *parser) parseInto(parse func() error) (*Query, error) {
	saved := p.q
	d := &Query{Lang: saved.Lang, Now: saved.Now}
	p.q = d
	err := parse()
	p.q = saved
	return d, err
}

// empty reports whether d holds no condition (fillers, punctuation).
func (d *Query) empty() bool {
	return d.Root == "" && len(d.Extensions) == 0 && len(d.Contents) == 0 && !d.IgnoreCase &&
		len(d.ExcludeDirs) == 0 && len(d.Sizes) == 0 && d.ModifiedAfter.IsZero() && d.ModifiedBefore.IsZero() &&
		d.Sort == SortNone && d.Limit == 0 && len(d.AnyOf) == 0
}

// classify returns the kind of a single condition, and false when it is not
// exactly one condition that OR can join.
func classify(d *Query) (CondKind, bool) {
	if d.Root != "" || len(d.ExcludeDirs) > 0 || d.Sort != SortNone || d.Limit != 0 || d.IgnoreCase || len(d.AnyOf) > 0 {
		return KindNone, false
	}
	n, k := 0, KindNone
	if len(d.Extensions) > 0 {
		n, k = n+1, KindExtension
	}
	if len(d.Contents) > 0 {
		n, k = n+1, KindContent
	}
	if len(d.Sizes) > 0 {
		n, k = n+1, KindSize
	}
	if !d.ModifiedAfter.IsZero() || !d.ModifiedBefore.IsZero() {
		n, k = n+1, KindTime
	}
	return k, n == 1
}

// sameDay is the midnight of d's time range when it lies within one day.
func sameDay(d *Query) time.Time {
	if d.ModifiedAfter.IsZero() || d.ModifiedBefore.IsZero() {
		return time.Time{}
	}
	day := midnight(d.ModifiedAfter)
	if d.ModifiedBefore.After(day.AddDate(0, 0, 1)) {
		return time.Time{}
	}
	return day
}

// merge adds a condition or an OR group to p.q.
func (p *parser) merge(it *item) error {
	q := p.q
	if it.kind == KindNone {
		d := it.alts[0]
		if d.Root != "" {
			if q.Root != "" {
				return &Error{Token: d.Root, zh: "搜尋位置只能指定一個", en: "only one search location can be given"}
			}
			q.Root = d.Root
		}
		q.Extensions = append(q.Extensions, d.Extensions...)
		q.Contents = append(q.Contents, d.Contents...)
		q.ExcludeDirs = append(q.ExcludeDirs, d.ExcludeDirs...)
		q.Sizes = append(q.Sizes, d.Sizes...)
		q.IgnoreCase = q.IgnoreCase || d.IgnoreCase
		if !d.ModifiedAfter.IsZero() {
			p.after(d.ModifiedAfter)
		}
		if !d.ModifiedBefore.IsZero() {
			p.before(d.ModifiedBefore)
		}
		if d.Sort != SortNone {
			q.Sort = d.Sort
		}
		if d.Limit != 0 {
			q.Limit = d.Limit
		}
		return nil
	}
	g := OrGroup{Kind: it.kind}
	for _, d := range it.alts {
		switch it.kind {
		case KindExtension:
			q.Extensions = append(q.Extensions, d.Extensions...) // already any-of
		case KindContent:
			g.Contents = append(g.Contents, d.Contents...)
		case KindSize:
			g.Sizes = append(g.Sizes, d.Sizes...)
		case KindTime:
			g.Times = append(g.Times, TimeRange{After: d.ModifiedAfter, Before: d.ModifiedBefore})
		}
	}
	if it.kind != KindExtension {
		g.Contents = dedupe(g.Contents)
		q.AnyOf = append(q.AnyOf, g)
	}
	return nil
}

// overlaps reports whether r can hold any time within [after, before).
func (r TimeRange) overlaps(after, before time.Time) bool {
	lo, hi := r.After, r.Before
	if !after.IsZero() && (lo.IsZero() || after.After(lo)) {
		lo = after
	}
	if !before.IsZero() && (hi.IsZero() || before.Before(hi)) {
		hi = before
	}
	return lo.IsZero() || hi.IsZero() || lo.Before(hi)
}
