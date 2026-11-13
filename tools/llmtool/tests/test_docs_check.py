import hashlib
import importlib.util
import json
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
        (self.repo / "AGENTS.md").write_text("guidance\n", encoding="utf-8")
        (self.repo / "CLAUDE.md").write_text("guidance\n", encoding="utf-8")
        hashes = {
            name: hashlib.sha256((self.repo / name).read_bytes()).hexdigest()
            for name in ("AGENTS.md", "CLAUDE.md")
        }
        config = self.repo / "tools" / "llmtool" / "guidance-drift.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"schema_version": 1, "hashes": hashes, "codex_only": []}), encoding="utf-8")
        # Minimal valid model allowlist (#1502 WP-G); the guidance check reads it
        # from the checked repository root. The model name is fictional.
        (config.parent / "developer-routes.json").write_text(json.dumps({
            "schema_version": 1,
            "routes": {"fixture": {
                "default_model": "gpt-6-luna",
                "allowed_models": ["gpt-6-luna"],
                "default_effort": "medium",
            }},
        }), encoding="utf-8")
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

    def test_guidance_drift_fails_docs_check(self):
        original = docs_check.guidance_drift_check.check
        docs_check.guidance_drift_check.check = lambda _repo: ["AGENTS.md: HASH_STALE stale"]
        try:
            self.assertTrue(any("HASH_STALE" in item for item in self.findings("# Good\n")))
        finally:
            docs_check.guidance_drift_check.check = original

    def test_checked_root_without_model_allowlist_fails_closed(self):
        (self.repo / "tools" / "llmtool" / "developer-routes.json").unlink()
        self.assertIn(
            "tools/llmtool/developer-routes.json: MODEL_ALLOWLIST_UNAVAILABLE FileNotFoundError",
            self.findings("# Good\n"),
        )

    def test_model_check_uses_checked_root_allowlist(self):
        skill = self.repo / "skills" / "sample" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: sample\ndescription: d\n---\nUse `gpt-9-wrong`.\n", encoding="utf-8")
        self.assertIn("skills/sample/SKILL.md: MODEL_NOT_ALLOWED gpt-9-wrong", self.findings("# Good\n"))

    def test_link_inside_fenced_code_is_ignored(self):
        self.assertFalse(self.findings("```md\n[example](missing.md)\n```\n"))


if __name__ == "__main__":
    unittest.main()
