package query

import (
	"strings"
	"testing"
)

func TestDepthConditions(t *testing.T) {
	const enNote = " (the search location is level 0; files directly inside it are level 1)"
	const zhNote = " (搜尋位置本身是第 0 層，直接在裡面的是第 1 層)"
	for _, c := range []struct {
		q      Query
		en, zh string
	}{
		{Query{MaxDepth: 1, HasMaxDepth: true}, "at most 1 level", "最多 1 層"},
		{Query{MaxDepth: 3, HasMaxDepth: true}, "at most 3 levels", "最多 3 層"},
		{Query{HasMaxDepth: true}, "at most 0 levels", "最多 0 層"},
		{Query{MinDepth: 1}, "at least 1 level", "至少 1 層"},
		{Query{MinDepth: 2}, "at least 2 levels", "至少 2 層"},
		{Query{MinDepth: 1, MaxDepth: 3, HasMaxDepth: true}, "1 to 3 levels", "1 到 3 層"},
		{Query{MinDepth: 2, MaxDepth: 3, HasMaxDepth: true}, "2 to 3 levels", "2 到 3 層"},
		{Query{MinDepth: 1, MaxDepth: 1, HasMaxDepth: true}, "exactly 1 level", "剛好 1 層"},
		{Query{MinDepth: 2, MaxDepth: 2, HasMaxDepth: true}, "exactly 2 levels", "剛好 2 層"},
	} {
		for _, l := range []struct {
			lang         Lang
			label, value string
		}{{English, "depth:", c.en + enNote}, {Chinese, "搜尋深度", c.zh + zhNote}} {
			cs := Conditions(&c.q, l.lang)
			if len(cs) != 1 || cs[0].Label != l.label || cs[0].Value != l.value {
				t.Errorf("%+v lang %d: got %+v, want [{%s %s}]", c.q, l.lang, cs, l.label, l.value)
			}
		}
	}
	// --min-depth 0 alone is no limit and shows nothing.
	if cs := Conditions(&Query{}, English); len(cs) != 0 {
		t.Errorf("zero Query: got %+v, want no conditions", cs)
	}
	// The depth row comes after the exclude dirs and before the order.
	q := &Query{ExcludeDirs: []string{"vendor"}, MaxDepth: 2, HasMaxDepth: true, Sort: SortLargest, Limit: 3}
	var labels []string
	for _, c := range Conditions(q, English) {
		labels = append(labels, c.Label)
	}
	if got, want := strings.Join(labels, ","), "exclude dirs:,depth:,order:,limit:"; got != want {
		t.Errorf("row order: got %s, want %s", got, want)
	}
	for _, p := range []Platform{MacOS, Linux} {
		out := Explain(q, p)
		for _, want := range []string{"depth:", "at most 2 levels", FindCommand(q, p), "-maxdepth 2"} {
			if !strings.Contains(out, want) {
				t.Errorf("Explain(%s) is missing %q:\n%s", p.Name(), want, out)
			}
		}
	}
	zq := *q
	zq.Lang = Chinese
	if out := Explain(&zq, MacOS); !strings.Contains(out, "搜尋深度") || !strings.Contains(out, "最多 2 層") {
		t.Errorf("Chinese Explain is missing the depth row:\n%s", out)
	}
}
