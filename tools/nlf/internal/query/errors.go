package query

import (
	"fmt"
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
	return &Error{Token: tok,
		zh:     "看不懂這個詞",
		en:     "cannot understand",
		hintZh: "提示：要搜尋的內容請加引號，例如 包含 \"錯誤訊息\"；執行 nlf --help 查看支援的說法。",
		hintEn: "hint: quote text you want to search for, e.g. containing \"some text\"; run nlf --help for supported phrases.",
	}
}
