package query

import (
	"errors"
	"strings"
	"testing"
	"time"
	_ "time/tzdata" // DST tests must not depend on the host's zoneinfo
)

// Date tests run at a fixed local time in a non-UTC zone, so a wrong
// default zone shows up as a failure.
var (
	dtLoc = time.FixedZone("CST", 8*3600)
	dtNow = time.Date(2026, 9, 28, 10, 30, 0, 0, dtLoc) // a Monday
)

func at(y int, m time.Month, d, h, min int) time.Time { return time.Date(y, m, d, h, min, 0, 0, dtLoc) }
func ymd(y int, m time.Month, d int) time.Time        { return at(y, m, d, 0, 0) }

var none time.Time // an unbounded side of a range

func TestDateRanges(t *testing.T) {
	tests := []struct {
		in       string
		from, to time.Time // want from <= mtime < to
	}{
		// year
		{"找 2026 年修改的檔案", ymd(2026, 1, 1), ymd(2027, 1, 1)},
		{"找 2025 年的 pdf 檔", ymd(2025, 1, 1), ymd(2026, 1, 1)},
		{"files modified in 2026", ymd(2026, 1, 1), ymd(2027, 1, 1)},
		{"pdf files from 2025", ymd(2025, 1, 1), ymd(2026, 1, 1)},
		{"yml files modified during 2026", ymd(2026, 1, 1), ymd(2027, 1, 1)},

		// year + month
		{"找 2026 年 9 月的 pdf 檔", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"找 2026年9月修改的 yml 檔", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"yml files from 2026-09", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"yml files from 2026/09", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"pdf files modified in September 2026", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"找 2026 年 12 月的檔案", ymd(2026, 12, 1), ymd(2027, 1, 1)},
		{"找 2026 年 02 月的檔案", ymd(2026, 2, 1), ymd(2026, 3, 1)},
		// month without a year: the year of now
		{"找 9 月修改的 log 檔", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"找九月修改的 log 檔", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"找十二月的檔案", ymd(2026, 12, 1), ymd(2027, 1, 1)},
		{"找一月的檔案", ymd(2026, 1, 1), ymd(2026, 2, 1)},
		{"files modified in September", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"files modified in Jan", ymd(2026, 1, 1), ymd(2026, 2, 1)},
		{"files modified in may", ymd(2026, 5, 1), ymd(2026, 6, 1)},
		{"找今年 9 月的 yaml 檔", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"找去年 9 月的 yaml 檔", ymd(2025, 9, 1), ymd(2025, 10, 1)},
		{"yaml files from September this year", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"yaml files from September last year", ymd(2025, 9, 1), ymd(2025, 10, 1)},

		// a single day
		{"找 2026-09-28 修改的 yml 檔", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"找 2026/09/28 修改的檔案", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"找 2026-9-8 修改的檔案", ymd(2026, 9, 8), ymd(2026, 9, 9)},
		{"查詢 2026 年 9 月 28 日改了什麼檔案", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"找 2026 年 9 月 28 號的檔案", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"找 9 月 28 日改過的 conf 檔", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"找 9/28 的檔案", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"找 03/04 的檔案", ymd(2026, 3, 4), ymd(2026, 3, 5)}, // MM/DD: always month/day
		{"files modified on 2026-09-28", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"files changed on Sep 28 2026", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"files changed on Sep 28, 2026", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"files modified September 28", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"找 2024-02-29 的檔案", ymd(2024, 2, 29), ymd(2024, 3, 1)}, // leap day

		// calendar shortcuts
		{"找今天修改的 yaml", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"找今日修改的 yaml", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"找昨天修改的 log", ymd(2026, 9, 27), ymd(2026, 9, 28)},
		{"找前天修改的 log", ymd(2026, 9, 26), ymd(2026, 9, 27)},
		{"files changed today", ymd(2026, 9, 28), ymd(2026, 9, 29)},
		{"files modified yesterday", ymd(2026, 9, 27), ymd(2026, 9, 28)},
		{"files modified the day before yesterday", ymd(2026, 9, 26), ymd(2026, 9, 27)},
		{"找本月修改的 log", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"找這個月修改的 log", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"找上個月修改的 log", ymd(2026, 8, 1), ymd(2026, 9, 1)},
		{"找今年修改的 yaml", ymd(2026, 1, 1), ymd(2027, 1, 1)},
		{"找去年修改的 yaml", ymd(2025, 1, 1), ymd(2026, 1, 1)},
		{"this month", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"last month", ymd(2026, 8, 1), ymd(2026, 9, 1)},
		{"this year", ymd(2026, 1, 1), ymd(2027, 1, 1)},
		{"last year", ymd(2025, 1, 1), ymd(2026, 1, 1)},

		// date ranges: a date end is inclusive
		{"找 2026/09/01 到 2026/09/15 修改的檔案", ymd(2026, 9, 1), ymd(2026, 9, 16)},
		{"找 9 月 1 日到 9 月 15 日的 yml 檔", ymd(2026, 9, 1), ymd(2026, 9, 16)},
		{"找 9 月 1 日到 15 日的 yml 檔", ymd(2026, 9, 1), ymd(2026, 9, 16)},
		{"找 2026-09-01 至 2026-09-30 的 pdf", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"找 9/1 到 9/15 的檔案", ymd(2026, 9, 1), ymd(2026, 9, 16)},
		{"找 9/20 到 9/28 改過的 conf", ymd(2026, 9, 20), ymd(2026, 9, 29)},
		{"找 2025 年到 2026 年的 pdf", ymd(2025, 1, 1), ymd(2027, 1, 1)},
		{"找 8 月到 9 月的檔案", ymd(2026, 8, 1), ymd(2026, 10, 1)},
		{"files modified from 2026-09-01 to 2026-09-15", ymd(2026, 9, 1), ymd(2026, 9, 16)},
		{"files changed between Sep 1 and Sep 15", ymd(2026, 9, 1), ymd(2026, 9, 16)},
		{"yml files from September 1 through September 15", ymd(2026, 9, 1), ymd(2026, 9, 16)},
		{"files from 2025 to 2026", ymd(2025, 1, 1), ymd(2027, 1, 1)},
		// "to now" ends at the instant now; since has no end at all
		{"找從 9 月 1 日到現在修改的檔案", ymd(2026, 9, 1), dtNow},
		{"找 9 月 1 日至現在的檔案", ymd(2026, 9, 1), dtNow},
		{"找 9 月 1 日到目前的檔案", ymd(2026, 9, 1), dtNow},
		{"files changed from Sep 1 until now", ymd(2026, 9, 1), dtNow},
		{"files changed from Sep 1 to now", ymd(2026, 9, 1), dtNow},
		{"from 9/1 to now", ymd(2026, 9, 1), dtNow},
		{"today 01:00 to now", at(2026, 9, 28, 1, 0), dtNow},
		{"找從 9 月 1 日修改的檔案", ymd(2026, 9, 1), none},
		{"找 9 月 1 日以來的 log", ymd(2026, 9, 1), none},
		{"從 9 月 1 日開始修改的檔案", ymd(2026, 9, 1), none},
		{"files modified since September 1", ymd(2026, 9, 1), none},
		{"since Sep 1", ymd(2026, 9, 1), none},

		// times of day: a clock end is exact
		{"找今天 2 點到 4 點修改的檔案", at(2026, 9, 28, 2, 0), at(2026, 9, 28, 4, 0)},
		{"今天 1:00 到 3:30", at(2026, 9, 28, 1, 0), at(2026, 9, 28, 3, 30)},
		{"today 01:00 to 03:30", at(2026, 9, 28, 1, 0), at(2026, 9, 28, 3, 30)},
		{"找昨天 23:00 到今天 02:00 修改的 log", at(2026, 9, 27, 23, 0), at(2026, 9, 28, 2, 0)},
		{"找 2026-09-28 01:00 到 03:30 改過的 yml", at(2026, 9, 28, 1, 0), at(2026, 9, 28, 3, 30)},
		{"查 9 月 28 日凌晨 1 點到 5 點改了什麼檔案", at(2026, 9, 28, 1, 0), at(2026, 9, 28, 5, 0)},
		{"找凌晨 1 點到 5 點修改的檔案", at(2026, 9, 28, 1, 0), at(2026, 9, 28, 5, 0)},   // no date: today
		{"找下午 1 點到 5 點修改的檔案", at(2026, 9, 28, 13, 0), at(2026, 9, 28, 17, 0)}, // end inherits 下午
		{"找上午 9 點到 11 點修改的檔案", at(2026, 9, 28, 9, 0), at(2026, 9, 28, 11, 0)},
		{"找晚上 10 點到 11 點半修改的檔案", at(2026, 9, 28, 22, 0), at(2026, 9, 28, 23, 30)},
		{"找昨天下午 3 點 15 分到 4 點修改的檔案", at(2026, 9, 27, 15, 15), at(2026, 9, 27, 16, 0)},
		{"找中午 12 點到 1 點修改的檔案", at(2026, 9, 28, 12, 0), at(2026, 9, 28, 13, 0)},
		{"files modified today between 2am and 4am", at(2026, 9, 28, 2, 0), at(2026, 9, 28, 4, 0)},
		{"files modified between 2am and 4am", at(2026, 9, 28, 2, 0), at(2026, 9, 28, 4, 0)},
		{"files modified between 12am and 1am", at(2026, 9, 28, 0, 0), at(2026, 9, 28, 1, 0)},
		{"files modified between 11am and 12pm", at(2026, 9, 28, 11, 0), at(2026, 9, 28, 12, 0)},
		{"files modified between 11:30pm and 11:45 pm", at(2026, 9, 28, 23, 30), at(2026, 9, 28, 23, 45)},
		{"files modified between 2 pm and 4 pm", at(2026, 9, 28, 14, 0), at(2026, 9, 28, 16, 0)},
		{"log files modified yesterday from 23:00 to today 02:00", at(2026, 9, 27, 23, 0), at(2026, 9, 28, 2, 0)},
		{"files changed on 2026-09-28 between 01:00 and 03:30", at(2026, 9, 28, 1, 0), at(2026, 9, 28, 3, 30)},
		{"files changed on 2026-09-28 at 01:00 to 03:30", at(2026, 9, 28, 1, 0), at(2026, 9, 28, 3, 30)},
		{"找 2026-09-28 到 2026-09-28 01:00 的檔案", ymd(2026, 9, 28), at(2026, 9, 28, 1, 0)},
		{"since yesterday 23:00", at(2026, 9, 27, 23, 0), none},

		// relative times stay durations from now, open-ended
		{"找最近 1 小時修改的檔案", dtNow.Add(-time.Hour), none},
		{"找最近 6 小時的 log", dtNow.Add(-6 * time.Hour), none},
		{"找最近 30 分鐘的 conf", dtNow.Add(-30 * time.Minute), none},
		{"找過去 2 天修改的 yaml", dtNow.Add(-48 * time.Hour), none},
		{"last hour", dtNow.Add(-time.Hour), none},
		{"last 6 hours", dtNow.Add(-6 * time.Hour), none},
		{"last 30 minutes", dtNow.Add(-30 * time.Minute), none},
		{"past 30 minutes", dtNow.Add(-30 * time.Minute), none},
		{"modified in the last 2 days", dtNow.Add(-48 * time.Hour), none},
		{"in the last month", dtNow.Add(-30 * 24 * time.Hour), none}, // rolling, unlike "last month"
		{"last week", dtNow.Add(-7 * 24 * time.Hour), none},
		{"最近 3 月", dtNow.Add(-90 * 24 * time.Hour), none}, // "3 月" after 最近 is a duration
		{"7 日內", dtNow.Add(-7 * 24 * time.Hour), none},
		{"3 月前", none, dtNow.Add(-90 * 24 * time.Hour)},
		{"3 年前", none, dtNow.Add(-3 * 365 * 24 * time.Hour)},

		// several time conditions intersect
		{"找今年修改而且是 9 月的檔案", ymd(2026, 9, 1), ymd(2026, 10, 1)},
		{"找 9 月修改、最近 7 天的檔案", dtNow.Add(-7 * 24 * time.Hour), ymd(2026, 10, 1)},
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
			for _, b := range []time.Time{q.ModifiedAfter, q.ModifiedBefore} {
				if !b.IsZero() && b.Location() != dtLoc {
					t.Errorf("Parse(%q) bound %s is not in the local zone of now", tt.in, b)
				}
			}
		})
	}
}

