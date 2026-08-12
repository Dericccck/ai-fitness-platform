"""所有文档解析器共用的文本规范化基础能力。"""

from __future__ import annotations

import re
import unicodedata

_FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL)


def clean_markdown(raw_content: str) -> str:
    """规范化文本，同时保留标题、项目符号、表格和段落结构。"""

    normalized = unicodedata.normalize("NFKC", raw_content).replace("\r\n", "\n")
    normalized = normalized.replace("\r", "\n").lstrip("\ufeff")
    normalized = _FRONT_MATTER.sub("", normalized, count=1)
    lines: list[str] = []
    previous_blank = False
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            if not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        lines.append(line)
        previous_blank = False
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        raise ValueError("document content must not be empty")
    return cleaned
