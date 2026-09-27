package query

import (
	"errors"
	"strings"
	"testing"
	"time"
)

// Absolute date comparators at the fixed dtNow (2026-09-28 10:30 +08:00).
//
//	before X        mtime <  X.start
//	on or before X  mtime <  X.end
//	after X         mtime >= X.end
//	on or after X   mtime >= X.start   (= since X)
func TestAbsoluteDateComparators(t *testing.T) {
	sep1, sep2 := ymd(2026, 9, 1), ymd(2026, 9, 2)
	tests := []struct {
		in       string
		from, to time.Time // want from <= mtime < to; none = unbounded
	}{
		// day, Chinese
		{"找 2026-09-01 以前修改的檔案", none, sep1},
		{"2026-09-01 之前", none, sep1},
		{"9 月 1 日以前", none, sep1},
		{"找 9 月 1 日之前的檔案", none, sep1},
		{"2026-09-01 或以前", none, sep2},
		{"2026-09-01 或之前", none, sep2},
		{"2026-09-01 以前含當日", none, sep2},
		{"2026-09-01 之前含當日", none, sep2},
		{"2026-09-01 以後", sep2, none},
		{"2026-09-01 之後", sep2, none},
		{"9 月 1 日以後", sep2, none},
		{"2026-09-01 或以後", sep1, none},
		{"2026-09-01 或之後", sep1, none},
		{"2026-09-01 以後含當日", sep1, none},
		{"2026-09-01 之後含當日", sep1, none},
		{"2026/09/01 以後", sep2, none},
		// day, English
		{"before Sep 1", none, sep1},
		{"before September 1", none, sep1},
		{"before 2026-09-01", none, sep1},
		{"before 9/1", none, sep1},
		{"after Sep 1", sep2, none},
		{"after September 1", sep2, none},
		{"after 2026-09-01", sep2, none},
		{"on or before Sep 1", none, sep2},
		{"on or before 2026-09-01", none, sep2},
		{"on or after Sep 1", sep1, none},
		{"on or after 2026-09-01", sep1, none},
		{"files modified on or after Sep 1", sep1, none},
		{"files modified before Sep 1 2026", none, sep1},
		{"before yesterday", none, ymd(2026, 9, 27)},
		{"after today", ymd(2026, 9, 29), none},
		// month (no year: this year)
		{"找 9 月以前修改的檔案", none, ymd(2026, 9, 1)},
		{"找 9 月以後修改的檔案", ymd(2026, 10, 1), none},
		{"找 9 月或以前修改的檔案", none, ymd(2026, 10, 1)},
		{"找 9 月或以後修改的檔案", ymd(2026, 9, 1), none},
		{"找 2025 年 12 月以後的檔案", ymd(2026, 1, 1), none},
		{"before September", none, ymd(2026, 9, 1)},
		{"after September", ymd(2026, 10, 1), none},
		{"on or before September", none, ymd(2026, 10, 1)},
		{"on or after September", ymd(2026, 9, 1), none},
		{"before September 2025", none, ymd(2025, 9, 1)},
		{"本月以前", none, ymd(2026, 9, 1)},
		// year
		{"找 2025 年以前修改的檔案", none, ymd(2025, 1, 1)},
		{"找 2025 年以後修改的檔案", ymd(2026, 1, 1), none},
		{"2025 年或以前", none, ymd(2026, 1, 1)},
		{"2025 年或以後", ymd(2025, 1, 1), none},
		{"before 2025", none, ymd(2025, 1, 1)},
		{"after 2025", ymd(2026, 1, 1), none},
		{"on or before 2025", none, ymd(2026, 1, 1)},
		{"on or after 2025", ymd(2025, 1, 1), none},
		{"去年以前", none, ymd(2025, 1, 1)},
		// AND of two comparators
		{"找 9 月 1 日以後且 9 月 15 日以前修改的檔案", sep2, ymd(2026, 9, 15)},
		{"找 9 月 1 日或以後且 9 月 15 日或以前修改的檔案", sep1, ymd(2026, 9, 16)},
		{"after Sep 1 and before Sep 15", sep2, ymd(2026, 9, 15)},
		{"找 9 月以後、最近 7 天修改的檔案", ymd(2026, 10, 1), none}, // after is later than now-7d
		// since is on-or-after, not after
		{"since Sep 1", sep1, none},
		{"從 9 月 1 日開始", sep1, none},
		{"9 月 1 日以來", sep1, none},
		// unchanged: "before" with a duration, and bare 前 after a month
		{"before 30 days", none, dtNow.Add(-30 * 24 * time.Hour)},
		{"modified before 3 days", none, dtNow.Add(-3 * 24 * time.Hour)},
		{"older than 30 days", none, dtNow.Add(-30 * 24 * time.Hour)},
		{"3 days ago", none, dtNow.Add(-3 * 24 * time.Hour)},
		{"3 天以前", none, dtNow.Add(-3 * 24 * time.Hour)},
		{"3 月前", none, dtNow.Add(-90 * 24 * time.Hour)},
		{"3 days or older", none, dtNow.Add(-3 * 24 * time.Hour)},
		{"from Sep 1 until now", sep1, dtNow},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			q, err := Parse(tt.in, dtNow)
			if err != nil {
				t.Fatalf("Parse(%q): %v", tt.in, err)
			}
			if !q.ModifiedAfter.Equal(tt.from) || !q.ModifiedBefore.Equal(tt.to) {
				t.Errorf("Parse(%q)\n got  [%s, %s)\n want [%s, %s)", tt.in,
					show(q.ModifiedAfter), show(q.ModifiedBefore), show(tt.from), show(tt.to))
			}
		})
	}
}