func show(t time.Time) string {
	if t.IsZero() {
		return "unbounded"
	}
	return t.Format("2006-01-02 15:04:05 MST")
}

func TestDateCombinesWithOtherConditions(t *testing.T) {
	tests := []struct {
		in   string
		want Query
	}{
		{"找 9 月修改的 yml 檔，排除 vendor", Query{Extensions: []string{"yaml", "yml"}, ExcludeDirs: []string{"vendor"},
			ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 10, 1)}},
		{`找昨天修改而且包含 "ERROR" 的 log`, Query{Extensions: []string{"log"}, Contents: []string{"ERROR"},
			ModifiedAfter: ymd(2026, 9, 27), ModifiedBefore: ymd(2026, 9, 28)}},
		{"找最近 6 小時大於 10MB 的 log", Query{Extensions: []string{"log"}, Sizes: []SizeCond{{">", 10 << 20}},
			ModifiedAfter: dtNow.Add(-6 * time.Hour)}},
		{"找 9 月最大的 10 個 log", Query{Extensions: []string{"log"}, Sort: SortLargest, Limit: 10,
			ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 10, 1)}},
		{"找 2026-09-28 最新修改的 20 個檔案", Query{Sort: SortNewest, Limit: 20,
			ModifiedAfter: ymd(2026, 9, 28), ModifiedBefore: ymd(2026, 9, 29)}},
		{"找 9/1 到 9/15 修改的 log，排除 archive 目錄", Query{Extensions: []string{"log"}, ExcludeDirs: []string{"archive"},
			ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 9, 16)}},
		{"找 9/1 到 9/15 大於 1MB 的 log", Query{Extensions: []string{"log"}, Sizes: []SizeCond{{">", 1 << 20}},
			ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 9, 16)}},
		{"go files modified in September larger than 1MB, top 5", Query{Extensions: []string{"go"}, Sizes: []SizeCond{{">", 1 << 20}},
			Limit: 5, ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 10, 1)}},
		{"10 newest log files from Sep 1 to Sep 15 excluding archive", Query{Extensions: []string{"log"}, ExcludeDirs: []string{"archive"},
			Sort: SortNewest, Limit: 10, ModifiedAfter: ymd(2026, 9, 1), ModifiedBefore: ymd(2026, 9, 16)}},
		{`log files modified today containing "timeout" ignore case`, Query{Extensions: []string{"log"}, Contents: []string{"timeout"},
			IgnoreCase: true, ModifiedAfter: ymd(2026, 9, 28), ModifiedBefore: ymd(2026, 9, 29)}},
		{"在 /var/log 找今天 2 點到 4 點修改的 log", Query{Root: "/var/log", Extensions: []string{"log"},
			ModifiedAfter: at(2026, 9, 28, 2, 0), ModifiedBefore: at(2026, 9, 28, 4, 0)}},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			got, err := Parse(tt.in, dtNow)
			if err != nil {
				t.Fatalf("Parse(%q): %v", tt.in, err)
			}
			tt.want.Lang, tt.want.Now = DetectLang(tt.in), dtNow
			if got.ModifiedAfter.Equal(tt.want.ModifiedAfter) && got.ModifiedBefore.Equal(tt.want.ModifiedBefore) {
				got.ModifiedAfter, got.ModifiedBefore = tt.want.ModifiedAfter, tt.want.ModifiedBefore
			}
			if !equalQuery(got, &tt.want) {
				t.Errorf("Parse(%q)\n got  %+v\n want %+v", tt.in, *got, tt.want)
			}
		})
	}
}

