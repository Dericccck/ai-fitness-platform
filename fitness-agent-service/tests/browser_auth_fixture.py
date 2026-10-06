"""仅用于浏览器交互验收的合成夹具，绝不能部署为正式登录服务。

从服务目录运行：uvicorn --app-dir tests browser_auth_fixture:app --host 127.0.0.1 --port 18090
前端设置 FITNESS_DEV_API_URL=http://127.0.0.1:18090，使用 18080 端口。
认证中间件/路由使用真实实现；身份签发、Redis 和业务回答是测试替身。
"""

import json

from fastapi import Request
from starlette.responses import StreamingResponse
from test_browser_auth import build

app, auth, _calls = build()
auth.settings.browser_auth_cookie_secure = False
auth.settings.browser_auth_allowed_origins = ["http://127.0.0.1:18080"]


@app.get("/api/v1/agent/capabilities")
async def capabilities() -> dict:
    return {
        "catalog_version": "synthetic-browser-fixture",
        "roles": ["STUDENT"],
        "items": [
            {
                "id": "fixture.fitness_knowledge",
                "display_name": "合成验收：健身知识",
                "domain": "TRAINING",
                "description": "仅验证界面，不访问模型或业务数据库。",
                "allowed_roles": ["STUDENT"],
                "read_only": True,
                "requires_confirmation": False,
                "confirmation_action": None,
            }
        ],
    }


@app.post("/api/v1/agent/chat/stream")
async def chat(request: Request) -> StreamingResponse:
    payload = await request.json()
    run = payload["run_id"]
    answer = "合成验收回答：登录、自动刷新、CSRF 与 SSE 界面连接成功；这不是实际模型评测。"

    async def events():
        for sequence, (event, data) in enumerate(
            [
                ("answer.delta", {"text": answer}),
                (
                    "run.completed",
                    {
                        "response": {
                            "conversation_id": payload["conversation_id"],
                            "answer": answer,
                            "route": "FITNESS_COACHING",
                            "tool_steps": 0,
                            "input_tokens": None,
                            "output_tokens": None,
                        }
                    },
                ),
            ],
            start=1,
        ):
            envelope = {"run_id": run, "sequence": sequence, "event": event, "data": data}
            yield f"event: {event}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
