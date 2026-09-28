package search

import (
	"bytes"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

// scanOracle is what scanTerms must report, computed on the whole file at
// once: nil for a binary file (a NUL byte in the first chunkSize bytes;
// this assumes the first Read of a regular local file returns a full
// chunk, as it does), otherwise whether each term occurs, after
// bytes.ToLower of both sides when ignoreCase is set.
func scanOracle(content []byte, terms []string, ignoreCase bool) []bool {
	if bytes.IndexByte(content[:min(len(content), chunkSize)], 0) >= 0 {
		return nil
	}
	low := func(b []byte) []byte {
		if ignoreCase {
			return bytes.ToLower(b)
		}
		return b
	}
	hay := low(content)
	found := make([]bool, len(terms))
	for i, term := range terms {
		found[i] = bytes.Contains(hay, low([]byte(term)))
	}
	return found
}

func never([]bool) bool { return false }

func allFound(found []bool) bool { return !slices.Contains(found, false) }

// checkScan runs scanTerms on the file at p (holding content) and compares
// it with the oracle: exactly when it reads the whole file, and without
// false positives when it may stop early.
func checkScan(t *testing.T, p string, content []byte, terms []string, ic bool) []bool {
	t.Helper()
	want := scanOracle(content, terms, ic)
	got, err := scanTerms(p, terms, ic, never)
	if err != nil {
		t.Fatalf("scanTerms: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("scanTerms(%d bytes, %q, ic=%v) = %v, want %v", len(content), abbrev(terms), ic, got, want)
	}
	early, err := scanTerms(p, terms, ic, allFound)
	if err != nil {
		t.Fatalf("scanTerms: %v", err)
	}
	if (early == nil) != (want == nil) {
		t.Fatalf("scanTerms with early stop = %v, want %v", early, want)
	}
	for i := range early {
		if early[i] && !want[i] {
			t.Fatalf("scanTerms with early stop found %q, which is not in the file", abbrev(terms)[i])
		}
	}
	if again, _ := scanTerms(p, terms, ic, never); !reflect.DeepEqual(again, got) {
		t.Fatalf("scanTerms is not deterministic: %v then %v", got, again)
	}
	return got
}

func abbrev(terms []string) []string {
	out := make([]string, len(terms))
	for i, s := range terms {
		if len(s) > 40 {
			s = s[:20] + "…" + s[len(s)-20:]
		}
		out[i] = s
	}
	return out
}

func FuzzScanTerms(f *testing.F) {
	f.Add([]byte("NEEDLE"), "NEEDLE", "", uint32(chunkSize-3), byte('a'), false)
	f.Add([]byte("x"), "x", "y", uint32(0), byte('a'), false)
	f.Add([]byte(""), "a", "", uint32(0), byte('a'), false)
	f.Add([]byte("\x00TODO"), "TODO", "", uint32(10), byte('a'), false)
	f.Add([]byte("\x00TODO"), "TODO", "", uint32(chunkSize-1), byte('a'), false)
	f.Add([]byte("\x00TODO"), "TODO", "", uint32(chunkSize), byte('a'), false)
	f.Add([]byte("Hello World"), "hello", "WORLD", uint32(chunkSize-5), byte('z'), true)
	f.Add([]byte("\xe2\x84\xaak"), "kk", "", uint32(chunkSize-3), byte('a'), true)
	f.Add([]byte("\xc4\xb0i"), "ii", "", uint32(chunkSize-2), byte('a'), true)
	f.Add([]byte("中zz"), "\ufffd", "", uint32(chunkSize-3), byte('a'), true)
	f.Add([]byte("\xff\xfe"), "\xff", "\ufffd", uint32(chunkSize-1), byte('b'), true)
	f.Add([]byte("\u2126\u212b\u1e9e"), "ωåß", "", uint32(chunkSize-4), byte('a'), true)
	f.Add([]byte("abc\xe2"), "\ufffd", "c\ufffd", uint32(0), byte('a'), true)
	f.Add([]byte("\xe2\x82"), "a\ufffd\ufffd", "", uint32(chunkSize-1), byte('a'), true)
	f.Add([]byte("END"), strings.Repeat("a", chunkSize+2)+"END", "", uint32(2*chunkSize), byte('a'), false)
	p := filepath.Join(f.TempDir(), "content")
	f.Fuzz(func(t *testing.T, data []byte, t1, t2 string, off uint32, fill byte, ic bool) {
		// The parser never produces an empty term (L6), so t1 must be set
		// and an empty t2 means a single term.
		if len(data) > 3*chunkSize || len(t1) > 2*chunkSize || len(t2) > 256 || t1 == "" {
			return
		}
		content := append(bytes.Repeat([]byte{fill}, int(off%(2*chunkSize+1))), data...)
		if err := os.WriteFile(p, content, 0o644); err != nil {
			t.Fatal(err)
		}
		terms := []string{t1}
		if t2 != "" {
			terms = append(terms, t2)
		}
		checkScan(t, p, content, terms, ic)
	})
}

// TestScanTermsBoundaryCases pins scanTerms at the 64 KiB read boundaries,
// against both the oracle and an explicit expectation.
func TestScanTermsBoundaryCases(t *testing.T) {
	a := func(n int) string { return strings.Repeat("a", n) }
	type tc struct {
		name    string
		content string
		terms   []string
		ic      bool
		want    []bool // nil: binary
	}
	cases := []tc{
		{"empty file", "", []string{"a"}, false, []bool{false}},
		{"one byte", "a", []string{"a", "b"}, false, []bool{true, false}},
		{"64K-1 needle at end", a(chunkSize-7) + "NEEDLE", []string{"NEEDLE"}, false, []bool{true}},
		{"64K needle at end", a(chunkSize-6) + "NEEDLE", []string{"NEEDLE"}, false, []bool{true}},
		{"64K+1 needle at end", a(chunkSize-5) + "NEEDLE", []string{"NEEDLE"}, false, []bool{true}},
		{"NUL at 0", "\x00TODO", []string{"TODO"}, false, nil},
		{"NUL at 64K-1", a(chunkSize-1) + "\x00TODO", []string{"TODO"}, false, nil},
		{"NUL at 64K", a(chunkSize) + "\x00TODO", []string{"TODO"}, false, []bool{true}},
		{"invalid byte, case-sensitive", "x\xffy", []string{"\xff", "\ufffd"}, false, []bool{true, false}},
		{"invalid byte, ignore case", "x\xffy", []string{"\ufffd", "X\xffY"}, true, []bool{true, true}},
		{"invalid byte at 64K", a(chunkSize-1) + "\xff\xfeZ", []string{"\ufffd\ufffdz"}, true, []bool{true}},
		{"1 MiB prefix", strings.Repeat("b", 1<<20) + "END", []string{"END", "NOPE"}, false, []bool{true, false}},
		{"term over two boundaries", a(chunkSize-50) + "B" + strings.Repeat("x", chunkSize+100) + "E" + a(10),
			[]string{"B" + strings.Repeat("x", chunkSize+100) + "E"}, false, []bool{true}},
		// B2: lower-casing changes the length (K U+212A is 3 bytes, k is 1).
		{"Kelvin before 64K", a(chunkSize-3) + "\u212ak", []string{"kk"}, true, []bool{true}},
		{"Kelvin split at 64K", a(chunkSize-1) + "\u212ak", []string{"kk"}, true, []bool{true}},
		{"Kelvin after 64K", a(chunkSize) + "\u212ak", []string{"kk"}, true, []bool{true}},
		{"dotted I at 64K", a(chunkSize-2) + "\u0130i", []string{"ii"}, true, []bool{true}},
		{"Ohm Angstrom sharp S at 64K", a(chunkSize-4) + "\u2126\u212b\u1e9e", []string{"ωåß"}, true, []bool{true}},
		{"Kelvin case-sensitive", a(chunkSize-3) + "\u212ak", []string{"kk", "\u212ak"}, false, []bool{false, true}},
		// B3: a rune split by the read boundary is not U+FFFD.
		{"split rune is not U+FFFD", a(chunkSize-3) + "中zz", []string{"\ufffd"}, true, []bool{false}},
		{"split rune still matches", a(chunkSize-1) + "中zz", []string{"中z"}, true, []bool{true}},
		// An incomplete UTF-8 sequence at EOF is flushed as U+FFFD, as when
		// the whole file is lower-cased at once.
		{"incomplete tail at EOF", "abc\xe2", []string{"\ufffd", "c\ufffd"}, true, []bool{true, true}},
		{"incomplete tail at 64K EOF", a(chunkSize-1) + "\xe2", []string{"a\ufffd"}, true, []bool{true}},
		{"incomplete tail split at 64K EOF", a(chunkSize-1) + "\xe2\x82", []string{"a\ufffd\ufffd"}, true, []bool{true}},
		{"incomplete tail after 64K EOF", a(chunkSize) + "\xf0\x9f", []string{"a\ufffd\ufffd"}, true, []bool{true}},
	}
	// A needle crossing the boundary at every split offset.
	for k := 1; k < len("NEEDLE"); k++ {
		cases = append(cases,
			tc{"split NEEDLE", a(chunkSize-k) + "NEEDLE" + a(3), []string{"NEEDLE"}, false, []bool{true}},
			tc{"split needle ignore case", a(chunkSize-k) + "NeEdLe" + a(3), []string{"nEeDlE"}, true, []bool{true}})
	}
	for k := 1; k < len("\u212a\u212a"); k++ {
		cases = append(cases, tc{"split Kelvin pair", a(chunkSize-k) + "\u212a\u212a", []string{"kk"}, true, []bool{true}})
	}
	dir := t.TempDir()
	for i, c := range cases {
		p := filepath.Join(dir, "case"+string(rune('a'+i%26))+string(rune('a'+i/26)))
		if err := os.WriteFile(p, []byte(c.content), 0o644); err != nil {
			t.Fatal(err)
		}
		t.Run(c.name, func(t *testing.T) {
			if got := checkScan(t, p, []byte(c.content), c.terms, c.ic); !reflect.DeepEqual(got, c.want) {
				t.Errorf("scanTerms = %v, want %v", got, c.want)
			}
		})
	}
}
