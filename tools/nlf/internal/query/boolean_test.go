package query

import (
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

// Boolean OR tests run at the fixed dtNow (2026-09-28 10:30 +08:00) from
// datetime_test.go.

func timeGroup(rs ...TimeRange) OrGroup { return OrGroup{Kind: KindTime, Times: rs} }
func dayRange(y int, m time.Month, d int) TimeRange {
	return TimeRange{ymd(y, m, d), ymd(y, m, d).AddDate(0, 0, 1)}
}
func monthRange(y int, m time.Month) TimeRange {
	return TimeRange{ymd(y, m, 1), ymd(y, m, 1).AddDate(0, 1, 0)}
}
func contentGroup(terms ...string) OrGroup { return OrGroup{Kind: KindContent, Contents: terms} }
func sizeGroup(cs ...SizeCond) OrGroup     { return OrGroup{Kind: KindSize, Sizes: cs} }

func TestParseOr(t *testing.T) {
	tests := []struct {
		in   string
		want Query
	}{
		// extension OR is the existing any-of Extensions list
		{"找 go 或 md 檔", Query{Extensions: []string{"go", "md"}}},
		{"go or md files", Query{Extensions: []string{"go", "md"}}},
		{"找 go 或 yml 或 json 檔", Query{Extensions: []string{"go", "yaml", "yml", "json"}}},
		{"go 或者 md 檔", Query{Extensions: []string{"go", "md"}}},
		{"go 檔或 md 檔", Query{Extensions: []string{"go", "md"}}},

		// content OR
		{`找包含 "ERROR" 或 "FATAL" 的 log`, Query{Extensions: []string{"log"}, AnyOf: []OrGroup{contentGroup("ERROR", "FATAL")}}},
		{`找內容包含 "timeout" 或 "connection refused" 的檔案`, Query{AnyOf: []OrGroup{contentGroup("timeout", "connection refused")}}},
		{`log files containing "ERROR" or "panic"`, Query{Extensions: []string{"log"}, AnyOf: []OrGroup{contentGroup("ERROR", "panic")}}},
		{`containing "foo" or "bar"`, Query{AnyOf: []OrGroup{contentGroup("foo", "bar")}}},
		{`包含 "ERROR" 或 "FATAL" 或 "panic"`, Query{AnyOf: []OrGroup{contentGroup("ERROR", "FATAL", "panic")}}},
		{`包含 "ERROR" 或包含 "FATAL"`, Query{AnyOf: []OrGroup{contentGroup("ERROR", "FATAL")}}},
		{"包含 ERROR 或 FATAL 的 log", Query{Extensions: []string{"log"}, AnyOf: []OrGroup{contentGroup("ERROR", "FATAL")}}},
		// AND between contents is unchanged
		{`包含 "A" 且包含 "B"`, Query{Contents: []string{"A", "B"}}},
		{`包含 "A" 和 "B"`, Query{Contents: []string{"A", "B"}}},
		{`containing "A" and "B"`, Query{Contents: []string{"A", "B"}}},
		{`包含 "A" 或 "B" 且包含 "C"`, Query{Contents: []string{"C"}, AnyOf: []OrGroup{contentGroup("A", "B")}}},

		// date/time OR: a union of ranges, never merged into one range
		{"找今天或昨天修改的檔案", Query{AnyOf: []OrGroup{timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27))}}},
		{"files modified today or yesterday", Query{AnyOf: []OrGroup{timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27))}}},
		{"找 8 月或 9 月修改的 yml 檔", Query{Extensions: []string{"yaml", "yml"},
			AnyOf: []OrGroup{timeGroup(monthRange(2026, 8), monthRange(2026, 9))}}},
		{"files modified in August or September", Query{AnyOf: []OrGroup{timeGroup(monthRange(2026, 8), monthRange(2026, 9))}}},
		{"8 月或 9 月或 10 月", Query{AnyOf: []OrGroup{timeGroup(monthRange(2026, 8), monthRange(2026, 9), monthRange(2026, 10))}}},
		{"找 1 月或 9 月的檔案", Query{AnyOf: []OrGroup{timeGroup(monthRange(2026, 1), monthRange(2026, 9))}}},
		{"找本月或上個月修改的 conf", Query{Extensions: []string{"conf"},
			AnyOf: []OrGroup{timeGroup(monthRange(2026, 9), monthRange(2026, 8))}}},
		{"找 2026-09-01 或 2026-09-15 修改的 log", Query{Extensions: []string{"log"},
			AnyOf: []OrGroup{timeGroup(dayRange(2026, 9, 1), dayRange(2026, 9, 15))}}},
		{"找今天 01:00 到 03:00 或 05:00 到 06:00 修改的檔案", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{at(2026, 9, 28, 1, 0), at(2026, 9, 28, 3, 0)}, TimeRange{at(2026, 9, 28, 5, 0), at(2026, 9, 28, 6, 0)})}}},
		// a clock-only alternative stays on the day of the one before it
		{"找昨天 1 點到 3 點或 5 點到 6 點修改的檔案", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{at(2026, 9, 27, 1, 0), at(2026, 9, 27, 3, 0)}, TimeRange{at(2026, 9, 27, 5, 0), at(2026, 9, 27, 6, 0)})}}},
		{"最近 1 小時或昨天", Query{AnyOf: []OrGroup{timeGroup(TimeRange{After: dtNow.Add(-time.Hour)}, dayRange(2026, 9, 27))}}},

		// size OR
		{"找大於 1GB 或小於 1KB 的檔案", Query{AnyOf: []OrGroup{sizeGroup(SizeCond{">", 1 << 30}, SizeCond{"<", 1 << 10})}}},
		{"files larger than 1GB or smaller than 1KB", Query{AnyOf: []OrGroup{sizeGroup(SizeCond{">", 1 << 30}, SizeCond{"<", 1 << 10})}}},

		// several groups, ANDed with each other and with the other conditions
		{`找今天或昨天修改，而且包含 "ERROR" 或 "FATAL" 的 log`, Query{Extensions: []string{"log"}, AnyOf: []OrGroup{
			timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27)), contentGroup("ERROR", "FATAL")}}},
		{"找 8 月或 9 月修改，大於 10MB 的 log", Query{Extensions: []string{"log"}, Sizes: []SizeCond{{">", 10 << 20}},
			AnyOf: []OrGroup{timeGroup(monthRange(2026, 8), monthRange(2026, 9))}}},
		{`find log files containing "ERROR" or "panic" modified today or yesterday`, Query{Extensions: []string{"log"},
			AnyOf: []OrGroup{contentGroup("ERROR", "panic"), timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27))}}},
		// the group only takes its neighbours; the rest stays AND
		{"找今天或昨天修改的 log", Query{Extensions: []string{"log"}, AnyOf: []OrGroup{timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27))}}},
		{"找今天或昨天修改，9 月的檔案", Query{ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 10, 1),
			AnyOf: []OrGroup{timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27))}}},
		{`找大於 1GB 或小於 1KB 的 log，排除 vendor，最多 10 個`, Query{Extensions: []string{"log"}, ExcludeDirs: []string{"vendor"}, Limit: 10,
			AnyOf: []OrGroup{sizeGroup(SizeCond{">", 1 << 30}, SizeCond{"<", 1 << 10})}}},

		// "or" inside comparator phrases is not a boolean OR
		{"10MB or more", Query{Sizes: []SizeCond{{">=", 10 << 20}}}},
		{"1GB or smaller", Query{Sizes: []SizeCond{{"<=", 1 << 30}}}},
		{"500 bytes or less", Query{Sizes: []SizeCond{{"<=", 500}}}},
		{"3 days or older", Query{ModifiedBefore: dtNow.Add(-3 * 24 * time.Hour)}},
		{"10MB 或以上", Query{Sizes: []SizeCond{{">=", 10 << 20}}}},
		{"1KB 或以下", Query{Sizes: []SizeCond{{"<=", 1 << 10}}}},
		// "or" in exclude lists still lists directories
		{"exclude vendor or build", Query{ExcludeDirs: []string{"vendor", "build"}}},
		{"exclude vendor and node_modules", Query{ExcludeDirs: []string{"vendor", "node_modules"}}},
		{"files changed between Sep 1 and Sep 15", Query{ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 9, 16)}},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			got, err := Parse(tt.in, dtNow)
			if err != nil {
				t.Fatalf("Parse(%q): %v", tt.in, err)
			}
			tt.want.Lang, tt.want.Now = DetectLang(tt.in), dtNow
			if !sameQuery(got, &tt.want) {
				t.Errorf("Parse(%q)\n got  %+v\n want %+v", tt.in, *got, tt.want)
			}
		})
	}
}

