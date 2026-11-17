"""Targeted safeguards for a known, review-pending movement guidance issue.

These rules do not establish medical correctness or detect all unsafe advice.
"""

from __future__ import annotations

import re
import unicodedata

# The observed documents applied this cue to specific exercises; without a
# reviewed scope, exclude the whole chunk rather than generalize the cue.
_KNEE = r"膝(?:盖|关节|部)?"
_FOOT = r"(?:脚|足|趾|足部)尖"
_RESTRICTION = r"(?:不要|不能|不应|不可|避免|禁止|不得|别|切勿)"
_CROSS = r"(?:超过|越过|超出|过)"
_UNIVERSAL_CUE = re.compile(
    rf"(?:{_KNEE}[^。！？；;]{{0,16}}{_RESTRICTION}[^。！？；;]{{0,8}}{_CROSS}"
    rf"[^。！？；;]{{0,8}}{_FOOT}|{_RESTRICTION}[^。！？；;]{{0,8}}{_KNEE}"
    rf"[^。！？；;]{{0,8}}{_CROSS}[^。！？；;]{{0,8}}{_FOOT})"
)
_UNIVERSAL_JUDGMENT = re.compile(
    rf"{_KNEE}[^。！？；;]{{0,8}}{_CROSS}[^。！？；;]{{0,8}}{_FOOT}"
    rf"[^。！？；;]{{0,10}}(?:就是|代表|说明|属于|即为|是(?!否))"
    rf"[^。！？；;]{{0,5}}(?:错误|不正确|不标准|危险|正常|安全|标准|正确)"
)
_INJURY_PROMISE = re.compile(
    r"(?:预防|防止|避免|杜绝)(?:运动中|运动|训练中|训练)?(?:出现|发生)?"
    r"(?:损伤|受伤|伤害|伤病)"
    r"|(?:能|能够|可以|有效|确保|保证)[^。！？；;]{0,5}保护"
    r"[^。！？；;]{0,5}(?:关节|膝盖|膝关节|腰椎|脊柱)"
)


def has_unreviewed_knee_cue(text: str) -> bool:
    """Flag a narrow set of absolute knee/toe cues, including OCR variants."""
    normalized = "".join(unicodedata.normalize("NFKC", text).split())
    return bool(_UNIVERSAL_CUE.search(normalized) or _UNIVERSAL_JUDGMENT.search(normalized))


def has_unverified_injury_claim(text: str) -> bool:
    """Flag a narrow set of unverified prevention/protection claims."""
    normalized = "".join(unicodedata.normalize("NFKC", text).split())
    return bool(_INJURY_PROMISE.search(normalized))