func equalQuery(a, b *Query) bool {
	return strings.Join(a.Extensions, ",") == strings.Join(b.Extensions, ",") &&
		strings.Join(a.Contents, ",") == strings.Join(b.Contents, ",") &&
		strings.Join(a.ExcludeDirs, ",") == strings.Join(b.ExcludeDirs, ",") &&
		len(a.Sizes) == len(b.Sizes) && (len(a.Sizes) == 0 || a.Sizes[0] == b.Sizes[0]) &&
		a.Root == b.Root && a.IgnoreCase == b.IgnoreCase && a.Sort == b.Sort && a.Limit == b.Limit &&
		a.ModifiedAfter.Equal(b.ModifiedAfter) && a.ModifiedBefore.Equal(b.ModifiedBefore)
}

func TestInvalidDatesAndTimes(t *testing.T) {
	tests := []struct {
		in, en, zh string
	}{
		{"2026-02-29", "invalid date", ""},
		{"找 2026-02-29 的檔案", "", "日期不存在"},
		{"2026-13-01", "invalid date", ""},
		{"2026-04-31", "invalid date", ""},
		{"2026-00-10", "invalid date", ""},
		{"2026-09-00", "invalid date", ""},
		{"找 2 月 30 日的檔案", "", "日期不存在"},
		{"找 2026 年 2 月 29 日的檔案", "", "日期不存在"},
		{"9/31", "invalid date", ""},
		{"13/01", "invalid date", ""},
		{"找 13 月的檔案", "", "月份不存在"},
		{"找 0 月的檔案", "", "月份不存在"},
		{"2026-13", "invalid month", ""},
		{"files on Feb 30", "invalid date", ""},

		{"找今天 25:00 到 26:00 的檔案", "", "時間不存在"},
		{"today 12:99 to 13:00", "invalid time of day", ""},
		{"today 24:00 to 01:00", "invalid time of day", ""},
		{"between 13pm and 2pm", "invalid time of day", ""},
		{"between 0am and 2am", "invalid time of day", ""},
		{"找晚上 12 點到 1 點的檔案", "", "時間不存在"},
		{"找下午 13 點到 14 點的檔案", "", "時間不存在"},
		{"找今天 25 點到 26 點的檔案", "", "時間不存在"},

		// a range must go forward; nothing is swapped silently
		{"找 9/20 到 9/10 的檔案", "", "開始時間必須早於結束時間"},
		{"files from Sep 20 to Sep 10", "start time must be before end time", ""},
		{"today 04:00 to 02:00", "start time must be before end time", ""},
		{"找今天 3 點到 3 點的檔案", "", "開始時間必須早於結束時間"},
		{"找 2026 年到 2025 年的檔案", "", "開始時間必須早於結束時間"},
		{"找今天 23:00 到昨天 01:00 的檔案", "", "開始時間必須早於結束時間"},
		{"找 12 月 1 日到現在的檔案", "", "開始時間必須早於結束時間"}, // a future start cannot reach now
		{"today 11:00 to now", "start time must be before end time", ""},

		// incomplete or ambiguous
		{"找 2 點修改的檔案", "", "單一時間點不能當作條件"},
		{"files modified at 2am", "a single point in time is not a range", ""},
		{"找凌晨修改的檔案", "", "後面需要時間"},
		{"找 9/1 到 的檔案", "", "時間範圍需要結束日期或時間"},
		{"files from Sep 1 to", "the range needs an end date or time", ""},
		{"files between Sep 1", `"between" needs "and"`, ""},
		{"03/04/2026", "ambiguous date format", ""},
		{"04-03-2026", "ambiguous date format", ""},
		{"2026.09.28", "ambiguous date format", ""},
		{"26-09-28", "ambiguous date format", ""},
		{"26/09/28", "ambiguous date format", ""},
		{"找 9 月 1 日之前的檔案", "", "看不懂這個詞"}, // before/after a date are not supported yet
		{"files that may contain TODO", `cannot understand: "may"`, ""},
		{"找 2026 年 3 月前的檔案", "", "看不懂這個時間"},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			q, err := Parse(tt.in, dtNow)
			var pe *Error
			if !errors.As(err, &pe) {
				t.Fatalf("Parse(%q) = %+v, %v; want an error", tt.in, q, err)
			}
			if tt.en != "" && !strings.Contains(pe.Message(English), tt.en) {
				t.Errorf("Parse(%q) error %q, want %q", tt.in, pe.Message(English), tt.en)
			}
			if tt.zh != "" && !strings.Contains(pe.Message(Chinese), tt.zh) {
				t.Errorf("Parse(%q) error %q, want %q", tt.in, pe.Message(Chinese), tt.zh)
			}
		})
	}
}

