"""多模态分析、租约回收及媒体删除各自独立轮询。"""

import asyncio

from prometheus_client import start_http_server

from app.core.worker_runtime import poll_worker, run_worker_process
from app.main import app, http_metrics, lifespan
from app.media.metadata import remove_stale_sandbox_files


async def run() -> None:
    async with lifespan(app, role="worker"):
        if app.state.multimodal_worker is None or app.state.media_deletion_worker is None:
            raise RuntimeError("多模态任务未启用")

        start_http_server(
            app.state.settings.multimodal_worker_metrics_port, registry=http_metrics.registry
        )

        async def reap() -> bool:
            return bool(await app.state.multimodal_repository.reap())

        async def cleanup_temp() -> bool:
            return bool(
                await asyncio.to_thread(
                    remove_stale_sandbox_files, app.state.settings.multimodal_validator_temp_dir
                )
            )

        await asyncio.gather(
            poll_worker(app.state.multimodal_worker.run_once, interval=1.0),
            poll_worker(app.state.media_deletion_worker.run_once, interval=3.0, busy_interval=0.1),
            poll_worker(app.state.media_deletion.expire_one, interval=30.0, busy_interval=0.1),
            poll_worker(
                app.state.media_deletion.expire_abandoned_upload, interval=30.0, busy_interval=0.1
            ),
            poll_worker(
                app.state.media_deletion_worker.cleanup_staging_once,
                interval=30.0,
                busy_interval=0.1,
            ),
            poll_worker(reap, interval=5.0),
            poll_worker(cleanup_temp, interval=60.0),
        )


if __name__ == "__main__":
    run_worker_process(run, metrics=http_metrics)
