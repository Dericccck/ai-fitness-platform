"""仅生成 SAE 已存在应用的镜像更新计划，绝不联网、推镜像或部署。

前提：应用/VPC/Secret/探针/资源/模型挂载已经人工配置并核验；数据库迁移单独审批。
输出不是云端部署成功证明，也不包含任何运行时 Secret。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

WORKERS = {
    "notification-worker": "app.notification_worker_main",
    "proactive-worker": "app.proactive_worker_main",
    "proactive-decision-worker": "app.proactive_decision_worker_main",
    "proactive-scheduler": "app.proactive_scheduler_main",
    "decision-action-worker": "app.decision_action_worker_main",
    "knowledge-worker": "app.knowledge_worker_main",
    "reindex-worker": "app.reindex_worker_main",
    "memory-retention-worker": "app.memory_retention_worker_main",
    "candidate-expiry-worker": "app.candidate_expiry_worker_main",
    "session-summary-worker": "app.session_summary_worker_main",
    "confirmation-reconciliation-worker": "app.confirmation_reconciliation_worker_main",
}
IMAGES = {"agent", "gateway", "training", "booking", "customer-service", "web"}
REQUIRED = {"training", "booking", "customer-service", "gateway", "agent-api", "web"}


def build_plan(config: dict[str, Any]) -> dict[str, Any]:
    if set(config) != {"region_id", "images", "applications"}:
        raise ValueError("配置只允许 region_id、images、applications；Secret 不应放入发布计划")
    region, images, apps = config["region_id"], config["images"], config["applications"]
    if not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}-[a-z]+-?\d*", region):
        raise ValueError("region_id 无效")
    if not isinstance(images, dict) or set(images) != IMAGES:
        raise ValueError("需要六个镜像的不可变地址")
    for image in images.values():
        if (
            not isinstance(image, str)
            or not re.fullmatch(r"[a-zA-Z0-9._:/-]+@sha256:[a-f0-9]{64}", image)
            or "example.com" in image
        ):
            raise ValueError("镜像必须是已推送仓库的 sha256 摘要地址，不能使用标签或占位符")
    if (
        not isinstance(apps, dict)
        or not REQUIRED <= apps.keys()
        or (apps.keys() - REQUIRED - WORKERS.keys())
    ):
        raise ValueError("应用集合缺少核心应用或包含未知角色")
    for app_id in apps.values():
        if not isinstance(app_id, str) or not re.fullmatch(r"[a-fA-F0-9-]{16,64}", app_id):
            raise ValueError("需要已创建的真实 SAE 应用 ID")
    if len(set(apps.values())) != len(apps):
        raise ValueError("独立角色不能复用同一个 SAE 应用 ID")
    groups = [
        ["training", "booking", "customer-service"],
        ["gateway"],
        ["agent-api"],
        sorted(apps.keys() & WORKERS.keys()),
        ["web"],
    ]
    stages = []
    for group in groups:
        if not group:
            continue
        commands = []
        for name in group:
            image_name = "agent" if name == "agent-api" or name in WORKERS else name
            params: dict[str, str] = {
                "RegionId": region,
                "AppId": apps[name],
                "ImageUrl": images[image_name],
            }
            if name in WORKERS:
                params.update(Command="python", CommandArgs=json.dumps(["-m", WORKERS[name]]))
            commands.append(
                {"application": name, "api": "sae.DeployApplication", "parameters": params}
            )
        stages.append(
            {
                "parallel_updates": commands,
                "next_stage_gate": "全部 ChangeOrder 成功且实例 readiness 通过；API 受理成功不等于部署成功",
            }
        )
    return {
        "mode": "offline_release_plan",
        "executed": False,
        "cloud_verified": False,
        "preconditions": [
            "确认目标为专用测试命名空间；应用 ID、VPC、Secret、服务 DNS、TLS 和镜像权限已核验",
            "镜像架构与 SAE 运行环境一致；本机 arm64 构建不能代替 amd64 验证",
            "先备份并审批 PG/MySQL 兼容迁移；迁移失败不进入发布阶段",
            "暂停确认写入口并 drain；请求体绑定协议升级期间不得新旧网关混跑",
            "配置 API/Worker 探针、120 秒停止宽限、连接池及模型 /models 只读共享挂载",
            "每个 Worker 独立应用，指标端口与其配置一致；不启用 HTTP 缩容到零",
        ],
        "stages": stages,
        "rollback": "先停写、排空及核对未知结果；按匹配协议回滚兼容镜像，保留新增表，不自动 downgrade",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = build_plan(json.loads(args.config.read_text(encoding="utf-8")))
    except (ValueError, TypeError, KeyError, OSError):
        print(
            json.dumps(
                {"passed": False, "executed": False, "error": "发布计划配置缺失、占位或无效"},
                ensure_ascii=False,
            )
        )
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "executed": False, "stages": len(plan["stages"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