// TestAbsoluteComparatorsCombine: comparators are ordinary time conditions,
// so they AND with everything and form time OR groups.
func TestAbsoluteComparatorsCombine(t *testing.T) {
	tests := []struct {
		in   string
		want Query
	}{
		// the first "or" of "on or before" belongs to the phrase; the middle one is Boolean OR
		{"on or before Sep 1 or on or after Sep 20", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{Before: ymd(2026, 9, 2)}, TimeRange{After: ymd(2026, 9, 20)})}}},
		{"before Sep 1 or after Sep 20", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{Before: ymd(2026, 9, 1)}, TimeRange{After: ymd(2026, 9, 21)})}}},
		{"找 9 月 1 日以前或 9 月 20 日以後修改的 log", Query{Extensions: []string{"log"}, AnyOf: []OrGroup{timeGroup(
			TimeRange{Before: ymd(2026, 9, 1)}, TimeRange{After: ymd(2026, 9, 21)})}}},
		{"before September or after October", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{Before: ymd(2026, 9, 1)}, TimeRange{After: ymd(2026, 11, 1)})}}},
		{"9 月 1 日或以前或 9 月 20 日或以後", Query{AnyOf: []OrGroup{timeGroup(
			TimeRange{Before: ymd(2026, 9, 2)}, TimeRange{After: ymd(2026, 9, 20)})}}},
		{"today or before Sep 1", Query{AnyOf: []OrGroup{timeGroup(dayRange(2026, 9, 28), TimeRange{Before: ymd(2026, 9, 1)})}}},
		// filename, content, size, extension
		{"找 2026-09-01 以後修改且檔名包含 config 的 yml", Query{Extensions: []string{"yaml", "yml"},
			Names: []NameCond{nameContains("config")}, ModifiedAfter: ymd(2026, 9, 2)}},
		{"找 9 月以前、檔名包含 backup 的 sql", Query{Extensions: []string{"sql"},
			Names: []NameCond{nameContains("backup")}, ModifiedBefore: ymd(2026, 9, 1)}},
		{"找 2025 年以前、檔名以 .bak 結尾的檔案", Query{Names: []NameCond{nameSuffix(".bak")}, ModifiedBefore: ymd(2025, 1, 1)}},
		{`找 2026-09-01 或以後修改，而且包含 "ERROR" 的 log`, Query{Extensions: []string{"log"}, Contents: []string{"ERROR"},
			ModifiedAfter: ymd(2026, 9, 1)}},
		{"找 2025 年以前且大於 1GB 的 log", Query{Extensions: []string{"log"}, Sizes: []SizeCond{{">", 1 << 30}},
			ModifiedBefore: ymd(2025, 1, 1)}},
		{"找檔名是 nginx.conf 或 httpd.conf，2026-09-01 以後修改", Query{ModifiedAfter: ymd(2026, 9, 2),
			AnyOf: []OrGroup{nameGroup(nameIs("nginx.conf"), nameIs("httpd.conf"))}}},
		// comparator "or" phrases are still not Boolean OR
		{"10MB or more", Query{Sizes: []SizeCond{{">=", 10 << 20}}}},
		{"1GB or smaller", Query{Sizes: []SizeCond{{"<=", 1 << 30}}}},
		{"10MB 或以上", Query{Sizes: []SizeCond{{">=", 10 << 20}}}},
		{"1KB 或以下", Query{Sizes: []SizeCond{{"<=", 1 << 10}}}},
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

