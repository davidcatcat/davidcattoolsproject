package query

import "time"

// Phrases are space-separated token texts. Chinese phrases are usually a
// single dictionary token; every Chinese word used here is registered in
// cjkDict by init so the tokenizer can split unspaced Chinese text.

var ignoreCasePhrases = []string{"ignore case", "ignoring case", "case insensitive", "case-insensitive",
	"不分大小寫", "不分大小写", "忽略大小寫", "忽略大小写", "不區分大小寫", "不区分大小写"}

var contentPhrases = []string{"containing", "contains", "contain", "with text", "with content",
	"with the text", "that contain", "that contains", "grep",
	"包含", "包括", "含有", "內容有", "内容有", "內容包含", "内容包含", "內容含有", "内容含有",
	"裡面有", "里面有", "關鍵字", "关键字", "內容", "内容", "有關鍵字", "有关键字"}

var excludePhrases = []string{"exclude", "excluding", "except", "except for", "skip", "skipping",
	"ignore", "ignoring", "not in", "outside of", "without",
	"排除", "不要", "忽略", "跳過", "跳过", "除了", "不包括", "不含目錄", "不含目录"}

var excludeTail = []string{"dir", "dirs", "directory", "directories", "folder", "folders",
	"目錄", "目录", "資料夾", "资料夹", "文件夾", "文件夹", "以外", "之外"}

var listSeps = []string{",", "、", "，", "and", "or", "和", "及", "與", "与", "跟", "或"}

var limitPhrases = []string{"top", "first", "limit", "limit to", "max", "only",
	"最多", "前", "只要", "只列", "只列出", "只顯示", "只显示", "最多列出", "最多顯示", "最多显示"}

var largestPhrases = []string{"largest", "biggest", "the largest", "the biggest", "最大", "最大的"}
var newestPhrases = []string{"newest", "latest", "most recent", "most recently modified",
	"the newest", "the latest", "recently modified", "recently changed", "most recently changed",
	"最新", "最新的", "最近修改", "最近修改的", "最近更新", "最近更新的", "最近改過的", "最近改过的"}

// Sort words accepted right after a limit number, e.g. "前10大", "top 5 largest".
var postNumLargest = []string{"大", "largest", "biggest"}
var postNumNewest = []string{"新", "newest", "latest"}

// Comparator phrases. The unit that follows decides whether the comparison
// is about size, modification time, or result count.
var cmpPhrases = map[string]string{
	"larger than": ">", "bigger than": ">", "greater than": ">", "more than": ">", "over": ">",
	"above": ">", "exceeding": ">", "at least": ">=", "no less than": ">=",
	"smaller than": "<", "less than": "<", "under": "<", "below": "<", "fewer than": "<",
	"at most": "<=", "up to": "<=", "no more than": "<=", "no larger than": "<=",
	"大於": ">", "大于": ">", "超過": ">", "超过": ">", "多於": ">", "多于": ">", "高於": ">", "高于": ">",
	"至少": ">=", "不少於": ">=", "不少于": ">=", "不小於": ">=", "不小于": ">=",
	"小於": "<", "小于": "<", "少於": "<", "少于": "<", "低於": "<", "低于": "<", "不到": "<",
	"不超過": "<=", "不超过": "<=", "不大於": "<=", "不大于": "<=",
}

// Time-only comparator phrases: "within" means newer than, "older" means before.
var withinPhrases = []string{"within", "within the last", "within the past", "in the last", "in the past",
	"in last", "in past", "during the last", "during the past", "last", "past", "the last", "the past",
	"newer than", "modified within", "changed within",
	"最近", "過去", "过去", "近"}
var olderPhrases = []string{"older than", "not modified in", "not changed in", "before"}

// Postfix phrases after a quantity, e.g. "10MB以上", "7天內", "3 days ago".
var postfixPhrases = map[string]string{
	"以上": ">=", "或以上": ">=", "or more": ">=", "or larger": ">=", "or bigger": ">=",
	"以下": "<=", "或以下": "<=", "or less": "<=", "or smaller": "<=",
	"以內": "within", "以内": "within", "內": "within", "内": "within", "之內": "within", "之内": "within",
	"前": "older", "之前": "older", "以前": "older", "ago": "older", "or older": "older",
}

var timeUnits = map[string]time.Duration{}
var sizeUnits = map[string]int64{}
var countUnits = []string{"file", "files", "result", "results", "item", "items", "match", "matches",
	"個", "个", "筆", "笔", "份", "檔", "档", "個檔案", "个档案", "個檔", "個文件", "个文件"}

