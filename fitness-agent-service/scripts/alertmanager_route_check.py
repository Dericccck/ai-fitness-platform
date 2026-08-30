"""在隔离容器中验收 Alertmanager 的 firing/resolved webhook 生命周期。"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx


class AlertmanagerRouteCheckError(RuntimeError):
    """Alertmanager 隔离路由验收失败。"""


SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = SERVICE_ROOT.parent / "deployment/observability/alertmanager.yml"
DEFAULT_IMAGE = "prom/alertmanager:v0.27.0"
DEFAULT_RECEIVER_PORT = 18080
DEFAULT_ALERTMANAGER_PORT = 19093
DEFAULT_TIMEOUT_SECONDS = 20.0


class _WebhookCaptureHandler(BaseHTTPRequestHandler):
    """接收本地 Alertmanager webhook，并把脱敏后的状态放入线程安全队列。"""

    events: queue.Queue[dict[str, Any]]

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400, "invalid JSON")
            return
        if not isinstance(payload, dict):
            self.send_error(400, "payload must be an object")
            return
        # 只保存验收所需的状态和标签，不把 generatorURL 等无关内容带入输出。
        alerts = payload.get("alerts", [])
        if isinstance(alerts, list):
            self.events.put(
                {
                    "status": payload.get("status"),
                    "alerts": [
                        alert.get("labels", {}) for alert in alerts if isinstance(alert, dict)
                    ],
                }
            )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, _format: str, *_args: object) -> None:
        """关闭 HTTP Server 默认 stderr 输出，避免把测试请求内容写入终端。"""


def _port_is_available(port: int) -> bool:
    """验证本地端口可绑定，避免启动容器后才得到不清晰的端口冲突。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def validate_runtime_options(
    receiver_port: int, alertmanager_port: int, timeout_seconds: float
) -> None:
    """校验隔离验收参数，避免误绑定系统端口或运行无限等待。"""

    if not 1024 <= receiver_port <= 65535 or not 1024 <= alertmanager_port <= 65535:
        raise AlertmanagerRouteCheckError("端口必须位于 1024-65535")
    if receiver_port == alertmanager_port:
        raise AlertmanagerRouteCheckError("接收器端口和 Alertmanager 端口不能相同")
    if timeout_seconds <= 0 or timeout_seconds > 120:
        raise AlertmanagerRouteCheckError("超时时间必须位于 0-120 秒")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验收 Alertmanager 本地 webhook 路由")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Alertmanager 配置文件"
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Alertmanager 镜像")
    parser.add_argument("--receiver-port", type=int, default=DEFAULT_RECEIVER_PORT)
    parser.add_argument("--alertmanager-port", type=int, default=DEFAULT_ALERTMANAGER_PORT)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args()


def _read_config(path: Path) -> None:
    if not path.is_file():
        raise AlertmanagerRouteCheckError(f"Alertmanager 配置不存在：{path}")
    try:
        path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AlertmanagerRouteCheckError(f"无法读取 Alertmanager 配置：{path}") from exc


def _run_docker(args: list[str]) -> subprocess.CompletedProcess[str]:
    """执行受控 Docker 命令，并把 stderr 转换成不含 Secret 的错误。"""

    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)
    except FileNotFoundError as exc:
        raise AlertmanagerRouteCheckError("未找到 Docker CLI") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-500:]
        raise AlertmanagerRouteCheckError(f"Docker 命令失败：{detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AlertmanagerRouteCheckError("Docker 命令超时") from exc


def _wait_ready(url: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    with httpx.Client(timeout=1.0, trust_env=False, follow_redirects=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(url.rstrip("/") + "/-/ready")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise AlertmanagerRouteCheckError("Alertmanager 未在限定时间内就绪")


def _post_alerts(url: str, starts_at: str, ends_at: str) -> None:
    payload = [
        {
            "labels": {
                "alertname": "FitnessAgentDown",
                "service": "fitness-agent",
                "severity": "critical",
            },
            "annotations": {"summary": "隔离验收告警"},
            "startsAt": starts_at,
            "endsAt": ends_at,
            "generatorURL": "http://prometheus.invalid/graph",
        }
    ]
    try:
        response = httpx.post(
            url.rstrip("/") + "/api/v2/alerts",
            json=payload,
            timeout=3.0,
            trust_env=False,
        )
        if not 200 <= response.status_code < 300:
            detail = response.text.strip().replace("\n", " ")[-300:]
            raise AlertmanagerRouteCheckError(
                f"Alertmanager API 返回 HTTP {response.status_code}：{detail or '无响应正文'}"
            )
    except httpx.HTTPError as exc:
        raise AlertmanagerRouteCheckError("无法向 Alertmanager 注入合成告警") from exc


def _wait_event(
    events: queue.Queue[dict[str, Any]],
    expected_status: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=min(0.5, max(0.05, deadline - time.monotonic())))
        except queue.Empty:
            continue
        if event.get("status") == expected_status:
            return event
    raise AlertmanagerRouteCheckError(f"未收到 Alertmanager {expected_status} webhook")


def execute_check(args: argparse.Namespace) -> None:
    """启动唯一临时 Alertmanager，验证 firing 和 resolved 通知后清理容器。"""

    _read_config(args.config)
    validate_runtime_options(args.receiver_port, args.alertmanager_port, args.timeout_seconds)
    if not _port_is_available(args.receiver_port) or not _port_is_available(args.alertmanager_port):
        raise AlertmanagerRouteCheckError("本地验收端口已被占用")

    events: queue.Queue[dict[str, Any]] = queue.Queue()
    _WebhookCaptureHandler.events = events
    server = ThreadingHTTPServer(("0.0.0.0", args.receiver_port), _WebhookCaptureHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    container_name = f"fitness-agent-alertmanager-check-{uuid.uuid4().hex[:12]}"
    config_dir = args.config.parent.resolve()
    command = [
        "docker",
        "run",
        "--rm",
        "--detach",
        "--name",
        container_name,
        "--add-host",
        "host.docker.internal:host-gateway",
        "--publish",
        f"127.0.0.1:{args.alertmanager_port}:9093",
        "--volume",
        f"{config_dir}:/etc/alertmanager:ro",
        args.image,
        "--config.file=/etc/alertmanager/alertmanager.yml",
    ]
    try:
        _run_docker(command)
        base_url = f"http://127.0.0.1:{args.alertmanager_port}"
        _wait_ready(base_url, args.timeout_seconds)
        starts_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        _post_alerts(base_url, starts_at, (datetime.now(UTC) + timedelta(hours=1)).isoformat())
        firing = _wait_event(events, "firing", args.timeout_seconds)
        _post_alerts(base_url, starts_at, (datetime.now(UTC) - timedelta(seconds=1)).isoformat())
        resolved = _wait_event(events, "resolved", args.timeout_seconds)
        print(
            "Alertmanager 隔离路由验收通过："
            f"firing={firing['status']}, resolved={resolved['status']}, "
            "通知仅到本地临时 webhook"
        )
    finally:
        try:
            _run_docker(["docker", "rm", "--force", container_name])
        except AlertmanagerRouteCheckError:
            # 主异常已经足够表达失败；清理失败仍不能静默影响后续开发。
            print(f"警告：临时 Alertmanager 容器清理失败：{container_name}")
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def main() -> int:
    try:
        execute_check(_parse_args())
    except AlertmanagerRouteCheckError as exc:
        print(f"Alertmanager 隔离路由验收失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
