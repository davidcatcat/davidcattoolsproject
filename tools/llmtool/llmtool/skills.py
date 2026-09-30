"""Declarative runtime resources, read only from an explicit trusted root."""

from dataclasses import dataclass
from pathlib import Path
import os
import re
import stat

from .config import yaml_module
from .errors import LLMToolError

BUILTIN_ROOT = Path(__file__).with_name("skills")
SKILL_LIMIT = 64 * 1024


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        raise LLMToolError("skill name must use 1-64 lowercase letters, digits or hyphens")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    version: int
    instructions: str

    def __post_init__(self):
        validate_name(self.name)
        if type(self.version) is not int or self.version < 1:
            raise LLMToolError("skill version must be a positive integer")
        for value in (self.description, self.instructions):
            if not isinstance(value, str) or not value.strip() or any(
                ord(char) < 32 and char not in "\n\r\t" for char in value
            ) or "\x7f" in value:
                raise LLMToolError("skill description and body must be nonempty text")

    def metadata(self):
        return {"name": self.name, "description": self.description, "version": self.version}


def parse_skill(text):
    yaml = yaml_module()

    class Loader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise LLMToolError("skill YAML aliases are unsupported")
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise LLMToolError("skill keys must be unique strings")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise LLMToolError("skill requires YAML frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise LLMToolError("skill requires closed YAML frontmatter")
    try:
        data = yaml.load("".join(lines[1:end]), Loader=Loader)
    except (yaml.YAMLError, RecursionError, ValueError):
        raise LLMToolError("skill frontmatter is malformed") from None
    if not isinstance(data, dict) or set(data) != {"name", "description", "version"}:
        raise LLMToolError("skill requires name, description, version only; unsupported fields forbidden")
    return Skill(data["name"], data["description"], data["version"], "".join(lines[end + 1:]))


def discover_skills(root=BUILTIN_ROOT):
    """No cwd/global search. Callers supplying roots must trust them."""
    root = Path(root)
    try:
        if root.is_symlink() or not root.is_dir():
            raise LLMToolError("skill root must be a regular directory")
        skills = {}
        for directory in sorted(root.iterdir()):
            validate_name(directory.name)
            if directory.is_symlink() or not directory.is_dir():
                raise LLMToolError("skill entry must be a regular directory")
            path = directory / "SKILL.md"
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                raise LLMToolError("skill must be a regular file")
            with path.open("rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise LLMToolError("skill must be a regular file")
                raw = stream.read(SKILL_LIMIT + 1)
            if len(raw) > SKILL_LIMIT:
                raise LLMToolError("skill exceeds 64 KiB")
            skill = parse_skill(raw.decode("utf-8"))
            if skill.name in skills:
                raise LLMToolError("duplicate skill name")
            skills[skill.name] = skill
        return tuple(skills[name] for name in sorted(skills))
    except (OSError, UnicodeError):
        raise LLMToolError("cannot read skill resource; details omitted") from None


def load_skill(name, root=BUILTIN_ROOT):
    validate_name(name)
    for skill in discover_skills(root):
        if skill.name == name:
            return skill
    raise LLMToolError("unknown skill")
