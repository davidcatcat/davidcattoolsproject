package query

import (
	"path"
	"slices"
)

// Filename conditions.
//
// A filename condition compares the file's base name (never its directory)
// and needs an explicit subject word plus an operator, so that ordinary
// content and extension phrases keep their meaning:
//
//	檔名是 application.yml        filename is nginx.conf       exact
//	檔名包含 config               name contains backup         contains
//	檔名以 app- 開頭              name starts with api-        prefix
//	檔名以 .bak 結尾              name ends with .old          suffix
//	檔名符合 "*.conf"             filename matches "app-*.yml" glob (path.Match, not regex)
//	名為 docker-compose.yml       files named kubeconfig       exact, subject implied
//
// "包含 nginx" stays a content condition and "go 或 md 檔" an extension list.
// Matching is case-sensitive; the value keeps the case it was typed in.

var (
	nameSubjects = []string{"檔名", "档名", "檔案名稱", "档案名称", "檔案名", "文件名", "文件名稱", "名稱", "名称", "名字",
		"filename", "file name", "file names", "filenames", "name", "names"}
	// Operators that imply the subject: "files named x", "名為 x 的檔案".
	impliedNameOps = []string{"named", "called", "名為", "名为", "名叫"}
	nameOps        = map[string]NameMatchKind{
		"是": NameExact, "叫": NameExact, "叫做": NameExact, "等於": NameExact, "等于": NameExact, "為": NameExact, "为": NameExact,
		"=": NameExact, "is": NameExact, "equals": NameExact, "is exactly": NameExact, "exactly": NameExact,
		"包含": NameContains, "包括": NameContains, "含有": NameContains, "有": NameContains,
		"contains": NameContains, "containing": NameContains, "contain": NameContains, "includes": NameContains, "including": NameContains,
		"開頭是": NamePrefix, "開頭為": NamePrefix, "开头是": NamePrefix, "开头为": NamePrefix,
		"starts with": NamePrefix, "start with": NamePrefix, "starting with": NamePrefix,
		"begins with": NamePrefix, "begin with": NamePrefix, "beginning with": NamePrefix,
		"結尾是": NameSuffix, "結尾為": NameSuffix, "结尾是": NameSuffix, "结尾为": NameSuffix,
		"ends with": NameSuffix, "end with": NameSuffix, "ending with": NameSuffix, "ending in": NameSuffix, "ends in": NameSuffix,
		"符合": NameGlob, "匹配": NameGlob, "吻合": NameGlob,
		"matches": NameGlob, "match": NameGlob, "matching": NameGlob, "glob": NameGlob,
	}
	// "以 X 開頭" / "以 X 結尾" / "從 X 開始": the operator wraps the value.
	nameOpen    = []string{"以", "從", "从"}
	nameClosing = map[string]NameMatchKind{"開頭": NamePrefix, "开头": NamePrefix, "開始": NamePrefix, "开始": NamePrefix,
		"結尾": NameSuffix, "结尾": NameSuffix}
	// Words that may directly follow an unquoted name without a space.
	nameBoundaries = append([]string{"開頭", "开头", "結尾", "结尾", "開始", "开始"}, contentBoundaries...)
)

// nameExpr parses one filename condition at the current position. It
// reports false, consuming nothing, when there is none.
func (p *parser) nameExpr() (bool, error) {
	save := p.pos
	kind, ok, err := p.nameOperator()
	if err != nil || !ok {
		p.pos = save
		return false, err
	}
	return true, p.nameValueFor(kind, save)
}

// nameOperator reads a subject and operator ("檔名包含", "filename is",
// "named") and returns the match kind. A wrapping operator ("檔名以") is
// returned as NameExact with the closing word read after the value.
func (p *parser) nameOperator() (NameMatchKind, bool, error) {
	if _, ok := p.accept(impliedNameOps); ok {
		return NameExact, true, nil
	}
	if _, ok := p.accept(nameSubjects); !ok {
		return 0, false, nil
	}
	start := p.pos - 1
	if w, ok := p.accept(mapKeys(nameOps)); ok {
		return nameOps[w], true, nil
	}
	if _, ok := p.accept(nameOpen); ok {
		return nameWrapped, true, nil
	}
	return 0, false, &Error{Token: p.rawFrom(start), zh: "檔名後面需要比對方式", en: "a filename condition needs an operator",
		hintZh: "例如：檔名是 nginx.conf、檔名包含 config、檔名以 app- 開頭、檔名以 .bak 結尾、檔名符合 \"*.conf\"",
		hintEn: `example: filename is nginx.conf, name contains config, name starts with app-, name ends with .bak, filename matches "*.conf"`}
}

// nameWrapped marks "以 X 開頭 / 結尾", whose kind is decided by the closing word.
const nameWrapped NameMatchKind = -1

