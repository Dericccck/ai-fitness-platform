# Agent 语音对话实施说明

实施日期：2026-10-14。设计依据：[录音发送与语音回复方案](agent-voice-conversation-design.md)。

已实现录音、编辑识别文字后发送、最终回复分段朗读、自动播放开关、暂停/继续/停止/重播、
识别与音频段重试、任务归属校验、Cookie/CSRF 集成、上传边界、配额、缓存和资源释放。

## 使用

1. 服务端 `fitness-agent-service/.env` 设置 `AGENT_VOICE_ENABLED=true`、`AGENT_VOICE_API_KEY`。
   也支持服务端环境变量 `DASHSCOPE_API_KEY`。本次已在本机被 Git 忽略的 `.env` 配置凭证，权限为 0600。
2. 后端启动时读取配置。按原项目流程启动/重启 Agent、PostgreSQL、Redis 以及所需业务服务。
   依赖可用 `uv sync --frozen` 同步；新增的直接依赖是 `markdown-it-py`，锁文件已更新。
3. 启动 Web (`npm run dev`) 或重新构建部署；登录并连接 Agent。
4. 点击录音，结束后检查输入框文字再发送。开启「语音回复」会尝试自动播放新一轮完整回复；
   手动点击「朗读」无需开启自动播放开关。回复恢复后保留手动播放，不自动出声。

本次没有启动完整 Java/数据库业务栈或发布生产部署。语音功能不需要数据库迁移。

## API

所有接口位于 `/api/v1/agent/voice`，沿用当前认证和请求 ID，响应不经公共缓存。

| 接口 | 内容 |
| --- | --- |
| `GET /config` | enabled、录音时长、上传大小、朗读字符上限 |
| `POST /transcriptions` | 请求为 `audio/wav` 二进制；返回 `{text,duration_ms}` |
| `GET /replies/{run_id}` | 服务端最终结果生成 `{run_id,revision,segment_count,segments}` |
| `POST /replies/{run_id}/segments/{index}` | JSON `{revision}`，返回 `audio/wav` 字节 |

不接受客户端提供的朗读文本；当前权限范围必须与 run 归属一致。
结果未完成返回 409，过期/不存在/无权读取返回 404，内容修订不匹配返回 409。
语音错误含 code、message、request_id；429 附带 Retry-After。

## 关键实现

- `app/voice/service.py`：百炼调用、真实 WAV 验证、Markdown 解析、分段、下载域名限制、
  总超时、并发控制、内存 LRU、同片段合并、专用 Prometheus 指标。
- `app/api/routes/voice.py`：读取录音前鉴权、独立 ASR/TTS 用户和机构配额、按 run 获取规范回复。
- `src/voice/recorder.ts`：浏览器原生录音解码后转成 16 kHz 单声道 PCM16 WAV。
- `src/voice/player.ts`：单播放器，当前段+下一段预取，generation 隔离迟到结果，释放对象 URL。
- `src/voice/useVoice.ts`：权限、录音、识别、播放和生命周期管理，清理麦克风轨道及网络请求。
- `src/voice/VoiceControls.tsx`：录音计时/音量、取消/重试、语音回复开关与朗读控件。

真实百炼 TTS 返回音频可能采用约 2GB/4GB 的占位 WAV 头。本实现仅对这类有界、帧完整的
流式音频重建精确长度的 WAV；普通截断文件仍拒绝。测试覆盖了这个实际联调发现的差异。

语音出错不重新执行 Agent。原业务确认卡保持原语义，停止播放与停止生成分别处理。

## 验证记录

真实百炼：固定中文测试句通过 TTS 合成、下载归一化及 ASR 识别。该测试通过真实语音路由，
但使用进程内身份和已完成 run 夹具，不调用业务数据库或 Agent 推理。单次结果：
音频长 5.76 秒，TTS 约 1.815 秒，ASR 约 0.522 秒。延迟仅为此次样本，不是性能保证。

隔离 Chrome：使用真实 MediaRecorder/Web Audio 及生成的测试音频；登录、ASR、Agent 和
TTS API 返回使用明确夹具。已验证录音/WAV、草稿追加、不自动发送、CSRF 请求头、
一次发送、自动朗读、暂停继续、停止、取消录音、识别超时重试和 390px 布局，无控制台错误。

执行与输出：

- 后端：语音及 Agent/流式/取消/浏览器认证相关回归 73 项通过，配置/指标回归另 18 项通过（共 91 项）。
- 前端：`npm test` 27 项通过，`npm run build` 通过，`npm run test:voice-browser` 通过。
- 类型/质量：新增 Python 模块通过严格 mypy 与 Ruff，前端通过 TypeScript。
- 真实联调：`python scripts/voice_live_check.py`，需百炼凭证和测试用 FFmpeg。
- 结果文件：`output/voice/live-check.json`、`browser-check.json`、固定测试音频和界面截图。

## 运行边界

- 原录音只保留在当前页面/服务端请求内存；不存数据库或长期对象存储。
- 默认单条录音 60 秒 / 2 MiB；朗读文本最多 15,360 字符，每段最多 480 字符。
- 每实例最多 4 个供应商任务，排队有界；供应商请求与下载共用 60 秒截止时间。
- 默认 ASR 用户/机构配额为 10/120 次每分钟，TTS 为 60/600 段每分钟。
  Redis 不可用时只暂停语音昂贵请求；不会绕过配额。
- 服务端每进程 32 MiB / 10 分钟缓存，不保证跨实例或重启后只计费一次。
  已发出的供应商请求在停止后可能完成并计费；不会继续预取后续段。
- 当前按现有 run 保留期（默认 24 小时）朗读，长期历史朗读尚未接入独立消息读取接口。
- 指标记录调用结果、时长、录音秒数、合成字符和缓存命中；费用以百炼账单为准。
  本次没有增加语音费用持久化账本，也不把未知费用记为零。
- 通过浏览器能力检测与类型探测提供降级；macOS Safari、Android/iOS 实机、微信 WebView
  尚未逐一验收，390px Chrome 视口不能替代这些实机测试。
- Nginx 新增语音路径的 2 MiB 请求体与超时限制；完整部署仍需 HTTPS 和原登录配置。

启用/关闭 `AGENT_VOICE_ENABLED` 后重启后端即可切换功能，前端文字对话继续使用原链路。
