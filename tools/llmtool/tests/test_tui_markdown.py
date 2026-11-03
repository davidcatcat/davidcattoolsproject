"""W56b+c pure Markdown/table/code/diff renderer: tokens, wrapping and degrade."""
import gc
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import tui_markdown as md
from llmtool.tui_markdown import display_width, render, render_document


def plain(line):
    return "".join(text for text, _ in line)


def tokens_of(lines, token):
    return [text for line in lines for text, kind in line if kind == token]


class RenderContractTests(unittest.TestCase):
    def assert_fits(self, lines, width):
        for line in lines:
            self.assertLessEqual(display_width(plain(line)), width, plain(line))
            for text, token in line:
                self.assertIsInstance(text, str)
                self.assertIn(token, md.TOKENS)
                self.assertTrue(text)

    def test_shape_tokens_and_purity(self):
        source = "# 標題\n- a **b**\n\n| x | y |\n|---|---|\n| 1 | 2 |\n```python\nx = 1\n```"
        first = render(source, 30)
        self.assertEqual(first, render(source, 30))
        self.assert_fits(first, 30)
        self.assertNotIn("curses", sys.modules.get("llmtool.tui_markdown").__dict__)
        self.assertEqual(render("", 20), [[]])
        self.assertEqual(render("a\n\nb", 20), [[("a", "text")], [], [("b", "text")]])

    def test_every_width_fits_for_mixed_document(self):
        source = SAMPLE_DOCUMENT
        for width in (4, 7, 12, 20, 33, 80):
            self.assert_fits(render(source, width), width)


class InlineTests(unittest.TestCase):
    def test_inline_styles(self):
        line = render("x **bold** *it* `co*de*` [site](https://e.x)", 80)[0]
        self.assertEqual(line, [
            ("x ", "text"), ("bold", "md.bold"), (" ", "text"), ("it", "md.italic"), (" ", "text"),
            ("co*de*", "md.code"), (" ", "text"), ("site (https://e.x)", "md.link")])

    def test_nested_styles_innermost_wins(self):
        self.assertEqual(render("**a *b* c**", 80)[0],
                         [("a ", "md.bold"), ("b", "md.italic"), (" c", "md.bold")])
        self.assertEqual(render("*a **b** c*", 80)[0],
                         [("a ", "md.italic"), ("b", "md.bold"), (" c", "md.italic")])
        self.assertEqual(render("**a *b***", 80)[0], [("a ", "md.bold"), ("b", "md.italic")])
        self.assertEqual(render("***x***", 80)[0], [("x", "md.bold")])
        self.assertEqual(render("**`x`**", 80)[0], [("x", "md.code")])

    def test_literal_markers(self):
        self.assertEqual(plain(render("2 * 3 * 4 and snake_case_name", 80)[0]),
                         "2 * 3 * 4 and snake_case_name")
        self.assertEqual(render(r"\*not\* it", 80)[0], [("*not* it", "text")])
        self.assertEqual(plain(render("**open and `tick", 80)[0]), "**open and `tick")

    def test_bare_url_is_link(self):
        line = render("see https://example.com/a, ok", 80)[0]
        self.assertIn(("https://example.com/a", "md.link"), line)


class BlockTests(unittest.TestCase):
    def test_heading_bullets_and_hanging_indent(self):
        lines = render("## 標題\n- item one two three four\n  * sub\n3. third", 14)
        self.assertEqual(lines[0], [("▍ 標題", "md.heading")])
        self.assertEqual(lines[1][0], ("• ", "md.bullet"))
        self.assertTrue(plain(lines[2]).startswith("  "))
        self.assertIn(("◦ ", "md.bullet"), [s for line in lines for s in line])
        self.assertIn(("3. ", "md.bullet"), lines[-1])

    def test_quote(self):
        lines = render("> quoted **x** words wrap here\n> > deep", 16)
        self.assertEqual(lines[0][0], ("│ quoted ", "md.quote"))
        self.assertIn(("x", "md.bold"), lines[0])
        self.assertTrue(all(plain(line).startswith("│ ") for line in lines))
        self.assertTrue(plain(lines[-1]).startswith("│ │ deep"))


