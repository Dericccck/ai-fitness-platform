"""校验 Agent 指标、Prometheus 抓取配置和告警规则契约。"""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class ObservabilityContractError(RuntimeError):
    """可观测性配置或 Prometheus 运行时状态不符合契约。"""


SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALERTS_PATH = SERVICE_ROOT.parent / "deployment/observability/fitness-agent-alerts.yml"
DEFAULT_PROMETHEUS_PATH = SERVICE_ROOT.parent / "deployment/observability/prometheus.yml"
DEFAULT_METRICS_PATH = SERVICE_ROOT / "app/core/metrics.py"
ALERT_NAME_PATTERN = re.compile(r"^\s*-\s+alert:\s*([A-Za-z][A-Za-z0-9_]*)\s*$", re.MULTILINE)
METRIC_PATTERN = re.compile(r"\bfitness_agent_[A-Za-z0-9_]+\b")
METRIC_DEFINITION_PATTERN = re.compile(
    r"(?:Counter|Gauge|Histogram)\(\s*\"([A-Za-z][A-Za-z0-9_]*)\""
)
EXPECTED_ALERTS = frozenset(
    {
        "FitnessAgentDown",
        "FitnessAgentHighHttp5xxRate",
        "FitnessAgentHighP95Latency",
        "FitnessAgentRequestsBacklogged",
        "FitnessAgentOperationsAuditFailed",
        "FitnessAgentMaintenanceFailed",
        "FitnessAgentNotificationDeliveryFailed",
    }
)


@dataclass(frozen=True)
class ObservabilityCheckConfig:
    """可观测性契约文件路径和可选的 Prometheus 地址。"""

    alerts_path: Path
    prometheus_path: Path
    metrics_path: Path
    prometheus_url: str
    execute: bool