const day = 24 * time.Hour

func init() {
	for _, w := range []string{"min", "mins", "minute", "minutes", "分鐘", "分钟", "分"} {
		timeUnits[w] = time.Minute
	}
	for _, w := range []string{"h", "hr", "hrs", "hour", "hours", "小時", "小时", "個小時", "个小时", "鐘頭", "钟头", "個鐘頭"} {
		timeUnits[w] = time.Hour
	}
	for _, w := range []string{"d", "day", "days", "天", "日"} {
		timeUnits[w] = day
	}
	for _, w := range []string{"w", "wk", "wks", "week", "weeks", "週", "周", "星期", "禮拜", "礼拜",
		"個星期", "个星期", "個禮拜", "个礼拜", "個週", "个周"} {
		timeUnits[w] = 7 * day
	}
	for _, w := range []string{"mo", "month", "months", "月", "個月", "个月"} {
		timeUnits[w] = 30 * day
	}
	for _, w := range []string{"y", "yr", "yrs", "year", "years", "年"} {
		timeUnits[w] = 365 * day
	}
	for _, w := range []string{"b", "byte", "bytes", "位元組", "字節", "字节"} {
		sizeUnits[w] = 1
	}
	for _, w := range []string{"k", "kb", "kib", "kilobyte", "kilobytes"} {
		sizeUnits[w] = 1 << 10
	}
	for _, w := range []string{"m", "mb", "mib", "megabyte", "megabytes", "兆"} {
		sizeUnits[w] = 1 << 20
	}
	for _, w := range []string{"g", "gb", "gib", "gigabyte", "gigabytes"} {
		sizeUnits[w] = 1 << 30
	}
	for _, w := range []string{"t", "tb", "tib", "terabyte", "terabytes"} {
		sizeUnits[w] = 1 << 40
	}

	lists := [][]string{ignoreCasePhrases, contentPhrases, excludePhrases, excludeTail, listSeps,
		limitPhrases, largestPhrases, newestPhrases, postNumLargest, postNumNewest, withinPhrases, olderPhrases, countUnits,
		fileNouns, extTriggers, extCopula, softContentPhrases, pathTriggers, fillers, todayPhrases, yesterdayPhrases, orPhrases, notPhrases}
	for _, l := range lists {
		addCJKFrom(l)
	}
	for w := range cmpPhrases {
		addCJKFrom([]string{w})
	}
	for w := range postfixPhrases {
		addCJKFrom([]string{w})
	}
	for w := range timeUnits {
		addCJKFrom([]string{w})
	}
	for w := range sizeUnits {
		addCJKFrom([]string{w})
	}
	for w := range extAliases {
		addCJKFrom([]string{w})
	}
}

// addCJKFrom registers phrases that consist of Chinese text only.
func addCJKFrom(phrases []string) {
	for _, p := range phrases {
		if isHanWord(p) {
			addCJK(p)
		}
	}
}

func isHanWord(s string) bool {
	for _, r := range s {
		if r < 0x80 {
			return false
		}
	}
	return s != ""
}

var todayPhrases = []string{"today", "今天", "今日"}
var yesterdayPhrases = []string{"yesterday", "昨天", "昨日"}

// Words that would change the meaning in ways v0.1 cannot express.
var orPhrases = []string{"or", "或", "或者", "或是"}
var notPhrases = []string{"not", "不包含", "不含", "沒有", "没有", "不是"}

var fileNouns = []string{"file", "files", "source", "sources", "code", "document", "documents", "docs",
	"檔", "檔案", "档", "档案", "文件", "文檔", "文档", "程式", "程式碼", "代碼", "代码", "原始碼", "源碼", "源码", "格式"}

var extTriggers = []string{"extension", "extensions", "ext", "with extension", "ending in", "ending with",
	"ends with", "副檔名", "副档名", "擴展名", "扩展名", "後綴", "后缀", "結尾", "结尾"}

// Optional copula after an extension trigger: "extension is .log", "副檔名是 .log".
var extCopula = []string{"is", "=", ":", "是", "為", "为"}

// "with TODO", "有 TODO 的檔案": content when followed by a free word or quoted text.
var softContentPhrases = []string{"with", "has", "have", "having", "that have", "that has", "有"}

var pathTriggers = []string{"in", "under", "inside", "at", "from", "within directory",
	"在", "於", "于", "從", "从"}