class WrapTests(unittest.TestCase):
    def test_cjk_boundaries_and_punctuation(self):
        lines = render("中文字串需要換行，句號。", 8)
        self.assertEqual([plain(line) for line in lines], ["中文字串", "需要換", "行，句", "號。"])
        for line in lines:
            self.assertLessEqual(display_width(plain(line)), 8)
            self.assertFalse(plain(line).startswith(("，", "。")))
        self.assertEqual("".join(plain(line) for line in lines), "中文字串需要換行，句號。")

    def test_words_break_at_spaces_and_long_words_split(self):
        lines = render("alpha beta gamma", 11)
        self.assertEqual([plain(line) for line in lines], ["alpha beta", "gamma"])
        lines = render("abcdefghijklmnop", 5)
        self.assertEqual([plain(line) for line in lines], ["abcde", "fghij", "klmno", "p"])

    def test_wide_char_never_split_and_combining_kept(self):
        lines = render("a中b", 2)
        self.assertEqual([plain(line) for line in lines], ["a", "中", "b"])
        self.assertEqual(display_width("é中"), 3)
        self.assertEqual(plain(render("é" * 3, 2)[1]), "é")

    def test_control_characters_are_neutralised(self):
        self.assertNotIn("\x1b", plain(render("a\x1b[31mb", 40)[0]))


class TableTests(unittest.TestCase):
    SOURCE = "| 名稱 | Value |\n|:--|--:|\n| 甲乙 | 1 |\n| x | 22 |"

    def test_aligned_borders_with_cjk_widths(self):
        lines = render(self.SOURCE, 40)
        text = [plain(line) for line in lines]
        self.assertEqual(text, [
            "┌──────┬───────┐",
            "│ 名稱 │ Value │",
            "├──────┼───────┤",
            "│ 甲乙 │     1 │",
            "│ x    │    22 │",
            "└──────┴───────┘",
        ])
        self.assertEqual({display_width(t) for t in text}, {16})
        self.assertIn(" 名稱 ", tokens_of(lines[1:2], "md.table.header"))
        self.assertIn("│", tokens_of(lines, "md.table.border"))
        self.assertTrue(tokens_of(lines[3:], "md.table.cell"))

    def test_over_width_wraps_cells_without_losing_characters(self):
        source = ("| 欄 | 說明 |\n|---|---|\n| a | 這是一段非常長的中文說明文字，需要在表格裡面換行 |\n"
                  "| b | averyveryverylongtokenwithoutspaces |")
        lines = render(source, 20)
        widths = {display_width(plain(line)) for line in lines}
        self.assertEqual(widths, {20})
        joined = "".join(plain(line) for line in lines)
        for chunk in ("這是一段非常長的中文說明文字", "需要在表格裡面換行", "averyveryve"):
            squashed = joined.replace("│", "").replace(" ", "")
            self.assertTrue(all(char in squashed for char in chunk))
        body = "".join(text for line in lines for text, token in line
                       if token == "md.table.cell").replace(" ", "")
        self.assertEqual(body, "a這是一段非常長的中文說明文字，需要在表格裡面換行"
                               "baveryveryverylongtokenwithoutspaces")

    def test_too_narrow_degrades_to_list(self):
        lines = render("| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |", 8)
        self.assertIn("a: 1", [plain(line) for line in lines])
        for line in lines:
            self.assertLessEqual(display_width(plain(line)), 8)

    def test_ragged_rows_and_escaped_pipe(self):
        lines = render("| a | b |\n|---|---|\n| 1 |\n| x \\| y | 2 | extra |", 40)
        text = "\n".join(plain(line) for line in lines)
        self.assertIn("x | y", text)
        self.assertIn("2 | extra", text)