// TestNotDates keeps numbers, words and text that are not dates working as before.
func TestNotDates(t *testing.T) {
	tests := []struct {
		in   string
		want Query
	}{
		{"2026 files", Query{Limit: 2026}}, // a bare year needs a preposition ("in 2026")
		{"top 10", Query{Limit: 10}},
		{"containing 2026-09-28", Query{Contents: []string{"2026-09-28"}}},
		{`containing "9 月 28 日"`, Query{Contents: []string{"9 月 28 日"}}},
		{"包含 帳號", Query{Contents: []string{"帳號"}}}, // 號 is not a dictionary word
		{"包含 重點", Query{Contents: []string{"重點"}}}, // neither is 點
		{"找 conf 檔案", Query{Extensions: []string{"conf"}}},
		{"找 tf 檔", Query{Extensions: []string{"tf"}}},
		{"找 conf", Query{Extensions: []string{"conf"}}},
		{"exclude dec2026", Query{ExcludeDirs: []string{"dec2026"}}},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			got, err := Parse(tt.in, dtNow)
			if err != nil {
				t.Fatalf("Parse(%q): %v", tt.in, err)
			}
			tt.want.Lang, tt.want.Now = DetectLang(tt.in), dtNow
			if !equalQuery(got, &tt.want) {
				t.Errorf("Parse(%q)\n got  %+v\n want %+v", tt.in, *got, tt.want)
			}
		})
	}
	// "terraform" is not an alias: only the explicit extension is understood.
	if q, err := Parse("找 2026 年修改的 terraform 檔", dtNow); err != nil || strings.Join(q.Extensions, ",") != "terraform" {
		t.Errorf("terraform 檔 = %+v, %v; want the literal extension .terraform, not .tf", q, err)
	}
}