// sameQuery compares queries, treating equal instants in any location as equal.
func sameQuery(a, b *Query) bool {
	if !a.ModifiedAfter.Equal(b.ModifiedAfter) || !a.ModifiedBefore.Equal(b.ModifiedBefore) || len(a.AnyOf) != len(b.AnyOf) {
		return false
	}
	for i := range a.AnyOf {
		ga, gb := a.AnyOf[i], b.AnyOf[i]
		if ga.Kind != gb.Kind || !reflect.DeepEqual(ga.Contents, gb.Contents) || !reflect.DeepEqual(ga.Sizes, gb.Sizes) || len(ga.Times) != len(gb.Times) {
			return false
		}
		for j := range ga.Times {
			if !ga.Times[j].After.Equal(gb.Times[j].After) || !ga.Times[j].Before.Equal(gb.Times[j].Before) {
				return false
			}
		}
	}
	ca, cb := *a, *b
	ca.ModifiedAfter, ca.ModifiedBefore, ca.AnyOf = time.Time{}, time.Time{}, nil
	cb.ModifiedAfter, cb.ModifiedBefore, cb.AnyOf = time.Time{}, time.Time{}, nil
	return reflect.DeepEqual(ca, cb)
}

func TestParseOrErrors(t *testing.T) {
	tests := []struct{ in, en, zh string }{
		// different kinds
		{"找大於 1GB 或最近 1 小時修改的檔案", "", "目前 OR 只能連接同類條件"},
		{`找包含 "ERROR" 或 9 月修改的 log`, "", "目前 OR 只能連接同類條件"},
		{"pdf 或大於 10MB", "", "目前 OR 只能連接同類條件"},
		{"大於 1GB 或今天", "", "目前 OR 只能連接同類條件"},
		{"go files or larger than 1k", "OR currently requires conditions of the same type", ""},
		{`containing "ERROR" or modified today`, "OR currently requires conditions of the same type", ""},
		{"larger than 1mb or modified today", "OR currently requires conditions of the same type", ""},
		// conditions OR cannot join
		{"排除 vendor 或 今天", "", "目前 OR 只能連接同類條件"},
		{"最大的 5 個 或 最新的 3 個", "", "目前 OR 只能連接同類條件"},
		{"top 5 or top 10", "OR currently requires conditions of the same type", ""},
		{`包含 "A" 不分大小寫 或 "B"`, "", "目前 OR 只能連接同類條件"},
		// incomplete
		{"今天 或", "", "「或」後面需要條件"},
		{"files modified today or", `"or" needs a condition after it`, ""},
		{"或 今天", "", "「或」前面需要條件"},
		{"or today", `"or" needs a condition before it`, ""},
		{"找 或 今天", "", "「或」前面需要條件"},
		{"今天 或 或 昨天", "", "「或」重複了"},
		{"today or or yesterday", `"or" is repeated`, ""},
		{"包含 ERROR 或 404", "", "數字需要單位"}, // a bare number is not taken as search text: quote it
		// an OR group that cannot overlap the other time conditions
		{"找今天或昨天修改，8 月的檔案", "", "修改時間條件互相矛盾"},
		// the old rules still apply inside an alternative
		{"找今天或 9/20 到 9/10 的檔案", "", "開始時間必須早於結束時間"},
		{`包含 "ERROR" 或 ""`, "search text must not be empty", ""},
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

func TestExplainShowsOr(t *testing.T) {
	q, err := Parse(`找今天或昨天修改，而且包含 "ERROR" 或 "FATAL"、大於 1GB 或小於 1KB 的 log`, dtNow)
	if err != nil {
		t.Fatal(err)
	}
	out := Explain(q, MacOS)
	for _, want := range []string{
		`內容包含       "ERROR" 或 "FATAL" (符合任一個即可，區分大小寫)`,
		"修改時間       任一區間（OR），符合任一個即可：",
		"- 2026-09-28 00:00:00 <= mtime < 2026-09-29 00:00:00",
		"- 2026-09-27 00:00:00 <= mtime < 2026-09-28 00:00:00",
		"檔案大小       > 1 GB 或 < 1 KB (符合任一個即可)",
		`-exec grep -qIF -e ERROR -e FATAL {} \;`,
		`\( -size +1073741824c -o -size -1024c \)`,
		`\( \( -newermt '2026-09-27 23:59:59' ! -newermt '2026-09-28 23:59:59' \) -o \( -newermt '2026-09-26 23:59:59' ! -newermt '2026-09-27 23:59:59' \) \)`,
	} {
		if !strings.Contains(out, want) {
			t.Errorf("Explain missing %q:\n%s", want, out)
		}
	}
	// An OR group must never look like two AND rows.
	if strings.Count(out, "內容包含") != 1 || strings.Count(out, "檔案大小") != 1 {
		t.Errorf("OR group shown as separate rows:\n%s", out)
	}
	q, _ = Parse(`log files containing "ERROR" or "panic" modified today or yesterday`, dtNow)
	out = Explain(q, Linux)
	for _, want := range []string{`content:       "ERROR" OR "panic" (any one matches, case-sensitive)`, "modified:      any of (OR), any one matches:"} {
		if !strings.Contains(out, want) {
			t.Errorf("Explain missing %q:\n%s", want, out)
		}
	}
}

func TestTimeRangeMatch(t *testing.T) {
	r := dayRange(2026, 9, 28)
	for _, c := range []struct {
		t    time.Time
		want bool
	}{
		{ymd(2026, 9, 28), true}, {ymd(2026, 9, 29).Add(-time.Nanosecond), true},
		{ymd(2026, 9, 29), false}, {ymd(2026, 9, 28).Add(-time.Nanosecond), false},
	} {
		if r.Match(c.t) != c.want {
			t.Errorf("Match(%s) = %v", c.t, !c.want)
		}
	}
	open := TimeRange{After: ymd(2026, 9, 1)}
	if !open.Match(ymd(2030, 1, 1)) || open.Match(ymd(2026, 8, 31)) {
		t.Error("open-ended range")
	}
}

// TestOrQAHardening covers the QA findings on Boolean OR: AND/OR connector
// collisions, contradictions between several time OR groups, and clock-only
// alternatives after a range that is not within one day.
func TestOrQAHardening(t *testing.T) {
	errs := []struct{ in, en, zh string }{
		// an AND connector next to an OR word is never skipped silently
		{"today or and yesterday", "OR and AND cannot be used together without a condition between them", ""},
		{"today and or yesterday", "OR and AND cannot be used together without a condition between them", ""},
		{"today or or yesterday", `"or" is repeated`, ""},
		{"今天 或 且 昨天", "", "OR 和 AND 不能連續使用"},
		{"今天 且 或 昨天", "", "OR 和 AND 不能連續使用"},
		{"今天 或 或 昨天", "", "「或」重複了"},
		{"今天 或 而且 昨天", "", "OR 和 AND 不能連續使用"},
		{"今天 並且 或 昨天", "", "OR 和 AND 不能連續使用"},
		{"今天和或昨天", "", "OR 和 AND 不能連續使用"},
		{`包含 "A" 且 或 "B"`, "", "OR 和 AND 不能連續使用"},
		{"larger than 1GB and or smaller than 1KB", "OR and AND cannot be used together", ""},

		// several time OR groups must still have a common instant
		{"找今天或昨天修改，而且 7 月或 8 月修改的檔案", "", "修改時間條件互相矛盾"},
		{"files modified today or yesterday and in July or August", "modification time conditions contradict each other", ""},
		{"找 9/1 或 9/15 修改，而且 8 月或 10 月修改的檔案", "", "修改時間條件互相矛盾"},
		{"找 1 月或 9 月修改，而且 3 月或 4 月修改的檔案", "", "修改時間條件互相矛盾"}, // Jan..Sep is not one range
		{"找今天或昨天修改，而且 9 月或 8 月、7 月的檔案", "", "修改時間條件互相矛盾"},   // plus a global bound

		// a clock-only alternative needs a day to take
		{"找昨天 23:00 到今天 02:00 或 05:00 到 06:00 修改的檔案", "", "前一個時間區間跨日，下一個 OR 時間區間需要明確指定日期"},
		{"yesterday 23:00 to today 02:00 or 05:00 to 06:00", "the next OR time range needs an explicit date", ""},
		{"找 8 月或 5 點到 6 點修改的檔案", "", "下一個 OR 時間區間需要明確指定日期"},
		{"找最近 6 小時或 1 點到 2 點修改的檔案", "", "下一個 OR 時間區間需要明確指定日期"},
	}
	for _, tt := range errs {
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

	valid := []struct {
		in   string
		want Query
	}{
		{"today or yesterday", Query{AnyOf: []OrGroup{timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27))}}},
		{"今天或昨天", Query{AnyOf: []OrGroup{timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27))}}},
		{"today or modified yesterday", Query{AnyOf: []OrGroup{timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27))}}},
		// both groups keep their alternatives; both overlap in September
		{"找今天或昨天修改，而且 9 月或 8 月修改的檔案", Query{AnyOf: []OrGroup{
			timeGroup(dayRange(2026, 9, 28), dayRange(2026, 9, 27)), timeGroup(monthRange(2026, 9), monthRange(2026, 8))}}},
		{"找 9/1 或 9/15 修改，而且 9 月或 10 月修改的檔案", Query{AnyOf: []OrGroup{
			timeGroup(dayRange(2026, 9, 1), dayRange(2026, 9, 15)), timeGroup(monthRange(2026, 9), monthRange(2026, 10))}}},
		{"找 1 月或 9 月修改，而且 9 月的檔案", Query{ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 10, 1),
			AnyOf: []OrGroup{timeGroup(monthRange(2026, 1), monthRange(2026, 9))}}},
		// clock-only alternatives: same day, or an explicit date after a cross-midnight range
		{"找昨天 1 點到 3 點或 5 點到 6 點修改的檔案", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{at(2026, 9, 27, 1, 0), at(2026, 9, 27, 3, 0)}, TimeRange{at(2026, 9, 27, 5, 0), at(2026, 9, 27, 6, 0)})}}},
		{"找昨天 1:00 到 3:00 或 5:00 到 6:00 修改的檔案", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{at(2026, 9, 27, 1, 0), at(2026, 9, 27, 3, 0)}, TimeRange{at(2026, 9, 27, 5, 0), at(2026, 9, 27, 6, 0)})}}},
		{"找昨天 23:00 到今天 02:00 或 今天 05:00 到 06:00 修改的檔案", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{at(2026, 9, 27, 23, 0), at(2026, 9, 28, 2, 0)}, TimeRange{at(2026, 9, 28, 5, 0), at(2026, 9, 28, 6, 0)})}}},
		{"找昨天 23:00 到今天 02:00 或 昨天 20:00 到 21:00 修改的檔案", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{at(2026, 9, 27, 23, 0), at(2026, 9, 28, 2, 0)}, TimeRange{at(2026, 9, 27, 20, 0), at(2026, 9, 27, 21, 0)})}}},
		{"找今天或 5 點到 6 點修改的檔案", Query{AnyOf: []OrGroup{timeGroup( // today is one day: 5-6 is today
			dayRange(2026, 9, 28), TimeRange{at(2026, 9, 28, 5, 0), at(2026, 9, 28, 6, 0)})}}},
		// AND words and "or" inside phrases are untouched
		{`包含 "A" 且包含 "B"`, Query{Contents: []string{"A", "B"}}},
		{`包含 "A" 或 "B"，而且包含 "C"`, Query{Contents: []string{"C"}, AnyOf: []OrGroup{contentGroup("A", "B")}}},
		{"exclude vendor and build", Query{ExcludeDirs: []string{"vendor", "build"}}},
		{"exclude vendor or build", Query{ExcludeDirs: []string{"vendor", "build"}}},
		{"go and md files", Query{Extensions: []string{"go", "md"}}},
		{"10MB or more", Query{Sizes: []SizeCond{{">=", 10 << 20}}}},
		{"10MB 或以上", Query{Sizes: []SizeCond{{">=", 10 << 20}}}},
		{"files between Sep 1 and Sep 15", Query{ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 9, 16)}},
	}
	for _, tt := range valid {
		t.Run(tt.in, func(t *testing.T) {
			got, err := Parse(tt.in, dtNow)
			if err != nil {
				t.Fatalf("Parse(%q): %v", tt.in, err)
			}
			tt.want.Lang, tt.want.Now = DetectLang(tt.in), dtNow
			if !sameQuery(got, &tt.want) {
				t.Errorf("Parse(%q)\n got  %+v\n want %+v", tt.in, *got, tt.want)
			}
		})
	}

	// AND between two days is a valid connector: it fails only as a
	// contradiction, never as a connector error.
	for _, in := range []string{"today and yesterday", "今天且昨天"} {
		_, err := Parse(in, dtNow)
		var pe *Error
		if !errors.As(err, &pe) || !strings.Contains(pe.Message(English), "contradict") ||
			strings.Contains(pe.Message(English), "cannot be used together") {
			t.Errorf("Parse(%q) = %v; want the time contradiction error, not a connector error", in, err)
		}
	}
}

