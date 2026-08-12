"""为本地健身知识库原始资料生成可审计清单。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = SERVICE_ROOT / "data" / "knowledge" / "raw"
MANIFEST_PATH = SERVICE_ROOT / "data" / "knowledge" / "manifest.json"

READ_ROLES = ["SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "COACH", "STUDENT"]
REFERENCE_ONLY_FILES = frozenset(
    {
        "Physical_Activity_Guidelines_2nd_edition.pdf",
        "Physical_Activity_Guidelines_2nd_edition_Presentation.pdf",
    }
)

# 来源 URL 固定记录在清单中，避免后续重新导入时丢失来源链路。
SOURCE_URLS = {
    "全民健身指南.docx": "https://www.sport.gov.cn/n315/n20067006/c20324479/content.html",
    "《全民健身指南》解读_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n10503/c819330/content.html"
    ),
    "《运动处方中国专家共识（2023）》发布——用好运动处方 运动也是良医_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20067664/c25517089/content.html"
    ),
    "居家健身不能少了运动处方_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c24905714/content.html"
    ),
    "弹力带缓解腰肌劳损_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c27152575/content.html"
    ),
    "日常注重平衡力锻炼_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c27318507/content.html"
    ),
    "科学练下蹲 保护膝关节 _国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c27384456/content.html"
    ),
    "肌肉耐力“升级”指南_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c28852273/content.html"
    ),
    "动出健康 护好肾脏_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c29063394/content.html"
    ),
    "你的体重，可能“骗”了你_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c29298798/content.html"
    ),
    "适度运动平稳守护血压健康_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c29684098/content.html"
    ),
    "高温运动防护四注意_国家体育总局.pdf": (
        "https://www.sport.gov.cn/n20001280/n20001265/n20066978/c29789013/content.html"
    ),
    "高尿酸血症营养和运动指导原则（2024年版）.pdf": (
        "https://www.nhc.gov.cn/ylyjs/gzdt/202407/256b4eb8398440a8811344c7be50a333.shtml"
    ),
    "高脂血症营养和运动指导原则（2024年版）.pdf": (
        "https://www.nhc.gov.cn/ylyjs/gzdt/202407/256b4eb8398440a8811344c7be50a333.shtml"
    ),
    "高血压营养和运动指导原则（2024年版）.pdf": (
        "https://www.nhc.gov.cn/ylyjs/gzdt/202407/256b4eb8398440a8811344c7be50a333.shtml"
    ),
    "高血糖症营养和运动指导原则（2024年版）.pdf": (
        "https://www.nhc.gov.cn/ylyjs/gzdt/202407/256b4eb8398440a8811344c7be50a333.shtml"
    ),
    "WHO guidelines on physical activity and sedentary behaviour.pdf": (
        "https://www.who.int/publications/b/55518"
    ),
    "Physical_Activity_Guidelines_2nd_edition.pdf": (
        "https://odphp.health.gov/our-work/nutrition-physical-activity/physical-activity-guidelines/current-guidelines"
    ),
    "Physical_Activity_Guidelines_2nd_edition_Presentation.pdf": (
        "https://odphp.health.gov/our-work/nutrition-physical-activity/physical-activity-guidelines/current-guidelines"
    ),
    "Physical_Activity_Guidelines_2nd_edition_Presentation.pptx": (
        "https://odphp.health.gov/our-work/nutrition-physical-activity/physical-activity-guidelines/current-guidelines"
    ),
    "PAG_ExecutiveSummary.pdf": (
        "https://odphp.health.gov/our-work/nutrition-physical-activity/physical-activity-guidelines/current-guidelines"
    ),
}


def sha256(path: Path) -> str:
    """以分块方式计算文件哈希，避免大文件一次性读入内存。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metadata_for(category: str, file_name: str, suffix: str) -> dict[str, Any]:
    """根据资料主题给出保守的初始审核元数据，最终状态仍需人工确认。"""

    risk_level = "NORMAL"
    document_type = "FITNESS_GUIDE"
    source_authority = "国家体育总局"
    requires_human_review = False
    license_note = "按来源网站版权政策使用，当前仅用于本地知识库测试"

    if category == "training-and-exercise":
        risk_level = "CAUTION"
        document_type = "TRAINING_GUIDE"
    elif category == "exercise-safety":
        risk_level = "CAUTION"
        document_type = "EXERCISE_SAFETY"
        if "腰肌劳损" in file_name or "血压" in file_name:
            risk_level = "MEDICAL"
            requires_human_review = True
    elif category == "weight-management":
        risk_level = "CAUTION"
        document_type = "WEIGHT_MANAGEMENT"
        requires_human_review = True
    elif category == "medical-guidelines":
        risk_level = "MEDICAL"
        document_type = "MEDICAL_EXERCISE_GUIDELINE"
        requires_human_review = True
        source_authority = "国家卫生健康委员会"
    elif category == "international-guidelines":
        document_type = "PHYSICAL_ACTIVITY_GUIDELINE"
        source_authority = "WHO" if file_name.startswith("WHO") else "U.S. HHS ODPHP"
        license_note = "按 WHO 或 U.S. HHS ODPHP 来源页面的版权和署名要求使用"
        if file_name in REFERENCE_ONLY_FILES:
            license_note = "仅作为人工参考；当前 PDF 文本层存在版式噪声，暂不进入 RAG"
    elif category == "reference-not-indexed":
        document_type = "REFERENCE_PRESENTATION"
        source_authority = "U.S. HHS ODPHP"
        license_note = "仅作为人工参考，当前解析器不直接入库"

    # reference-not-indexed 目录中的文件只保留人工参考，即使扩展名本身受支持，
    # 也不能因为“能解析”就自动进入 RAG，避免版式复杂的演示文稿污染检索库。
    indexable = (
        file_name not in REFERENCE_ONLY_FILES
        and category != "reference-not-indexed"
        and suffix
        in {
            ".pdf",
            ".docx",
            ".xlsx",
            ".md",
            ".markdown",
            ".txt",
        }
    )
    source_url = SOURCE_URLS.get(file_name)
    if source_url is None:
        raise ValueError(f"未配置来源 URL：{file_name}")
    return {
        "category": category,
        "document_type": document_type,
        "risk_level": risk_level,
        "requires_human_review": requires_human_review,
        "visibility": "GLOBAL",
        "allowed_roles": READ_ROLES,
        "organization_id": None,
        "source_authority": source_authority,
        "source_url": source_url,
        "source_license": license_note,
        "indexable": indexable,
        "status": "PENDING_REVIEW" if indexable else "REFERENCE_ONLY",
    }


def build_manifest() -> dict[str, Any]:
    """扫描 raw 目录并生成稳定排序的资料清单。"""

    if not RAW_ROOT.exists():
        raise SystemExit(f"原始资料目录不存在：{RAW_ROOT}")

    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in RAW_ROOT.rglob("*") if item.is_file()):
        relative_path = path.relative_to(SERVICE_ROOT).as_posix()
        category = path.parent.name
        suffix = path.suffix.lower()
        entry = {
            "relative_path": relative_path,
            "file_name": path.name,
            "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
            **metadata_for(category, path.name, suffix),
        }
        entries.append(entry)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "raw_root": "data/knowledge/raw",
        "entry_count": len(entries),
        "entries": entries,
    }


def main() -> None:
    """生成 JSON 文件，并在终端打印摘要。"""

    manifest = build_manifest()
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    indexable_count = sum(1 for item in manifest["entries"] if item["indexable"])
    review_count = sum(1 for item in manifest["entries"] if item["requires_human_review"])
    print(
        f"已生成 {MANIFEST_PATH}：共 {manifest['entry_count']} 份，"
        f"可解析 {indexable_count} 份，需人工审核 {review_count} 份。"
    )


if __name__ == "__main__":
    main()