// TestCalendarUnitsAcrossDST checks that days, months and years follow the
// calendar, not multiples of 24 hours, where clocks change.
func TestCalendarUnitsAcrossDST(t *testing.T) {
	ny, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		now  time.Time
		in   string
		from time.Time
		hrs  float64 // length of the range
	}{
		{time.Date(2026, 3, 8, 12, 0, 0, 0, ny), "today", time.Date(2026, 3, 8, 0, 0, 0, 0, ny), 23},
		{time.Date(2026, 3, 9, 12, 0, 0, 0, ny), "yesterday", time.Date(2026, 3, 8, 0, 0, 0, 0, ny), 23},
		{time.Date(2026, 3, 9, 12, 0, 0, 0, ny), "2026-11-01", time.Date(2026, 11, 1, 0, 0, 0, 0, ny), 25},
		{time.Date(2026, 3, 9, 12, 0, 0, 0, ny), "this month", time.Date(2026, 3, 1, 0, 0, 0, 0, ny), 31*24 - 1},
		{time.Date(2026, 3, 9, 12, 0, 0, 0, ny), "2026-03-07 to 2026-03-08", time.Date(2026, 3, 7, 0, 0, 0, 0, ny), 47},
	}
	for _, tt := range tests {
		q, err := Parse(tt.in, tt.now)
		if err != nil {
			t.Fatalf("Parse(%q): %v", tt.in, err)
		}
		if !q.ModifiedAfter.Equal(tt.from) || q.ModifiedBefore.Hour() != 0 || q.ModifiedBefore.Minute() != 0 ||
			q.ModifiedBefore.Sub(q.ModifiedAfter).Hours() != tt.hrs {
			t.Errorf("Parse(%q) = [%s, %s); want start %s, midnight end, %v hours", tt.in,
				q.ModifiedAfter, q.ModifiedBefore, tt.from, tt.hrs)
		}
	}
}

func TestExplainShowsTimeRange(t *testing.T) {
	q, err := Parse("找 2026-09-28 修改的 yml 檔", dtNow)
	if err != nil {
		t.Fatal(err)
	}
	out := Explain(q, MacOS)
	for _, want := range []string{"2026-09-28 00:00:00 <= mtime < 2026-09-29 00:00:00", "時區", "CST (+08:00)"} {
		if !strings.Contains(out, want) {
			t.Errorf("Explain missing %q:\n%s", want, out)
		}
	}
	q, _ = Parse("files modified since September 1", dtNow)
	if out := Explain(q, Linux); !strings.Contains(out, "after 2026-09-01 00:00:00 (mtime >= 2026-09-01 00:00:00)") ||
		strings.Contains(out, "<= mtime <") {
		t.Errorf("open-ended range shown wrong:\n%s", out)
	}
}
