"""在隔离容器中验收 Prometheus 到 Alertmanager 的真实告警链路。"""

from __future__ import annotations

import argparse
import queue
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx

from scripts.alertmanager_route_check import (
    AlertmanagerRouteCheckError,
    _port_is_available,
    _run_docker,
    _wait_ready,
    _WebhookCaptureHandler,
    validate_runtime_options,
)

SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = SERVICE_ROOT.parent / "deployment/observability/alertmanager.yml"
DEFAULT_IMAGE = "prom/alertmanager:v0.27.0"
DEFAULT_PROMETHEUS_IMAGE = "prom/prometheus:v2.54.1"
DEFAULT_RECEIVER_PORT = 18080
DEFAULT_METRICS_PORT = 18081
DEFAULT_ALERTMANAGER_PORT = 19093
DEFAULT_PROMETHEUS_PORT = 19090
DEFAULT_TIMEOUT_SECONDS = 40.0


class _MetricsHandler(BaseHTTPRequestHandler):
    """可控的模拟 Metrics 服务：先返回 503 触发 down，后恢复为 200。"""

    available = False

    def do_GET(self) -> None:
        if self.path != "/metrics":
            self.send_error(404)
            return
        if not self.available:
            self.send_error(503, "synthetic outage")
            return
        body = b"# HELP fitness_agent_e2e_probe Synthetic probe\n# TYPE fitness_agent_e2e_probe gauge\nfitness_agent_e2e_probe 1\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """关闭模拟服务默认日志，避免把测试请求写入终端。"""


def build_prometheus_config(alertmanager_port: int, metrics_port: int) -> str:
    """生成只包含模拟目标和临时规则的 Prometheus 配置。"""

    return f"""global:
  scrape_interval: 1s
  evaluation_interval: 1s