class CodeBlockTests(unittest.TestCase):
    def test_border_label_no_markdown_and_continuation(self):
        lines = render("```text\n**not bold** # x\n" + "y" * 30 + "\n```", 20)
        self.assertEqual(lines[0], [("┌─ ", "md.codeblock.border"), ("text", "md.codeblock.lang"),
                                    (" " + "─" * 12, "md.codeblock.border")])
        self.assertEqual(lines[1], [("│ ", "md.codeblock.border"), ("**not bold** # x", "md.codeblock")])
        self.assertEqual(lines[2][0], ("│ ", "md.codeblock.border"))
        self.assertEqual(lines[3][0], ("│↪", "md.codeblock.border"))
        self.assertEqual(plain(lines[2]) + plain(lines[3])[2:], "│ " + "y" * 30)
        self.assertTrue(plain(lines[-1]).startswith("└"))

    def test_unclosed_fence_runs_to_end(self):
        lines = render("```\n# not heading", 20)
        self.assertEqual(lines[1], [("│ ", "md.codeblock.border"), ("# not heading", "md.codeblock")])

    def test_python(self):
        lines = render('```python\ndef f(x=1):  # c\n    return len("s"), None\n```', 60)
        self.assertIn(("def", "syntax.keyword"), lines[1])
        self.assertIn(("1", "syntax.number"), lines[1])
        self.assertIn(("# c", "syntax.comment"), lines[1])
        self.assertIn(("len", "syntax.builtin"), lines[2])
        self.assertIn(('"s"', "syntax.string"), lines[2])
        self.assertIn(("None", "syntax.keyword"), lines[2])

    def test_python_multiline_and_broken_source(self):
        lines = render('```py\nx = """a\nb"""\ny = (\n```', 40)
        self.assertIn(('"""a', "syntax.string"), lines[1])
        self.assertIn(('b"""', "syntax.string"), lines[2])
        self.assertEqual(plain(lines[3]), "│ y = (")

    def test_json(self):
        lines = render('```json\n{"k": [1, -2.5e3, true, null, "v"]}\n```', 60)
        line = lines[1]
        self.assertIn(('"k"', "syntax.string"), line)
        self.assertIn(("-2.5e3", "syntax.number"), line)
        self.assertIn(("true", "syntax.keyword"), line)
        self.assertIn(("null", "syntax.keyword"), line)

    def test_shell(self):
        lines = render("```bash\nif [ -n \"$HOME\" ]; then echo 'hi' 42 # note\nfi\n```", 80)
        line = lines[1]
        self.assertIn(("if", "syntax.keyword"), line)
        self.assertIn(('"$HOME"', "syntax.string"), line)
        self.assertIn(("echo", "syntax.builtin"), line)
        self.assertIn(("42", "syntax.number"), line)
        self.assertIn(("# note", "syntax.comment"), line)
        self.assertEqual(render("```sh\nfi\n```", 20)[1][1], ("fi", "syntax.keyword"))

    def test_unknown_language_is_plain(self):
        self.assertEqual(render("```rust\nfn main() {}\n```", 40)[1][1], ("fn main() {}", "md.codeblock"))


DIFF = """diff --git a/f.py b/f.py
index 111..222 100644
--- a/f.py
+++ b/f.py
@@ -10,3 +10,4 @@ def f():
 keep
-old
+new
+added
 tail
\\ No newline at end of file"""


