package query

import (
	"fmt"
	"strings"
	"unicode"
)

// Error is a parse error with bilingual messages.
type Error struct {
	Token          string // offending input fragment, if any
	zh, en         string
	hintZh, hintEn string
}

func (e *Error) Error() string { return e.Message(English) }

// Message renders the error in the given language.
func (e *Error) Message(l Lang) string {
	msg, hint := e.en, e.hintEn
	if l == Chinese {
		msg, hint = e.zh, e.hintZh
	}
	if e.Token != "" {
		if l == Chinese {
			msg = fmt.Sprintf("%s：「%s」", msg, e.Token)
		} else {
			msg = fmt.Sprintf("%s: %q", msg, e.Token)
		}
	}
	if hint != "" {
		msg += "\n" + hint
	}
	return msg
}

// DetectLang returns Chinese when the input contains any Han character.
func DetectLang(s string) Lang {
	for _, r := range s {
		if unicode.Is(unicode.Han, r) {
			return Chinese
		}
	}
	return English
}

func errUnknown(tok string) *Error {
	if looksLikeWindowsPath(tok) {
		return errWindowsPath(tok)
	}
	return &Error{Token: tok,
		zh:     "看不懂這個詞",
		en:     "cannot understand",
		hintZh: "提示：要搜尋的內容請加引號，例如 包含 \"錯誤訊息\"；執行 nlf --help 查看支援的說法。",
		hintEn: "hint: quote text you want to search for, e.g. containing \"some text\"; run nlf --help for supported phrases.",
	}
}

// errWindowsPath rejects a Windows path inside the query. Paths in the query
// use "/" on every OS; a Windows path goes in --dir, which takes an OS-native
// path as is.
func errWindowsPath(tok string) *Error {
	return &Error{Token: tok,
		zh:     "查詢文字裡不能使用 Windows 路徑",
		en:     "a Windows path cannot be used inside the query",
		hintZh: `提示：Windows 路徑（C:\…、\\server\share）請用 --dir 指定，例如 nlf --dir "C:\Users\me\Documents" "找 go 檔"；查詢文字裡的路徑請用 /（./src、~/Documents）。`,
		hintEn: `hint: give a Windows path (C:\..., \\server\share) with --dir, e.g. nlf --dir "C:\Users\me\Documents" "go files"; paths inside the query use / (./src, ~/Documents).`,
	}
}

// looksLikeWindowsPath reports whether s starts like a Windows drive path
// ("C:\x", "c:/x") or a UNC path ("\\server\share"). Such a word is an
// error with a pointer to --dir, and after a location word ("in", "在") so
// is such quoted text, which would otherwise be read as content. The query
// syntax is the same on every OS.
func looksLikeWindowsPath(s string) bool {
	if strings.HasPrefix(s, `\\`) {
		return true
	}
	return len(s) >= 3 && ('a' <= s[0]|0x20 && s[0]|0x20 <= 'z') && s[1] == ':' && (s[2] == '\\' || s[2] == '/')
}
