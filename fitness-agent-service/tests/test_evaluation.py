from app.rag.evaluation import (
    RetrievalEvalCase,
    RetrievalEvalThresholds,
    aggregate_results,
    case_from_mapping,
    evaluate_case,
)
from app.rag.evaluation_cli import main


def test_document_quality_calculates_noise_duplicates_tables_and_parents() -> None:
    from app.rag.document_quality import measure_document_quality
    from app.rag.formats import ParsedBlock
    from app.rag.ingestion import ChunkDraft

    blocks = [
        ParsedBlock(kind="TEXT", content="正文内容。" * 30, source_page=1),
        ParsedBlock(kind="TEXT", content="正文内容。" * 30, source_page=1),
        ParsedBlock(kind="TEXT", content="网站导航", source_page=1),
        ParsedBlock(
            kind="TABLE",
            content="| 动作 | 组数 |\n| --- | --- |\n| 深蹲 | 4 |",
            source_page=1,
        ),
    ]
    drafts = [ChunkDraft("正文内容。" * 30, (), "正文内容。" * 30)]

    metrics = measure_document_quality(blocks, drafts, total_pages=1)

    assert metrics.noise_rate == 0.25
    assert metrics.duplicate_rate == 0.25
    assert metrics.duplicate_glyph_block_count == 0
    assert metrics.parent_integrity == 1.0
    assert metrics.table_integrity == 1.0
    assert metrics.missing_pages == ()


def test_document_quality_thresholds_block_bad_metrics() -> None:
    from app.rag.document_quality import DocumentQualityThresholds, measure_document_quality
    from app.rag.formats import ParsedBlock

    metrics = measure_document_quality(
        [ParsedBlock(kind="TEXT", content="网站导航", source_page=1)],
        [],
        total_pages=2,
    )
    failures = DocumentQualityThresholds().validate(metrics)

    assert "noise_rate" in " ".join(failures)
    assert "missing_pages" in " ".join(failures)


def test_document_quality_counts_duplicate_glyph_residue_without_flagging_normal_words() -> None:
    from app.rag.document_quality import measure_document_quality
    from app.rag.formats import ParsedBlock

    metrics = measure_document_quality(
        [
            ParsedBlock(kind="TEXT", content="IInnffoorrmmaattiioonn adapted."),
            ParsedBlock(kind="TEXT", content="Coffee and letter remain normal."),
        ],
        [],
    )

    assert metrics.duplicate_glyph_block_count == 1


def test_document_quality_ignores_repeated_titles_and_cross_page_guidance() -> None:
    from app.rag.document_quality import measure_document_quality
    from app.rag.formats import ParsedBlock

    blocks = [
        ParsedBlock(kind="TEXT", content="测试目的", source_page=1),
        ParsedBlock(kind="TEXT", content="测试目的", source_page=2),
        ParsedBlock(kind="TEXT", content="核心建议。" * 30, source_page=1),
        ParsedBlock(kind="TEXT", content="核心建议。" * 30, source_page=2),
    ]

    metrics = measure_document_quality(blocks, [], total_pages=2)

    assert metrics.duplicate_block_count == 0


def test_document_quality_reports_ocr_and_visual_review_pages() -> None:
    from app.rag.document_quality import DocumentQualityThresholds, measure_document_quality
    from app.rag.formats import ParsedBlock, PdfPageProfile

    blocks = [ParsedBlock(kind="TEXT", content="动作说明。", source_page=2)]
    profiles = [
        PdfPageProfile(1, 1, 1.0, 0, 0.0, 0, 0, "OCR_REQUIRED"),
        PdfPageProfile(
            2,
            1,
            0.7,
            20,
            0.05,
            0,
            1,
            "VISUAL_REVIEW_REQUIRED",
            detected_columns=3,
        ),
    ]

    metrics = measure_document_quality(
        blocks,
        [],
        total_pages=2,
        page_profiles=profiles,
    )

    assert metrics.ocr_required_pages == (1,)
    assert metrics.visual_review_required_pages == (2,)
    assert metrics.max_image_area_ratio == 1.0
    assert metrics.toc_page_count == 0
    assert metrics.repeated_edge_line_count == 0
    assert metrics.layout_reordered_page_count == 1
    assert "ocr_required_pages" in " ".join(DocumentQualityThresholds().validate(metrics))