func TestTimeSatisfiable(t *testing.T) {
	jan, sep, oct := monthRange(2026, 1), monthRange(2026, 9), monthRange(2026, 10)
	for _, c := range []struct {
		name string
		q    Query
		want bool
	}{
		{"no groups", Query{}, true},
		{"one group", Query{AnyOf: []OrGroup{timeGroup(jan, sep)}}, true},
		{"groups overlap in one pair", Query{AnyOf: []OrGroup{timeGroup(jan, sep), timeGroup(oct, sep)}}, true},
		{"no pair overlaps", Query{AnyOf: []OrGroup{timeGroup(jan, sep), timeGroup(oct, monthRange(2026, 3))}}, false},
		{"union is not flattened", Query{AnyOf: []OrGroup{timeGroup(jan, sep), timeGroup(monthRange(2026, 5))}}, false},
		{"global bound excludes all", Query{ModifiedAfter: ymd(2026, 10, 1), AnyOf: []OrGroup{timeGroup(jan, sep)}}, false},
		{"global bound keeps one", Query{ModifiedBefore: ymd(2026, 2, 1), AnyOf: []OrGroup{timeGroup(jan, sep)}}, true},
		{"three groups", Query{AnyOf: []OrGroup{timeGroup(jan, sep), timeGroup(sep, oct), timeGroup(dayRange(2026, 9, 15))}}, true},
		{"open ranges", Query{AnyOf: []OrGroup{timeGroup(TimeRange{After: ymd(2026, 9, 1)}), timeGroup(TimeRange{Before: ymd(2026, 9, 2)})}}, true},
		{"open ranges apart", Query{AnyOf: []OrGroup{timeGroup(TimeRange{After: ymd(2026, 9, 2)}), timeGroup(TimeRange{Before: ymd(2026, 9, 2)})}}, false},
		{"size groups ignored", Query{AnyOf: []OrGroup{sizeGroup(SizeCond{">", 1})}}, true},
	} {
		if got := timeSatisfiable(&c.q); got != c.want {
			t.Errorf("%s: timeSatisfiable = %v, want %v", c.name, got, c.want)
		}
	}
}