class DiffTests(unittest.TestCase):
    def test_tokens_and_line_numbers(self):
        lines = render(DIFF, 60)
        self.assertEqual(lines[0], [("      ", "diff.lineno"), ("diff --git a/f.py b/f.py", "diff.meta")])
        self.assertEqual(lines[4][1], ("@@ -10,3 +10,4 @@ def f():", "diff.hunk"))
        self.assertEqual(lines[5], [("10 10 ", "diff.lineno"), (" keep", "text")])
        self.assertEqual(lines[6], [("11    ", "diff.lineno"), ("-old", "diff.del")])
        self.assertEqual(lines[7], [("   11 ", "diff.lineno"), ("+new", "diff.add")])
        self.assertEqual(lines[8], [("   12 ", "diff.lineno"), ("+added", "diff.add")])
        self.assertEqual(lines[9], [("12 13 ", "diff.lineno"), (" tail", "text")])
        self.assertEqual(lines[10][1][1], "diff.meta")

    def test_fenced_and_bare_hunk_and_mid_text(self):
        fenced = render("```diff\n@@ -1 +1 @@\n-a\n+b\n```", 30)
        self.assertEqual(fenced[1], [("│ ", "md.codeblock.border"), ("    ", "diff.lineno"),
                                     ("@@ -1 +1 @@", "diff.hunk")])
        self.assertEqual(fenced[2][1:], [("1   ", "diff.lineno"), ("-a", "diff.del")])
        bare = render("--- a\n+++ b\n@@ -5,1 +7,1 @@\n-x\n+y", 30)
        self.assertEqual(bare[3], [("5   ", "diff.lineno"), ("-x", "diff.del")])
        self.assertEqual(bare[4], [("  7 ", "diff.lineno"), ("+y", "diff.add")])
        mixed = render("Here:\n" + DIFF + "\n\n- after", 60)
        self.assertEqual(mixed[0], [("Here:", "text")])
        self.assertEqual(mixed[-1][0], ("• ", "md.bullet"))

    def test_long_line_wraps_under_gutter(self):
        lines = render("@@ -1 +1 @@\n+" + "z" * 30, 20)
        self.assertEqual(lines[2][0], ("    ", "diff.lineno"))
        self.assertEqual(lines[2][1][1], "diff.add")

    def test_collapse_long_diff(self):
        body = "\n".join(f"+line {n}" for n in range(1, 120))
        source = "@@ -0,0 +1,119 @@\n" + body
        result = render_document(source, 60)
        self.assertEqual(result.collapsed, 120 - 50)
        self.assertEqual(len(result.lines), 51)
        self.assertEqual(result.lines[40][-1], ("… 已收合 70 行", "diff.meta"))
        self.assertEqual(result.lines[39][0], ("     39 ", "diff.lineno"))
        self.assertEqual(result.lines[-1], [("    119 ", "diff.lineno"), ("+line 119", "diff.add")])
        full = render_document(source, 60, collapse=False)
        self.assertEqual((full.collapsed, len(full.lines)), (0, 120))
        self.assertEqual(render_document(DIFF, 60).collapsed, 0)


class MalformedInputTests(unittest.TestCase):
    def test_never_raises(self):
        samples = [
            None, 12, "", "\n\n", "**", "*" * 500, "_" * 50 + "x", "[" * 200, "`" * 7,
            "| a |\n|---|", "|\n|", "| a | b |\n|-|-|\n" + "|" * 50, "```", "~~~py\n'''", "```json\n\"",
            "@@ -x +y @@\n+a", "@@ -1,999999 +1 @@\n" + "-x\n" * 3, "> " * 100,
            "**" + "*a " * 300, "\x00\x07\x1b", "\ud800", "# " * 40, "1." * 30, "\t\tcode\t",
            "[a](b" * 50, "https://", "**_**_**_" * 80,
        ]
        for sample in samples:
            for width in (0, 1, 3, 40):
                with self.subTest(sample=repr(sample)[:40], width=width):
                    lines = render(sample, width)
                    self.assertIsInstance(lines, list)

    def test_bad_width_is_tolerated(self):
        self.assertEqual(render("abc", "x"), [[("abc", "text")]])


SAMPLE_DOCUMENT = "\n".join([
    "# 標題 **粗體** 與 *斜體*",
    "一段中文段落，包含 `inline code` 與 [連結](https://example.com/path) 以及很長的句子需要換行。",
    "- bullet one with some words",
    "  - 子項目 nested",
    "> quote line",
    "| 欄位 | 說明 | n |",
    "|:--|---|--:|",
    "| 名稱 | 一些文字 | 12 |",
    "```python",
    "def handler(event):  # comment",
    "    return {\"ok\": True, \"n\": 42}",
    "```",
    "```json",
    "{\"a\": [1, 2, null]}",
    "```",
    "```bash",
    "for f in *.txt; do echo \"$f\"; done",
    "```",
    "",
])