rule_files:
  - /etc/prometheus/e2e-rules.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: [\"host.docker.internal:{alertmanager_port}\"]

scrape_configs:
  - job_name: fitness-agent-e2e
    metrics_path: /metrics
    static_configs:
      - targets: [\"host.docker.internal:{metrics_port}\"]
"""


def build_e2e_rules() -> str:
    """生成短窗口规则，避免真实生产规则的 2 分钟等待拖长隔离验收。"""

    return """groups:
  - name: fitness-agent-e2e
    interval: 1s
    rules:
      - alert: FitnessAgentDownE2E
        expr: up{job=\"fitness-agent-e2e\"} == 0
        for: 2s
        labels:
          severity: critical
          service: fitness-agent
        annotations:
          summary: \"隔离验收 Agent 不可用\"
          description: \"模拟 Metrics 服务连续异常。\"
"""


def build_alertmanager_config(config_path: Path, receiver_port: int) -> str:
    """复用仓库路由，只替换本次临时 webhook 端口。"""

    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AlertmanagerRouteCheckError(f"无法读取 Alertmanager 配置：{config_path}") from exc
    return content.replace("host.docker.internal:18080", f"host.docker.internal:{receiver_port}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验收 Prometheus 到 Alertmanager 的隔离告警链路")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Alertmanager 配置文件"
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Alertmanager 镜像")
    parser.add_argument(
        "--prometheus-image", default=DEFAULT_PROMETHEUS_IMAGE, help="Prometheus 镜像"
    )
    parser.add_argument("--receiver-port", type=int, default=DEFAULT_RECEIVER_PORT)
    parser.add_argument("--metrics-port", type=int, default=DEFAULT_METRICS_PORT)
    parser.add_argument("--alertmanager-port", type=int, default=DEFAULT_ALERTMANAGER_PORT)
    parser.add_argument("--prometheus-port", type=int, default=DEFAULT_PROMETHEUS_PORT)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args()


def _post_synthetic_alerts(url: str, alerts: list[dict[str, Any]]) -> None:
    """向 Alertmanager 注入合成状态，确保不携带真实用户、机构或请求标识。"""

    try:
        response = httpx.post(
            url.rstrip("/") + "/api/v2/alerts", json=alerts, timeout=3.0, trust_env=False
        )
        if not 200 <= response.status_code < 300:
            detail = response.text.strip().replace("\n", " ")[-300:]
            raise AlertmanagerRouteCheckError(
                f"Alertmanager API 返回 HTTP {response.status_code}：{detail or '无响应正文'}"
            )
    except httpx.HTTPError as exc:
        raise AlertmanagerRouteCheckError("无法向 Alertmanager 注入合成告警") from exc


def _alert(alertname: str, severity: str, starts_at: str, ends_at: str) -> dict[str, Any]:
    return {
        "labels": {"alertname": alertname, "service": "fitness-agent", "severity": severity},
        "annotations": {"summary": "隔离验收告警"},
        "startsAt": starts_at,
        "endsAt": ends_at,
        "generatorURL": "http://prometheus.invalid/graph",
    }


def _next_event(events: queue.Queue[dict[str, Any]], timeout_seconds: float) -> dict[str, Any]:
    try:
        return events.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise AlertmanagerRouteCheckError("未收到 Alertmanager webhook") from exc


def _drain_events(events: queue.Queue[dict[str, Any]], wait_seconds: float) -> list[dict[str, Any]]:
    """等待一个通知窗口后收集剩余事件，用于确认 warning 被抑制。"""

    deadline = time.monotonic() + wait_seconds
    drained: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        try:
            drained.append(events.get(timeout=min(0.2, deadline - time.monotonic())))
        except queue.Empty:
            pass
    return drained


def execute_check(args: argparse.Namespace) -> None:
    """启动两个临时容器，验证真实 Prometheus 发送、恢复和 Alertmanager 抑制。"""

    validate_runtime_options(args.receiver_port, args.alertmanager_port, args.timeout_seconds)
    all_ports = [
        args.receiver_port,
        args.metrics_port,
        args.alertmanager_port,
        args.prometheus_port,
    ]
    if any(not 1024 <= port <= 65535 for port in all_ports) or len(set(all_ports)) != len(
        all_ports
    ):
        raise AlertmanagerRouteCheckError("验收端口必须唯一且位于 1024-65535")
    if any(not _port_is_available(port) for port in all_ports):
        raise AlertmanagerRouteCheckError("本地验收端口已被占用")

    events: queue.Queue[dict[str, Any]] = queue.Queue()
    _WebhookCaptureHandler.events = events
    _MetricsHandler.available = False
    webhook_server = ThreadingHTTPServer(("0.0.0.0", args.receiver_port), _WebhookCaptureHandler)
    metrics_server = ThreadingHTTPServer(("0.0.0.0", args.metrics_port), _MetricsHandler)
    webhook_thread = threading.Thread(target=webhook_server.serve_forever, daemon=True)
    metrics_thread = threading.Thread(target=metrics_server.serve_forever, daemon=True)
    webhook_thread.start()
    metrics_thread.start()

    started_containers: list[str] = []
    try:
        with TemporaryDirectory(prefix="fitness-agent-observability-e2e-") as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "alertmanager.yml").write_text(
                build_alertmanager_config(args.config, args.receiver_port), encoding="utf-8"
            )
            (temp_path / "prometheus.yml").write_text(
                build_prometheus_config(args.alertmanager_port, args.metrics_port), encoding="utf-8"
            )
            (temp_path / "e2e-rules.yml").write_text(build_e2e_rules(), encoding="utf-8")

            alertmanager_name = f"fitness-agent-alertmanager-e2e-{uuid.uuid4().hex[:12]}"
            prometheus_name = f"fitness-agent-prometheus-e2e-{uuid.uuid4().hex[:12]}"
            _run_docker(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--detach",
                    "--name",
                    alertmanager_name,
                    "--add-host",
                    "host.docker.internal:host-gateway",
                    "--publish",
                    f"127.0.0.1:{args.alertmanager_port}:9093",
                    "--volume",
                    f"{temp_path}:/etc/alertmanager:ro",
                    args.image,
                    "--config.file=/etc/alertmanager/alertmanager.yml",
                ]
            )
            started_containers.append(alertmanager_name)
            _run_docker(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--detach",
                    "--name",
                    prometheus_name,
                    "--add-host",
                    "host.docker.internal:host-gateway",
                    "--publish",
                    f"127.0.0.1:{args.prometheus_port}:9090",
                    "--volume",
                    f"{temp_path}:/etc/prometheus:ro",
                    args.prometheus_image,
                    "--config.file=/etc/prometheus/prometheus.yml",
                ]
            )
            started_containers.append(prometheus_name)

            alertmanager_url = f"http://127.0.0.1:{args.alertmanager_port}"
            _wait_ready(alertmanager_url, args.timeout_seconds)
            _wait_ready(f"http://127.0.0.1:{args.prometheus_port}", args.timeout_seconds)

            first_event = _next_event(events, args.timeout_seconds)
            if first_event.get("status") != "firing":
                raise AlertmanagerRouteCheckError(
                    f"Prometheus 触发通知状态异常：{first_event.get('status')}"
                )
            _MetricsHandler.available = True
            resolved_event = _next_event(events, args.timeout_seconds)
            if resolved_event.get("status") != "resolved":
                raise AlertmanagerRouteCheckError(
                    f"Prometheus 恢复通知状态异常：{resolved_event.get('status')}"
                )

            starts_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            firing_ends_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            _post_synthetic_alerts(
                alertmanager_url,
                [
                    _alert("FitnessAgentCriticalE2E", "critical", starts_at, firing_ends_at),
                    _alert("FitnessAgentWarningE2E", "warning", starts_at, firing_ends_at),
                ],
            )
            suppressed_event = _next_event(events, args.timeout_seconds)
            labels = suppressed_event.get("alerts", [{}])[0]
            if suppressed_event.get("status") != "firing" or labels.get("severity") != "critical":
                raise AlertmanagerRouteCheckError("critical/warning 抑制结果不符合预期")
            unexpected = [event for event in _drain_events(events, 2.0) if event.get("alerts")]
            if any(
                alert.get("severity") == "warning"
                for event in unexpected
                for alert in event["alerts"]
            ):
                raise AlertmanagerRouteCheckError("同服务 critical 存在时仍发送了 warning 通知")

            resolved_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            _post_synthetic_alerts(
                alertmanager_url,
                [
                    _alert("FitnessAgentCriticalE2E", "critical", starts_at, resolved_at),
                    _alert("FitnessAgentWarningE2E", "warning", starts_at, resolved_at),
                ],
            )
            resolved_suppressed_event = _next_event(events, args.timeout_seconds)
            if resolved_suppressed_event.get("status") != "resolved":
                raise AlertmanagerRouteCheckError("critical 恢复通知状态异常")
            print(
                "Prometheus→Alertmanager 端到端验收通过："
                "AgentDown firing/resolved、critical 抑制 warning、恢复通知均通过"
            )
    finally:
        for container_name in reversed(started_containers):
            try:
                _run_docker(["docker", "rm", "--force", container_name])
            except AlertmanagerRouteCheckError:
                print(f"警告：临时容器清理失败：{container_name}")
        webhook_server.shutdown()
        webhook_server.server_close()
        metrics_server.shutdown()
        metrics_server.server_close()
        webhook_thread.join(timeout=2)
        metrics_thread.join(timeout=2)


def main() -> int:
    try:
        execute_check(_parse_args())
    except AlertmanagerRouteCheckError as exc:
        print(f"Prometheus→Alertmanager 端到端验收失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