// extAliases maps language or category words to file extensions.
var extAliases = map[string][]string{
	"go": {"go"}, "golang": {"go"},
	"python": {"py"}, "py": {"py"},
	"javascript": {"js"}, "js": {"js"}, "typescript": {"ts"}, "ts": {"ts"},
	"markdown": {"md"}, "md": {"md"},
	"text": {"txt"}, "txt": {"txt"},
	"shell": {"sh"}, "sh": {"sh"}, "bash": {"sh"}, "zsh": {"zsh"},
	"yaml": {"yaml", "yml"}, "yml": {"yaml", "yml"},
	"json": {"json"}, "csv": {"csv"}, "xml": {"xml"}, "toml": {"toml"}, "sql": {"sql"},
	"html": {"html", "htm"}, "css": {"css"},
	"java": {"java"}, "c": {"c", "h"}, "cpp": {"cpp", "cc", "hpp"}, "c++": {"cpp", "cc", "hpp"},
	"rust": {"rs"}, "rs": {"rs"}, "swift": {"swift"}, "ruby": {"rb"}, "rb": {"rb"}, "php": {"php"},
	"log": {"log"}, "logs": {"log"},
	"pdf": {"pdf"}, "pdfs": {"pdf"},
	"word": {"docx", "doc"}, "docx": {"docx"}, "doc": {"doc"},
	"excel": {"xlsx", "xls"}, "xlsx": {"xlsx"}, "xls": {"xls"},
	"powerpoint": {"pptx", "ppt"}, "ppt": {"ppt"}, "pptx": {"pptx"},
	"zip": {"zip"}, "zips": {"zip"},
	"jpg": {"jpg", "jpeg"}, "jpeg": {"jpg", "jpeg"}, "png": {"png"}, "gif": {"gif"}, "heic": {"heic"},
	"mp4": {"mp4"}, "mov": {"mov"}, "mp3": {"mp3"},
	"image": imageExts, "images": imageExts, "picture": imageExts, "pictures": imageExts,
	"photo": imageExts, "photos": imageExts, "圖片": imageExts, "图片": imageExts, "照片": imageExts,
	"video": videoExts, "videos": videoExts, "影片": videoExts, "視頻": videoExts, "视频": videoExts,
	"audio": audioExts, "music": audioExts, "音樂": audioExts, "音乐": audioExts, "音檔": audioExts, "音档": audioExts,
}

var imageExts = []string{"jpg", "jpeg", "png", "gif", "heic", "webp", "bmp", "tiff"}
var videoExts = []string{"mp4", "mov", "m4v", "avi", "mkv"}
var audioExts = []string{"mp3", "m4a", "wav", "aac", "flac"}

// Aliases that are too ambiguous to mean an extension without a file noun
// ("c files" is fine, a lone "c" is not).
var aliasNeedsNoun = map[string]bool{"c": true, "text": true, "word": true, "shell": true, "code": true}

// fillers carry no meaning for the search and are skipped.
var fillers = []string{
	"find", "search", "search for", "look for", "looking for", "show", "show me", "list", "get", "give me",
	"i", "want", "need", "please", "me", "all", "any", "every", "the", "a", "an", "some", "my",
	"file", "files", "that", "which", "are", "is", "were", "was", "be", "been", "and", "with", "of", "for",
	"modified", "changed", "updated", "edited", "touched", "last modified", "ones", "results", "here",
	"recursively", "only", "whose", "having", "has", "have", "to", "in", "size", "sized", "where", "just",
	"找", "找出", "找到", "搜尋", "搜寻", "搜索", "搜", "查", "查找", "查詢", "查询", "列出", "顯示", "显示",
	"給我", "给我", "幫我", "帮我", "請", "请", "我", "要", "想", "想要", "所有", "全部", "任何",
	"的", "檔案", "檔", "文件", "档案", "档", "且", "並且", "并且", "而且", "並", "并", "同時", "同时", "和", "跟",
	"修改", "修改過", "修改过", "更新", "更新過", "更新过", "改過", "改过", "變更", "变更", "異動", "异动", "編輯", "编辑",
	"被", "過", "过", "裡", "裡面", "里", "里面", "中", "之中", "下", "底下", "下面",
	"目錄", "目录", "資料夾", "资料夹", "有", "是", "了", "一下", "一些", "哪些", "那些", "大小", "時間", "时间",
	"為", "为", "可以", "嗎", "吗", "呢", "吧", "以", "就好", "即可", "個", "个", "範圍", "范围", "都",
}
