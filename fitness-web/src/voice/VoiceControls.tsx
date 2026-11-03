import type { VoiceConversation } from "./useVoice";

export function VoiceComposer({ voice, disabled }: { voice: VoiceConversation; disabled: boolean }) {
  const { recording, config } = voice;
  const capturing = recording.status === "recording";
  return <div className="voice-composer">
    <div className="voice-toolbar">
      <button type="button" className={capturing ? "voice-record is-recording" : "voice-record"}
        disabled={disabled || !config?.enabled || (voice.busy && !capturing)}
        onClick={() => capturing ? voice.finishRecording() : void voice.startRecording()}>
        <span aria-hidden="true">{capturing ? "■" : "●"}</span> {capturing ? "结束录音" : "录音"}
      </button>
      {voice.busy && <button type="button" className="voice-secondary" onClick={() => voice.cancelRecording()}>取消</button>}
      <label className="voice-toggle"><input type="checkbox" checked={voice.autoplay}
        disabled={!config?.enabled} onChange={event => voice.setVoiceReplies(event.target.checked)} />语音回复</label>
      {capturing && <><span className="voice-timer">{recording.seconds}s / {config?.max_recording_seconds}s</span>
        <meter aria-label="麦克风音量" min={0} max={1} value={recording.level} /></>}
      {voice.configurationError && <button type="button" className="voice-secondary" onClick={voice.refreshConfiguration}>重试连接语音</button>}
    </div>
    <div className="voice-note" role="status" aria-live="polite">
      {recording.status === "permission" ? "请允许浏览器使用麦克风" :
        recording.status === "processing" ? "正在处理录音…" : recording.status === "transcribing" ? "正在识别语音…" :
          recording.note || (config?.enabled ? "录音将发送至语音服务识别，可修改文字后发送" :
            voice.configurationError || (config ? "语音暂未启用" : "连接后可使用录音和语音回复"))}
    </div>
    {recording.error && <div className="voice-error" role="alert"><span>{recording.error}</span>
      {voice.canRetry && <button type="button" onClick={() => void voice.retryRecognition()}>重试识别</button>}
      <button type="button" disabled={disabled || !config?.enabled} onClick={() => void voice.startRecording()}>重新录音</button>
      <button type="button" onClick={() => voice.cancelRecording()}>改用文字</button>
    </div>}
  </div>;
}

export function VoiceReply({ runId, voice }: { runId: string; voice: VoiceConversation }) {
  if (!voice.config?.enabled) return null;
  const state = voice.playback;
  const selected = state.runId === runId;
  const status = selected ? state.status : "idle";
  const labels = { idle: "朗读", preparing: "语音准备中…", playing: "暂停", paused: "继续播放",
    ready: "点击播放", ended: "重新播放", error: "重试朗读" };
  return <div className="voice-reply">
    <button type="button" disabled={voice.busy || status === "preparing"} onClick={() => {
      if (status === "playing") voice.pause();
      else if (status === "paused" || status === "ready") void voice.resume();
      else if (status === "error") void voice.retry();
      else void voice.play(runId);
    }}>{labels[status]}</button>
    {selected && !["idle", "ended"].includes(status) && <button type="button" onClick={voice.stopPlayback}>停止播放</button>}
    {selected && state.count > 0 && <span aria-live="polite">第 {state.index + 1} / {state.count} 段</span>}
    {selected && state.error && <span className="voice-error" role="alert">{state.error}</span>}
  </div>;
}
