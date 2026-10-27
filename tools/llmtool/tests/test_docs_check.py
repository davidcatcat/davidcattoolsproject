import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "docs_check.py"
SPEC = importlib.util.spec_from_file_location("docs_check", SCRIPT)
docs_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docs_check)


class DocsCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.original = docs_check.REPO
        docs_check.REPO = self.repo
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        (self.repo / "index.md").write_text("# Base\n\nunchanged pass" + "word: old-value\n", encoding="utf-8")
        self.commit()
        self.base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()

    def tearDown(self):
        docs_check.REPO = self.original
        self.temp.cleanup()

    def commit(self):
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.repo, check=True)

    def findings(self, body):
        (self.repo / "index.md").write_text(body, encoding="utf-8")
        self.commit()
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        return docs_check.check(self.base, head)

    def test_broken_relative_link(self):
        self.assertTrue(any("broken relative link" in item for item in self.findings("[bad](missing.md)\n")))

    def test_missing_anchor(self):
        (self.repo / "other.md").write_text("# Present\n", encoding="utf-8")
        self.assertTrue(any("missing anchor" in item for item in self.findings("[bad](other.md#absent)\n")))

    def test_heading_and_explicit_anchor(self):
        (self.repo / "other.md").write_text("# Good Heading\n<a id=\"explicit\"></a>\n", encoding="utf-8")
        self.assertFalse(self.findings("[heading](other.md#good-heading)\n[html](other.md#explicit)\n"))

    def test_sensitive_added_line_only(self):
        self.assertFalse(self.findings("unchanged pass" + "word: old-value\n"))
        self.assertTrue(any("sensitive-content" in item for item in self.findings("pass" + "word: newly-added\n")))

    def test_bad_skill_frontmatter(self):
        skill = self.repo / "skills" / "sample" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: sample\ndescription: ''\n---\n", encoding="utf-8")
        self.commit()
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        self.assertTrue(any("description must be non-empty" in item for item in docs_check.check(self.base, head)))

    def test_clean_docs_pass(self):
        (self.repo / "other.md").write_text("# Good\n", encoding="utf-8")
        self.assertFalse(self.findings("[good](other.md#good)\n"))

    def test_link_inside_fenced_code_is_ignored(self):
        self.assertFalse(self.findings("```md\n[example](missing.md)\n```\n"))


if __name__ == "__main__":
    unittest.main()