def _read(path: Path, label: str) -> str:
    """读取契约文件；错误中只暴露受控文件路径和类型。"""

    if not path.is_file():
        raise ObservabilityContractError(f"{label}不存在：{path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ObservabilityContractError(f"无法读取{label}：{path}") from exc


def _rule_blocks(alerts_text: str) -> list[str]:
    """按告警项切分 YAML 文本，避免门禁依赖额外 YAML 解析库。"""

    starts = [match.start() for match in ALERT_NAME_PATTERN.finditer(alerts_text)]
    return [
        alerts_text[start : starts[index + 1] if index + 1 < len(starts) else len(alerts_text)]
        for index, start in enumerate(starts)
    ]


def extract_alert_names(alerts_text: str) -> frozenset[str]:
    """提取告警名称，保证规则改名时不会绕过监控契约。"""

    return frozenset(ALERT_NAME_PATTERN.findall(alerts_text))


def _metric_family_name(metric: str) -> str:
    """将 Histogram 暴露的 bucket/count/sum 子指标还原为定义时的指标族。"""

    for suffix in ("_bucket", "_count", "_sum"):
        if metric.endswith(suffix):
            return metric[: -len(suffix)]
    return metric


def validate_alert_rules(alerts_text: str, metrics_text: str) -> tuple[str, ...]:
    """验证告警集合、必需字段、低基数标签和指标引用。"""

    names = extract_alert_names(alerts_text)
    if names != EXPECTED_ALERTS:
        missing = sorted(EXPECTED_ALERTS - names)
        unexpected = sorted(names - EXPECTED_ALERTS)
        raise ObservabilityContractError(
            f"告警集合不符合预期：missing={missing}, unexpected={unexpected}"
        )
    if len(names) != len(ALERT_NAME_PATTERN.findall(alerts_text)):
        raise ObservabilityContractError("告警名称存在重复")

    defined_metrics = {
        f"fitness_agent_{name}" for name in METRIC_DEFINITION_PATTERN.findall(metrics_text)
    }
    referenced_metrics = {
        _metric_family_name(metric) for metric in METRIC_PATTERN.findall(alerts_text)
    }
    undefined_metrics = sorted(referenced_metrics - defined_metrics)
    if undefined_metrics:
        raise ObservabilityContractError(f"告警引用了未定义的 Agent 指标：{undefined_metrics}")

    for block in _rule_blocks(alerts_text):
        name_match = ALERT_NAME_PATTERN.search(block)
        name = name_match.group(1) if name_match else "unknown"
        if not re.search(r"^\s+expr:\s*(\||.+)", block, re.MULTILINE):
            raise ObservabilityContractError(f"告警 {name} 缺少 expr")
        if not re.search(r"^\s+labels:\s*$", block, re.MULTILINE):
            raise ObservabilityContractError(f"告警 {name} 缺少 labels")
        if not re.search(r"^\s+severity:\s+(critical|warning|info)\s*$", block, re.MULTILINE):
            raise ObservabilityContractError(f"告警 {name} 缺少合法 severity")
        if not re.search(r"^\s+service:\s+fitness-agent\s*$", block, re.MULTILINE):
            raise ObservabilityContractError(f"告警 {name} 缺少固定 service 标签")
        if not re.search(r"^\s+annotations:\s*$", block, re.MULTILINE):
            raise ObservabilityContractError(f"告警 {name} 缺少 annotations")
        if not re.search(r"^\s+summary:\s+\S", block, re.MULTILINE) or not re.search(
            r"^\s+description:\s+\S", block, re.MULTILINE
        ):
            raise ObservabilityContractError(f"告警 {name} 缺少 summary 或 description")
        # 标签不能包含用户/请求等高基数值，否则会按业务请求无限创建时间序列。
        label_section = block.split("labels:", 1)[1].split("annotations:", 1)[0]
        if re.search(r"request_id|trace_id|user_id|organization_id|ticket_id", label_section):
            raise ObservabilityContractError(f"告警 {name} 的 labels 包含高基数业务标识")
    return tuple(sorted(names))


def validate_prometheus_config(prometheus_text: str) -> None:
    """验证 Prometheus 至少配置告警规则和 Agent/Worker 抓取任务。"""

    if "rule_files:" not in prometheus_text or "fitness-agent-alerts.yml" not in prometheus_text:
        raise ObservabilityContractError("Prometheus 未配置健身 Agent 告警规则文件")
    for job_name in ("fitness-agent", "fitness-agent-workers"):
        if not re.search(rf"job_name:\s*{re.escape(job_name)}\s*$", prometheus_text, re.MULTILINE):
            raise ObservabilityContractError(f"Prometheus 缺少抓取任务：{job_name}")


def validate_static_contract(config: ObservabilityCheckConfig) -> tuple[str, ...]:
    """执行不依赖运行中 Prometheus 的静态检查。"""

    alerts_text = _read(config.alerts_path, "告警规则文件")
    prometheus_text = _read(config.prometheus_path, "Prometheus 配置文件")
    metrics_text = _read(config.metrics_path, "指标定义文件")
    names = validate_alert_rules(alerts_text, metrics_text)
    validate_prometheus_config(prometheus_text)
    return names


async def _check_prometheus_runtime(url: str, expected_alerts: tuple[str, ...]) -> int:
    """只读检查 Prometheus 配置加载状态和已加载告警名称。"""

    async with httpx.AsyncClient(
        timeout=5.0,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        try:
            config_response = await client.get(url.rstrip("/") + "/api/v1/status/config")
            rules_response = await client.get(url.rstrip("/") + "/api/v1/rules?type=alert")
        except httpx.HTTPError as exc:
            raise ObservabilityContractError("无法连接 Prometheus 运行时接口") from exc
    if config_response.status_code != 200 or rules_response.status_code != 200:
        raise ObservabilityContractError(
            f"Prometheus API 返回异常：config={config_response.status_code}, "
            f"rules={rules_response.status_code}"
        )
    try:
        config_payload: Any = config_response.json()
        rules_payload: Any = rules_response.json()
    except ValueError as exc:
        raise ObservabilityContractError("Prometheus API 返回不是 JSON") from exc
    if config_payload.get("status") != "success" or rules_payload.get("status") != "success":
        raise ObservabilityContractError("Prometheus 未成功返回配置或规则状态")

    groups = rules_payload.get("data", {}).get("groups", [])
    loaded_names = {
        rule.get("name")
        for group in groups
        if isinstance(group, dict)
        for rule in group.get("rules", [])
        if isinstance(rule, dict) and isinstance(rule.get("name"), str)
    }
    missing = sorted(set(expected_alerts) - loaded_names)
    if missing:
        raise ObservabilityContractError(f"Prometheus 未加载告警：{missing}")
    return len(loaded_names)


def build_parser() -> argparse.ArgumentParser:
    """构造静态和 Prometheus 运行时校验参数。"""

    parser = argparse.ArgumentParser(description="校验健身 Agent 可观测性契约")
    parser.add_argument("--alerts", type=Path, default=DEFAULT_ALERTS_PATH, help="告警规则文件")
    parser.add_argument(
        "--prometheus-config",
        type=Path,
        default=DEFAULT_PROMETHEUS_PATH,
        help="Prometheus 配置文件",
    )
    parser.add_argument(
        "--metrics", type=Path, default=DEFAULT_METRICS_PATH, help="Agent 指标定义文件"
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://127.0.0.1:9090",
        help="Prometheus 地址；仅 --execute 时访问",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="在静态检查后只读访问 Prometheus API，验证规则确已加载",
    )
    return parser


def main() -> int:
    """命令行入口；默认不访问网络，运行时检查失败不会泄露响应正文。"""

    try:
        args = build_parser().parse_args()
        config = ObservabilityCheckConfig(
            alerts_path=args.alerts,
            prometheus_path=args.prometheus_config,
            metrics_path=args.metrics,
            prometheus_url=str(args.prometheus_url).strip().rstrip("/"),
            execute=bool(args.execute),
        )
        names = validate_static_contract(config)
        print(f"可观测性静态契约校验通过：告警 {len(names)} 条，指标引用已匹配")
        if config.execute:
            loaded_count = asyncio.run(_check_prometheus_runtime(config.prometheus_url, names))
            print(f"Prometheus 运行时校验通过：已加载告警规则 {loaded_count} 条")
    except (ObservabilityContractError, OSError) as exc:
        print(f"可观测性契约校验失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