def test_document_quality_does_not_call_intentionally_removed_toc_page_missing() -> None:
    from app.rag.document_quality import measure_document_quality
    from app.rag.formats import ParsedBlock, PdfPageProfile

    metrics = measure_document_quality(
        [ParsedBlock(kind="TEXT", content="正文内容。", source_page=2)],
        [],
        total_pages=2,
        page_profiles=[
            PdfPageProfile(
                1,
                0,
                0.0,
                100,
                0.1,
                0,
                0,
                "NORMAL",
                toc_detected=True,
            ),
        ],
    )

    assert metrics.excluded_pages == (1,)
    assert metrics.missing_pages == ()
    assert metrics.page_coverage == 1.0


def test_document_quality_allows_system_injected_heading_prefix_in_child() -> None:
    from app.rag.document_quality import measure_document_quality
    from app.rag.formats import ParsedBlock
    from app.rag.ingestion import ChunkDraft

    metrics = measure_document_quality(
        [
            ParsedBlock(
                kind="TEXT",
                content="正文内容。",
                heading_path=("训练安全",),
                parent_content="训练安全\n前置说明。正文内容。",
            )
        ],
        [ChunkDraft("训练安全\n正文内容。", ("训练安全",), "训练安全\n前置说明。正文内容。")],
    )

    assert metrics.parent_integrity == 1.0


def test_document_quality_allows_same_tokens_when_pdf_column_order_differs() -> None:
    from app.rag.document_quality import measure_document_quality
    from app.rag.formats import ParsedBlock
    from app.rag.ingestion import ChunkDraft

    metrics = measure_document_quality(
        [
            ParsedBlock(
                kind="TEXT",
                content="列一内容 列二内容",
                heading_path=("复杂章节",),
                parent_content="复杂章节\n列二内容 列一内容",
            )
        ],
        [ChunkDraft("复杂章节\n列一内容 列二内容", ("复杂章节",), "复杂章节\n列二内容 列一内容")],
    )

    assert metrics.parent_integrity == 1.0


def test_document_quality_does_not_count_complete_heading_parent_as_fragment() -> None:
    from app.rag.document_quality import measure_document_quality
    from app.rag.formats import ParsedBlock

    metrics = measure_document_quality(
        [
            ParsedBlock(
                kind="TEXT",
                content="MESSAGE FROM THE SECRETARY",
                parent_content="MESSAGE FROM THE SECRETARY",
            )
        ],
        [],
    )

    assert metrics.fragment_block_count == 0


def test_document_quality_blocks_ambiguous_table_continuation() -> None:
    from app.rag.document_quality import DocumentQualityThresholds, measure_document_quality
    from app.rag.formats import ParsedBlock

    metrics = measure_document_quality(
        [
            ParsedBlock(
                kind="TABLE",
                content="| 指标 | 数值 |\n| --- | --- |\n| A | 1 |",
                metadata={"table_continuation_status": "AMBIGUOUS_REVIEW"},
            )
        ],
        [],
    )

    assert metrics.table_integrity == 0.0
    assert metrics.table_ambiguous_continuation_count == 1
    assert "table_integrity" in " ".join(DocumentQualityThresholds().validate(metrics))