// nameValueFor reads the value of a filename condition of the given kind
// and adds it to the query.
func (p *parser) nameValueFor(kind NameMatchKind, from int) error {
	v, err := p.nameValue(from)
	if err != nil {
		return err
	}
	if kind == nameWrapped {
		w, ok := p.accept(mapKeys(nameClosing))
		if !ok {
			return &Error{Token: p.rawFrom(from), zh: "「以 … 」後面需要「開頭」或「結尾」", en: `"以 …" must end with 開頭 (starts with) or 結尾 (ends with)`,
				hintZh: "例如：檔名以 app- 開頭、檔名以 .bak 結尾"}
		}
		kind = nameClosing[w]
	}
	return p.addName(NameCond{Kind: kind, Pattern: v})
}

// nameAlternative parses the condition after "或" in a filename OR group.
// A full condition ("或檔名是 x") uses its own operator; a bare value
// ("檔名包含 prod 或 staging") inherits the operator on the left.
func (p *parser) nameAlternative(inherit NameMatchKind) error {
	if ok, err := p.nameExpr(); ok || err != nil {
		return err
	}
	from := p.pos
	if inherit == NamePrefix || inherit == NameSuffix {
		p.accept(nameOpen) // "檔名以 app- 開頭或以 api- 開頭"
	}
	if t, ok := p.cur(); !ok || !(t.kind == tQuoted || t.kind == tExt || t.kind == tHan || isFreeWord(t)) {
		p.pos = from
		return p.step() // not a name: let the OR check report the kind mismatch
	}
	v, err := p.nameValue(from)
	if err != nil {
		return err
	}
	if w, ok := p.accept(mapKeys(nameClosing)); ok && nameClosing[w] != inherit {
		return &Error{Token: p.rawFrom(from), zh: "這個檔名條件和前面的比對方式不同，請寫完整", en: "this filename alternative uses a different operator; write it in full",
			hintZh: "例如：檔名以 app- 開頭或檔名以 .log 結尾"}
	}
	return p.addName(NameCond{Kind: inherit, Pattern: v})
}

// nameValue reads a filename: quoted text as typed, or an unquoted run of
// characters up to a space or punctuation. A keyword glued to the run is
// ambiguous and must be quoted.
func (p *parser) nameValue(from int) (string, error) {
	t, ok := p.cur()
	if !ok || !(t.kind == tQuoted || t.kind == tWord || t.kind == tExt || t.kind == tNum || t.kind == tHan) {
		return "", &Error{Token: p.rawFrom(from), zh: "檔名條件需要名稱", en: "a filename condition needs a name",
			hintZh: "例如：檔名是 nginx.conf；含中文或空白的檔名請加引號，例如 檔名是 \"中文設定檔.yml\"、檔名包含 \"foo bar\"",
			hintEn: `example: filename is nginx.conf; quote names with spaces or non-ASCII text, e.g. name contains "foo bar"`}
	}
	p.pos++
	if t.kind == tQuoted {
		if t.text == "" {
			return "", &Error{Token: p.rawFrom(from), zh: "檔名不能是空字串", en: "a filename pattern must not be empty"}
		}
		return t.text, nil
	}
	v := t.raw
	for {
		nt, ok := p.cur()
		if !ok || nt.spaceBefore || nt.kind == tPunct || nt.kind == tOp || nt.kind == tQuoted {
			return v, nil
		}
		if nt.kind == tHan || nt.kind == tNum || nt.kind == tExt || nt.kind == tWord {
			v += nt.raw
			p.pos++
			continue
		}
		if nt.kind == tCJK && slices.Contains(nameBoundaries, nt.text) {
			return v, nil
		}
		return "", &Error{Token: v + nt.raw, zh: "檔名與關鍵字連在一起，無法確定範圍", en: "the filename runs into a keyword; its extent is ambiguous",
			hintZh: "提示：請用引號標出檔名，例如 檔名是 \"" + v + nt.raw + "\"",
			hintEn: `hint: quote the filename, e.g. filename is "` + v + nt.raw + `"`}
	}
}

// addName validates c and adds it to the query.
func (p *parser) addName(c NameCond) error {
	if c.Kind == NameGlob {
		if _, err := path.Match(c.Pattern, ""); err != nil {
			return &Error{Token: c.Pattern, zh: "檔名 glob pattern 不合法", en: "invalid filename glob pattern",
				hintZh: "提示：glob 支援 *、?、[...]（不是正規表示式），方括號必須成對，例如 \"core.*\"、\"app-?.log\"、\"[ab]*.conf\"",
				hintEn: `hint: globs support *, ? and [...] (not regular expressions); brackets must be closed, e.g. "core.*", "app-?.log", "[ab]*.conf"`}
		}
	}
	p.q.Names = append(p.q.Names, c)
	return nil
}
