"""Prepare per-case review materials without recording or inventing approvals."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .dataset import Dataset, _reject_nonfinite_json, _unique_json_object, load_dataset
from .review_queue import build_review_queue
from .signed_evidence import _required_review_domains


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _component_statuses(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        raise TypeError("COMPONENT_OBSERVATIONS_INVALID")
    rows = []
    for name, detail in sorted(value.items()):
        if not isinstance(name, str) or not isinstance(detail, dict):
            raise TypeError("COMPONENT_OBSERVATIONS_INVALID")
        status = detail.get("component_status", detail.get("status", "NOT_REPORTED"))
        if not isinstance(status, str) or len(status) > 80:
            raise ValueError("COMPONENT_STATUS_INVALID")
        rows.append({"component": name, "status": status})
    return rows


def build_packet(
    original: Dataset, proposal: Dataset, manifest_raw: bytes, cases_raw: bytes
) -> dict[str, Any]:
    """Record exact input hashes; report pairing and observations remain untrusted."""
    manifest = json.loads(
        manifest_raw, object_pairs_hook=_unique_json_object, parse_constant=_reject_nonfinite_json
    )
    if (
        not isinstance(manifest, dict)
        or manifest.get("dataset_sha256") != original.sha256
        or manifest.get("dataset_version") != original.dataset_version
        or manifest.get("mode") != "dataset_local_exploratory"
        or manifest.get("split") != "all"
        or not isinstance(manifest.get("run_id"), str)
        or not isinstance(manifest.get("created_at"), str)
    ):
        raise ValueError("REPORT_DATASET_MISMATCH")
    original_by_id = {case.case_id: case for case in original.cases}
    proposed_by_id = {case.case_id: case for case in proposal.cases}
    if (
        len(original_by_id) != len(original.cases)
        or len(proposed_by_id) != len(proposal.cases)
        or set(original_by_id) != set(proposed_by_id)
        or any(case.source.type != "synthetic" for case in original.cases)
        or any(case.review.status != "draft" for case in proposal.cases)
    ):
        raise ValueError("CASE_SETS_OR_PROPOSAL_STATUS_INVALID")
    report: dict[str, dict[str, Any]] = {}
    for line in cases_raw.splitlines():
        item = json.loads(
            line, object_pairs_hook=_unique_json_object, parse_constant=_reject_nonfinite_json
        )
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("case_id"), str)
            or item["case_id"] in report
            or item.get("task_status") not in {"INCONCLUSIVE", "SKIPPED", "ERROR", "FAIL"}
        ):
            raise ValueError("REPORT_CASES_INVALID")
        report[item["case_id"]] = item
    if set(report) != set(original_by_id):
        raise ValueError("REPORT_CASE_COVERAGE_INVALID")
    review_queue = build_review_queue(proposal)
    queue_by_id = {item["case_id"]: item for item in review_queue["cases"]}
    rows = []
    for case in original.cases:
        candidate = proposed_by_id[case.case_id]
        outcome = report[case.case_id]
        original_content = case.model_dump(mode="json")
        candidate_content = candidate.model_dump(mode="json")
        if (
            outcome.get("split") != case.split
            or outcome.get("primary_scenario") != case.primary_scenario
            or candidate.primary_scenario != case.primary_scenario
            or candidate.case_id != case.case_id
            or any(
                original_content[key] != candidate_content[key]
                for key in original_content
                if key not in {"dataset_version", "metadata", "expected"}
            )
        ):
            raise ValueError("CASE_IDENTITY_MISMATCH")
        domains = _required_review_domains(candidate)
        rows.append(
            {
                "case_id": case.case_id,
                "split": case.split,
                "scenario": case.primary_scenario,
                "required_review_domains_from_draft_taxonomy": sorted(domains)
                if domains is not None
                else None,
                "original_case": original_content,
                "proposed_case": candidate_content,
                "missing_proposed_fields": queue_by_id[case.case_id]["missing_grader_parameters"],
                "local_task_status": outcome["task_status"],
                "component_observations": _component_statuses(outcome.get("observations")),
                "human_review": None,
            }
        )
    return {
        "schema_version": "1.0",
        "purpose": "UNREVIEWED_CASE_AND_EVIDENCE_PREPARATION",
        "original_dataset_sha256": original.sha256,
        "proposal_dataset_sha256": proposal.sha256,
        "component_manifest_sha256": _sha(manifest_raw),
        "component_cases_sha256": _sha(cases_raw),
        "component_run_id": manifest["run_id"],
        "component_created_at": manifest["created_at"],
        "report_pairing": "DIRECTORY_ONLY_NOT_INDEPENDENTLY_VERIFIED",
        "report_origin": "local_exploratory_components_not_task_acceptance",
        "case_count": len(rows),
        "cases": rows,
    }


def render_markdown(packet: dict[str, Any]) -> str:
    header = [
        "# 120 条评测案例人工审核材料（未审核）",
        "",
        "本材料汇总原始规划、参数提案和本地组件状态；它不是审核结论、模型评分或验收证据。",
        "逐条核对业务前提、预期、断言参数、实际证据和开发/测试集泄漏；涉及安全或医疗内容须由具备相应资质的人员审核。",
        "提案中的字段存在不代表数值正确；`INCONCLUSIVE` 表示完整任务未判定。审核领域仅按草稿标签推导，可能不完整。",
        "组件报告来自历史运行，报告文件之间的配对和来源未独立认证；代码变更后需重新运行。",
        "",
        f"- 原始数据集 SHA-256：`{packet['original_dataset_sha256']}`",
        f"- 参数提案 SHA-256：`{packet['proposal_dataset_sha256']}`",
        f"- 本地组件报告 SHA-256：`{packet['component_cases_sha256']}`",
        f"- 组件运行时间：`{packet['component_created_at']}`；运行 ID：`{packet['component_run_id']}`",
        "- 审核结论、审核人和签署：待真实审核后填写；本材料未生成任何签名。",
    ]
    for row in packet["cases"]:
        original = row["original_case"]
        proposed = row["proposed_case"]
        material = {
            "原始步骤": original["steps"],
            "原始预期": original["expected"],
            "参数提案": proposed["expected"],
            "角色与夹具": {"actor": original["actor"], "fixture": original["fixture"]},
            "提案中缺少的评分字段": row["missing_proposed_fields"],
        }
        domains = row["required_review_domains_from_draft_taxonomy"]
        header.extend(
            [
                "",
                f"## {row['case_id']} · {row['scenario']} · {row['split']}",
                "",
                f"完整任务状态：`{row['local_task_status']}`；草稿标签推导审核领域：`{domains}`。",
                "",
                "```json",
                json.dumps(material, ensure_ascii=False, indent=2),
                "```",
                "",
                "组件观察：`" + json.dumps(row["component_observations"], ensure_ascii=False) + "`",
                "",
                "待审核：业务语义和参数、证据充分性、分组隔离、必要领域风险及实际输出。",
            ]
        )
    return "\n".join(header) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    original, proposal = load_dataset(args.original), load_dataset(args.proposal)
    report_dir, output_dir = Path(args.report_dir), Path(args.output_dir)
    packet = build_packet(
        original,
        proposal,
        (report_dir / "manifest.json").read_bytes(),
        (report_dir / "cases.jsonl").read_bytes(),
    )
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    (output_dir / "packet.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "review.md").write_text(render_markdown(packet), encoding="utf-8")
    print(json.dumps({"case_count": packet["case_count"], "status": "UNREVIEWED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