def test_quality_report_comparison_requires_same_source_and_marks_directions() -> None:
    from app.rag.document_quality import compare_quality_reports

    before = {
        "label": "before",
        "results": [
            {
                "relative_path": "data/a.pdf",
                "source_sha256": "same",
                "status": "BLOCKED",
                "metrics": {
                    "noise_rate": 0.2,
                    "fragment_rate": 0.4,
                    "duplicate_rate": 0.1,
                    "duplicate_glyph_block_count": 2,
                    "parent_integrity": 0.8,
                    "table_integrity": 0.8,
                    "page_coverage": 0.8,
                    "missing_pages": [2],
                    "ocr_required_pages": [1],
                },
            }
        ],
    }
    after = {
        "label": "after",
        "results": [
            {
                "relative_path": "data/a.pdf",
                "source_sha256": "same",
                "status": "PASS",
                "metrics": {
                    "noise_rate": 0.0,
                    "fragment_rate": 0.3,
                    "duplicate_rate": 0.02,
                    "duplicate_glyph_block_count": 0,
                    "parent_integrity": 1.0,
                    "table_integrity": 1.0,
                    "page_coverage": 1.0,
                    "missing_pages": [],
                    "ocr_required_pages": [],
                },
            }
        ],
    }

    comparison = compare_quality_reports(before, after)

    entry = comparison["entries"][0]
    assert comparison["regressed_count"] == 0
    assert comparison["improved_count"] == 1
    assert entry["after_status"] == "PASS"
    assert set(entry["improvements"]) == {
        "noise_rate",
        "fragment_rate",
        "duplicate_rate",
        "duplicate_glyph_block_count",
        "parent_integrity",
        "table_integrity",
        "page_coverage",
        "missing_pages",
        "ocr_required_pages",
    }


def test_quality_report_comparison_rejects_changed_source_hash() -> None:
    from app.rag.document_quality import compare_quality_reports

    report = {
        "results": [
            {
                "relative_path": "data/a.pdf",
                "source_sha256": "before",
                "metrics": {},
            }
        ]
    }
    changed = {
        "results": [
            {
                "relative_path": "data/a.pdf",
                "source_sha256": "after",
                "metrics": {},
            }
        ]
    }

    import pytest

    with pytest.raises(ValueError, match="SHA-256"):
        compare_quality_reports(report, changed)


def test_retrieval_evaluation_calculates_recall_and_mrr() -> None:
    case = RetrievalEvalCase("case-1", "热身", frozenset({"doc-a", "doc-b"}))

    result = evaluate_case(case, ["doc-x", "doc-b", "doc-a"], k=3)

    assert result.recall_at_k == 1.0
    assert result.reciprocal_rank == 0.5
    assert aggregate_results([result]) == {
        "recall_at_k": 1.0,
        "mrr": 0.5,
        "case_count": 1.0,
        "forbidden_hits": 0.0,
    }


def test_evaluation_deduplicates_hits_and_counts_forbidden_results() -> None:
    case = RetrievalEvalCase(
        "security-1",
        "组织训练",
        frozenset({"allowed-1", "allowed-2"}),
        frozenset({"other-org-1"}),
    )

    result = evaluate_case(case, ["allowed-1", "allowed-1", "other-org-1"], k=3)

    assert result.recall_at_k == 0.5
    assert result.forbidden_hits == 1


def test_thresholds_report_quality_and_permission_failures() -> None:
    thresholds = RetrievalEvalThresholds(0.9, 0.8)

    failures = thresholds.validate(
        {"recall_at_k": 0.5, "mrr": 0.7, "forbidden_hits": 1.0, "case_count": 1.0}
    )

    assert len(failures) == 3


def test_case_mapping_loads_golden_retrieval_and_forbidden_ids() -> None:
    case = case_from_mapping(
        {
            "case_id": "case-1",
            "query": "热身",
            "relevant_ids": ["allowed-1"],
            "forbidden_ids": ["private-1"],
            "retrieved_ids": ["allowed-1"],
        }
    )

    assert case.retrieved_ids == ("allowed-1",)
    assert case.forbidden_ids == frozenset({"private-1"})


def test_evaluation_cli_returns_nonzero_when_threshold_is_breached(tmp_path) -> None:
    cases = tmp_path / "cases.json"
    thresholds = tmp_path / "thresholds.json"
    cases.write_text(
        '[{"case_id":"case-1","query":"热身","relevant_ids":["allowed-1"],'
        '"retrieved_ids":["other-1"],"forbidden_ids":["other-1"]}]',
        encoding="utf-8",
    )
    thresholds.write_text(
        '{"k": 5, "min_recall_at_k": 1.0, "min_mrr": 1.0, "max_forbidden_hits": 0}',
        encoding="utf-8",
    )

    assert main(["--cases", str(cases), "--thresholds", str(thresholds)]) == 1
