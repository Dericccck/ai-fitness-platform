export function encodeWav(samples: Float32Array, sampleRate = 16000): Blob {
  const bytes = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(bytes);
  const label = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  label(0, "RIFF"); view.setUint32(4, bytes.byteLength - 8, true); label(8, "WAVE");
  label(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); label(36, "data"); view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const value = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
  }
  return new Blob([bytes], { type: "audio/wav" });
}

export async function normalizeRecording(recording: Blob, maxSeconds: number, signal: AbortSignal): Promise<Blob> {
  signal.throwIfAborted();
  const context = new AudioContext();
  try {
    const audio = await context.decodeAudioData(await recording.arrayBuffer());
    signal.throwIfAborted();
    if (audio.duration < 0.3 || audio.duration > maxSeconds + 0.25) {
      throw new Error(`录音时长应为 0.3 至 ${maxSeconds} 秒，请重新录音`);
    }
    const frames = Math.min(Math.floor(maxSeconds * 16000), Math.ceil(audio.duration * 16000));
    const offline = new OfflineAudioContext(1, frames, 16000);
    const source = offline.createBufferSource();
    source.buffer = audio; source.connect(offline.destination); source.start();
    const normalized = await offline.startRendering();
    signal.throwIfAborted();
    return encodeWav(normalized.getChannelData(0));
  } finally { await context.close(); }
}

export function recordingSupport(): string | null {
  if (!window.isSecureContext) return "录音需要 HTTPS 或 localhost，请使用安全地址访问";
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined" ||
      typeof AudioContext === "undefined" || typeof OfflineAudioContext === "undefined") {
    return "当前浏览器不支持录音，请使用新版 Chrome、Edge 或 Safari";
  }
  return null;
}

export function recordingError(reason: unknown): string {
  if (reason instanceof DOMException) {
    if (["NotAllowedError", "SecurityError"].includes(reason.name)) return "麦克风权限未允许，请在浏览器设置中开启后重试";
    if (reason.name === "NotFoundError") return "没有找到麦克风，请连接设备后重试";
    if (reason.name === "NotReadableError") return "麦克风被占用或无法读取，请检查设备后重试";
    if (reason.name === "EncodingError") return "当前浏览器无法解析录音，请换一个浏览器或使用文字输入";
  }
  return reason instanceof Error ? reason.message : "录音处理失败，请重新录音";
}