func TestAbsoluteComparatorErrors(t *testing.T) {
	tests := []struct{ in, en, zh string }{
		// missing operand
		{"before", `"before" must be followed by a date, month or year`, ""},
		{"after", `"after" must be followed by a date, month or year`, ""},
		{"on or before", `"on or before" must be followed by a date, month or year`, ""},
		{"on or after", `"on or after" must be followed by a date, month or year`, ""},
		{"after foo", `"after" must be followed by a date`, ""},
		{"以前", "", "看不懂這個詞"},
		{"之後", "", "看不懂這個詞"},
		{"或以前", "", "看不懂這個詞"},
		{"或以後", "", "看不懂這個詞"},
		// no times of day (ambiguous: which day?)
		{"before 14:00", "not a time of day", ""},
		{"after 3pm", "not a time of day", ""},
		{"找下午 3 點以前的檔案", "", "不支援時刻"},
		{"找今天 3 點以前的檔案", "", "不支援時刻"},
		{"after 2026-09-01 14:00", "not a time of day", ""},
		{"before now", `"before" must be followed by a date`, ""},
		// invalid dates are still rejected
		{"before 2026-02-29", "invalid date", ""},
		{"after 2026-13-01", "invalid date", ""},
		{"on or before 2026-04-31", "invalid date", ""},
		{"2026-02-29 以前", "", "日期不存在"},
		{"2026-13-01 以後", "", "日期不存在"},
		{"13 月以前", "", "月份不存在"},
		// a comparator cannot take a range
		{"after Sep 1 to Sep 10", "before/after cannot be followed by a range", ""},
		{"找 9 月 1 日以後到 9 月 10 日的檔案", "", "以前 / 以後 不能再接時間範圍"},
		// contradictions are found before searching
		{"找 9 月 20 日以後且 9 月 10 日以前修改的檔案", "", "修改時間條件互相矛盾"},
		{"after 2025 and before 2025", "contradict", ""},
		{"before Sep 1 and after Sep 1", "contradict", ""},
		{"找 9 月 1 日以前且 9 月的檔案", "", "修改時間條件互相矛盾"},
		// OR still needs the same kind and a complete condition
		{"before Sep 1 or larger than 1GB", "OR currently requires conditions of the same type", ""},
		{"before Sep 1 or", `"or" needs a condition after it`, ""},
		{"on or before Sep 1 or or after Sep 20", `"or" is repeated`, ""},
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

// TestAbsoluteComparatorsAcrossDST: bounds are local midnights from the
// calendar, not now or start plus 24 hours.
func TestAbsoluteComparatorsAcrossDST(t *testing.T) {
	ny, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 3, 20, 12, 0, 0, 0, ny)
	mid := func(m time.Month, d int) time.Time { return time.Date(2026, m, d, 0, 0, 0, 0, ny) }
	for _, c := range []struct {
		in       string
		from, to time.Time
	}{
		{"on or before 2026-03-08", none, mid(3, 9)}, // DST starts on Mar 8: that day is 23 hours
		{"after 2026-03-08", mid(3, 9), none},
		{"on or before 2026-11-01", none, mid(11, 2)}, // DST ends on Nov 1: 25 hours
		{"after 2026-11-01", mid(11, 2), none},
		{"on or before March", none, mid(4, 1)},
	} {
		q, err := Parse(c.in, now)
		if err != nil {
			t.Fatalf("Parse(%q): %v", c.in, err)
		}
		if !q.ModifiedAfter.Equal(c.from) || !q.ModifiedBefore.Equal(c.to) {
			t.Errorf("Parse(%q) = [%s, %s), want [%s, %s)", c.in, show(q.ModifiedAfter), show(q.ModifiedBefore), show(c.from), show(c.to))
		}
		for _, b := range []time.Time{q.ModifiedAfter, q.ModifiedBefore} {
			if !b.IsZero() && (b.Hour() != 0 || b.Minute() != 0) {
				t.Errorf("Parse(%q) bound %s is not a local midnight", c.in, b)
			}
		}
	}
	if d := mid(3, 9).Sub(mid(3, 8)); d != 23*time.Hour {
		t.Errorf("Mar 8 2026 in New York is %v long, want 23h (test assumption)", d)
	}
}

func TestExplainShowsAbsoluteBound(t *testing.T) {
	for _, c := range []struct{ in, want string }{
		{"找 2026-09-01 以前修改的 yml", "修改時間       早於 2026-09-01 00:00:00 (mtime < 2026-09-01 00:00:00)"},
		{"after Sep 1", "modified:      after 2026-09-02 00:00:00 (mtime >= 2026-09-02 00:00:00)"},
		{"on or after Sep 1", "modified:      after 2026-09-01 00:00:00 (mtime >= 2026-09-01 00:00:00)"},
		{"on or before Sep 1", "modified:      before 2026-09-02 00:00:00 (mtime < 2026-09-02 00:00:00)"},
		{"找 9 月 1 日或以後且 9 月 15 日或以前修改的檔案", "修改時間       2026-09-01 00:00:00 <= mtime < 2026-09-16 00:00:00"},
	} {
		q, err := Parse(c.in, dtNow)
		if err != nil {
			t.Fatal(err)
		}
		if out := Explain(q, MacOS); !strings.Contains(out, c.want) {
			t.Errorf("Explain(%q) missing %q:\n%s", c.in, c.want, out)
		}
	}
}
