from pathlib import Path

from PIL import Image

from app.rag.formats import ParsedBlock, ParsedDocument, PdfPageProfile
from scripts.pdf_visual_audit import audit_page_expectation, page_ink_ratio


def _document(profile: PdfPageProfile, *blocks: ParsedBlock) -> ParsedDocument:
    return ParsedDocument(
        blocks=tuple(blocks),
        media_type="application/pdf",
        page_profiles=(profile,),
    )


def test_page_ink_ratio_detects_blank_and_non_blank_images(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    filled = tmp_path / "filled.png"
    Image.new("RGB", (20, 20), "white").save(blank)
    image = Image.new("RGB", (20, 20), "white")
    image.putpixel((10, 10), (0, 0, 0))
    image.save(filled)

    assert page_ink_ratio(blank) == 0
    assert page_ink_ratio(filled) > 0


def test_toc_page_must_not_produce_blocks() -> None:
    profile = PdfPageProfile(1, 0, 0.0, 100, 0.5, 0, 0, "NORMAL", toc_detected=True)

    errors, human_review = audit_page_expectation(_document(profile), 1, "toc")

    assert errors == []
    assert human_review is False


def test_table_page_requires_structure_and_safe_continuation() -> None:
    profile = PdfPageProfile(1, 0, 0.0, 100, 0.5, 1, 0, "NORMAL")
    block = ParsedBlock(
        kind="TABLE",
        content="| 动作 | 组数 |\n|---|---|\n| 深蹲 | 3 |",
        source_page=1,
        metadata={
            "table_header_signature": "动作|组数",
            "table_column_count": 2,
            "table_continuation_status": "SINGLE_PAGE",
        },
    )

    errors, human_review = audit_page_expectation(_document(profile, block), 1, "table")

    assert errors == []
    assert human_review is False


def test_visual_page_is_explicitly_sent_to_human_review() -> None:
    profile = PdfPageProfile(
        1,
        1,
        0.9,
        80,
        0.1,
        0,
        1,
        "VISUAL_REVIEW_REQUIRED",
    )

    errors, human_review = audit_page_expectation(_document(profile), 1, "visual")

    assert errors == []
    assert human_review is True