class PerformanceTests(unittest.TestCase):
    def test_two_thousand_lines_under_half_second(self):
        lines = SAMPLE_DOCUMENT.split("\n")
        source = "\n".join((lines * (2000 // len(lines) + 1))[:2000])
        self.assertEqual(source.count("\n"), 1999)
        render(source[:2000], 80)   # warm caches
        start = time.perf_counter()
        rendered = render(source, 80)
        elapsed = time.perf_counter() - start
        self.assertGreater(len(rendered), 2000)
        self.assertLess(elapsed, 0.5, f"2,000-line render took {elapsed:.3f}s")


ADVERSARIAL = {
    "10,000 [": "[" * 10000,
    "10,000 *": "*" * 10000,
    "10,000 backticks": "`" * 10000,
    "10,000 inline backticks": "x " + "` ``" * 2500,
    "5,000 | in one row": "| a | b |\n|---|---|\n" + "|" * 5000,
    "5,000 | header": "|" * 5000 + "\n|---|---|\n| a | b |",
    "1,000 nested >": "> " * 1000 + "x",
    "1,000 bare >": ">" * 1000,
    "*a repeated": "*a " * 3000,
    "[x]( repeated": "[x](" * 2500,
    "# then spaces": "# " + " " * 10000 + "x",
    "# then hashes": "# " + "#" * 10000 + "x",
    "fence with tick": "```" + "a" * 10000 + "`",
}


def best_time(source, repeat=3):
    best = float("inf")
    enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(repeat):
            start = time.perf_counter()
            render(source, 80)
            best = min(best, time.perf_counter() - start)
    finally:
        if enabled:
            gc.enable()
    return best


class AdversarialInputTests(unittest.TestCase):
    def test_each_adversarial_input_under_fifth_of_a_second(self):
        for name, source in ADVERSARIAL.items():
            with self.subTest(name):
                elapsed = best_time(source)
                self.assertLess(elapsed, 0.2, f"{name}: {elapsed:.3f}s")

    def test_time_grows_linearly(self):
        def workload(n):
            return "\n".join([
                "[" * n, "*a " * (n // 3), "x " + "` ``" * (n // 4), "_a " * (n // 3),
                "| a | b |", "|---|---|", "|" * n, "", "> " * (n // 20) + "x", "# " + "#" * n,
            ])
        best_time(workload(1000))   # warm caches
        small, large = best_time(workload(8000), 5), best_time(workload(16000), 5)
        # Doubling input should stay within a linear ratio, with a fixed
        # scheduling floor so sub-millisecond jitter does not dominate.
        self.assertLessEqual(large, 3 * small + 0.05, f"n: {small:.4f}s, 2n: {large:.4f}s")

    def test_degraded_output_keeps_every_character(self):
        self.assertEqual("".join(plain(line) for line in render("[" * 300, 80)), "[" * 300)
        self.assertEqual("".join(plain(line) for line in render("*a " * 50, 400)).split(),
                         ("*a " * 50).split())
        nested = render("> > > deep", 40)[0]
        self.assertEqual(nested, [("│ │ │ deep", "md.quote")])
        self.assertTrue(plain(render("> " * 1000 + "x", 20)[0]).endswith("x"))

    def test_block_rules_after_regex_rewrite(self):
        self.assertEqual(render("## Title ##", 40)[0], [("▍ Title", "md.heading")])
        self.assertEqual(render("# C# #", 40)[0], [("▍ C#", "md.heading")])
        self.assertEqual(render("#", 40)[0], [("▍ ", "md.heading")])
        self.assertEqual(plain(render("#hashtag", 40)[0]), "#hashtag")
        self.assertEqual(plain(render("```a`b", 40)[0]), "```a`b")
        self.assertEqual(render("~~~ sh x\nfi\n~~~", 40)[0][1], ("sh", "md.codeblock.lang"))
        self.assertTrue(plain(render("| a |\n|:-:|\n| 1 |", 40)[0]).startswith("┌"))
        self.assertEqual(plain(render("| a |\n| x |", 40)[0]), "| a |")
        self.assertEqual(render("[a [b](u)", 40)[0], [("[a ", "text"), ("b (u)", "md.link")])


if __name__ == "__main__":
    unittest.main()
