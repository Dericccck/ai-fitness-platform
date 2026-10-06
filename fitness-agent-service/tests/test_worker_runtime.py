import asyncio
import signal
import subprocess
import sys

from app.core.worker_runtime import poll_worker


async def test_sigterm_stop_finishes_batch_and_never_claims_next() -> None:
    stop = asyncio.Event()
    entered = asyncio.Event()
    release = asyncio.Event()
    completed: list[int] = []

    async def run_once() -> None:
        entered.set()
        await release.wait()
        completed.append(1)

    task = asyncio.create_task(poll_worker(run_once, interval=10, stop=stop))
    await entered.wait()
    stop.set()
    assert not task.done()
    release.set()
    await task
    assert completed == [1]


async def test_idle_worker_shutdown_does_not_wait_for_poll_interval() -> None:
    stop = asyncio.Event()
    ran = asyncio.Event()

    async def run_once() -> None:
        ran.set()

    task = asyncio.create_task(poll_worker(run_once, interval=100, stop=stop))
    await ran.wait()
    stop.set()
    await asyncio.wait_for(task, timeout=0.1)


def test_real_sigterm_process_finishes_inflight_batch_and_exits() -> None:
    code = """
import asyncio
from app.core.worker_runtime import _run_process, poll_worker
async def batch():
    print('CLAIMED', flush=True)
    await asyncio.sleep(0.2)
    print('COMMITTED', flush=True)
async def run():
    await poll_worker(batch, interval=100)
asyncio.run(_run_process(run, timeout=2))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout.readline().strip() == "CLAIMED"
        process.send_signal(signal.SIGTERM)
        output, errors = process.communicate(timeout=5)
        assert process.returncode == 0, errors
        assert output.count("COMMITTED") == 1 and "CLAIMED" not in output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
