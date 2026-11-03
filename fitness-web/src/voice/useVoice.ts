import { useEffect, useRef, useState } from "react";
import { getVoiceConfig, getVoiceManifest, getVoiceSegment, transcribeRecording } from "../api";
import type { VoiceConfig } from "../types";
import { VoicePlayer, idlePlayback } from "./player";
import { normalizeRecording, recordingError, recordingSupport } from "./recorder";

export type RecordingState = {
  status: "idle" | "permission" | "recording" | "processing" | "transcribing" | "error";
  seconds: number;
  level: number;
  error: string;
  note: string;
};
const idleRecording: RecordingState = { status: "idle", seconds: 0, level: 0, error: "", note: "" };

export function useVoice(connected: boolean, active: boolean, onTranscript: (text: string) => void) {
  const [config, setConfig] = useState<VoiceConfig | null>(null);
  const [configurationError, setConfigurationError] = useState("");
  const [configVersion, setConfigVersion] = useState(0);
  const [recording, setRecording] = useState<RecordingState>({ ...idleRecording });
  const [playback, setPlayback] = useState({ ...idlePlayback });
  const [autoplay, setAutoplay] = useState(() => {
    try { return localStorage.getItem("fitness.voice-replies") === "true"; } catch { return false; }
  });
  const autoplayRef = useRef(autoplay);
  const availability = useRef({ active, connected, enabled: !!config?.enabled });
  availability.current = { active, connected, enabled: !!config?.enabled };
  const transcriptRef = useRef(onTranscript); transcriptRef.current = onTranscript;
  const mounted = useRef(false);
  const recordingId = useRef(0);
  const recordingController = useRef<AbortController | null>(null);
  const media = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const timers = useRef<ReturnType<typeof setInterval>[]>([]);
  const wav = useRef<Blob | null>(null);
  const playerRef = useRef<VoicePlayer | null>(null);
  if (!playerRef.current) playerRef.current = new VoicePlayer({
    manifest: getVoiceManifest, segment: getVoiceSegment,
    audio: () => new Audio(), objectUrl: blob => URL.createObjectURL(blob),
    revokeUrl: url => URL.revokeObjectURL(url), changed: state => { if (mounted.current) setPlayback(state); },
  });
  const player = playerRef.current;

  function releaseCapture() {
    for (const timer of timers.current) clearInterval(timer);
    timers.current = [];
    stream.current?.getTracks().forEach(track => { track.onended = null; track.stop(); });
    stream.current = null;
    if (audioContext.current) void audioContext.current.close().catch(() => {});
    audioContext.current = null;
  }

  function cancelRecording(notify = true) {
    recordingId.current++; recordingController.current?.abort(); recordingController.current = null;
    const recorder = media.current; media.current = null;
    if (recorder) {
      recorder.ondataavailable = null; recorder.onstop = null; recorder.onerror = null;
      if (recorder.state !== "inactive") recorder.stop();
    }
    releaseCapture(); wav.current = null;
    if (notify && mounted.current) setRecording({ ...idleRecording });
  }

  async function recognize(audio: Blob, id: number, controller: AbortController) {
    if (id !== recordingId.current || !mounted.current) return;
    setRecording(current => ({ ...current, status: "transcribing", error: "" }));
    try {
      const result = await transcribeRecording(audio, controller.signal);
      if (id !== recordingId.current || !mounted.current) return;
      transcriptRef.current(result.text); wav.current = null;
      setRecording({ ...idleRecording, note: "语音已转为文字，可修改后发送" });
    } catch (reason) {
      if (id !== recordingId.current || controller.signal.aborted || !mounted.current) return;
      setRecording(current => ({ ...current, status: "error", error: recordingError(reason) }));
    }
  }

  function finishRecording() {
    const recorder = media.current;
    if (!recorder || recorder.state === "inactive") return;
    recorder.stop(); releaseCapture();
    setRecording(current => ({ ...current, status: "processing", level: 0 }));
  }

  async function startRecording() {
    if (!config?.enabled || !active || !connected) return;
    const unsupported = recordingSupport();
    if (unsupported) { setRecording({ ...idleRecording, status: "error", error: unsupported }); return; }
    player.stop(); cancelRecording();
    const id = recordingId.current;
    const controller = new AbortController(); recordingController.current = controller;
    setRecording({ ...idleRecording, status: "permission" });
    try {
      const capture = await navigator.mediaDevices.getUserMedia({ audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      } });
      if (id !== recordingId.current || !mounted.current || controller.signal.aborted) {
        capture.getTracks().forEach(track => track.stop()); return;
      }
      stream.current = capture;
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"]
        .find(type => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(capture, mime ? { mimeType: mime } : undefined);
      media.current = recorder;
      const chunks: Blob[] = []; let bytes = 0;
      recorder.ondataavailable = event => {
        if (id !== recordingId.current) return;
        bytes += event.data.size;
        if (bytes > 8 * 1024 * 1024) {
          cancelRecording(); setRecording({ ...idleRecording, status: "error", error: "录音过大，请缩短后重录" }); return;
        }
        if (event.data.size) chunks.push(event.data);
      };
      recorder.onerror = () => {
        cancelRecording(); setRecording({ ...idleRecording, status: "error", error: "录音中断，请检查麦克风后重录" });
      };
      recorder.onstop = () => {
        releaseCapture(); media.current = null;
        void (async () => {
          try {
            const normalized = await normalizeRecording(new Blob(chunks, { type: recorder.mimeType }), config.max_recording_seconds, controller.signal);
            if (id !== recordingId.current || !mounted.current) return;
            if (normalized.size > config.max_upload_bytes) throw new Error("录音过大，请缩短后重录");
            wav.current = normalized;
            await recognize(normalized, id, controller);
          } catch (reason) {
            if (id !== recordingId.current || controller.signal.aborted || !mounted.current) return;
            setRecording(current => ({ ...current, status: "error", error: recordingError(reason) }));
          }
        })();
      };
      capture.getTracks().forEach(track => { track.onended = () => {
        cancelRecording(); setRecording({ ...idleRecording, status: "error", error: "麦克风已断开，请重新录音" });
      }; });
      const began = performance.now();
      recorder.start(250);
      setRecording({ ...idleRecording, status: "recording" });
      timers.current.push(setInterval(() => {
        if (id !== recordingId.current || recorder.state !== "recording") return;
        const elapsed = (performance.now() - began) / 1000;
        setRecording(current => ({ ...current, seconds: Math.min(config.max_recording_seconds, Math.floor(elapsed)) }));
        if (elapsed >= config.max_recording_seconds) finishRecording();
      }, 100));
      // 音量提示是辅助信息；无法建立分析器不能中断已启动的录音。
      try {
        const context = new AudioContext(); audioContext.current = context;
        const analyser = context.createAnalyser(); analyser.fftSize = 256;
        context.createMediaStreamSource(capture).connect(analyser);
        await context.resume();
        if (id !== recordingId.current || media.current !== recorder || recorder.state !== "recording") return;
        const values = new Uint8Array(analyser.fftSize);
        timers.current.push(setInterval(() => {
          analyser.getByteTimeDomainData(values);
          const rms = Math.sqrt(values.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / values.length);
          setRecording(current => ({ ...current, level: Math.min(1, rms * 5) }));
        }, 100));
      } catch { /* 浏览器仍可通过 MediaRecorder 录音。 */ }
    } catch (reason) {
      if (id !== recordingId.current || !mounted.current) return;
      releaseCapture(); setRecording({ ...idleRecording, status: "error", error: recordingError(reason) });
    }
  }

  async function retryRecognition() {
    if (!wav.current) return;
    recordingController.current?.abort();
    const controller = new AbortController(); recordingController.current = controller;
    await recognize(wav.current, recordingId.current, controller);
  }

  function setVoiceReplies(value: boolean) {
    autoplayRef.current = value; setAutoplay(value);
    try { localStorage.setItem("fitness.voice-replies", String(value)); } catch { /* 偏好可只保留在当前页面。 */ }
    if (!value) player.stop();
  }

  useEffect(() => {
    mounted.current = true;
    const hide = () => {
      if (document.hidden) { cancelRecording(); player.stop(); }
    };
    const changed = () => { cancelRecording(); player.stop(); setConfig(null); };
    window.addEventListener("fitness.scope-changed", changed);
    document.addEventListener("visibilitychange", hide);
    return () => {
      mounted.current = false; cancelRecording(false); player.stop(false);
      window.removeEventListener("fitness.scope-changed", changed);
      document.removeEventListener("visibilitychange", hide);
    };
  }, []);

  useEffect(() => {
    if (!connected) { setConfig(null); return; }
    const controller = new AbortController();
    setConfigurationError("");
    getVoiceConfig(controller.signal).then(value => {
      if (!controller.signal.aborted) setConfig(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setConfigurationError(recordingError(reason));
    });
    return () => controller.abort();
  }, [connected, configVersion]);

  useEffect(() => {
    if (!active || !connected) { cancelRecording(); player.stop(); }
  }, [active, connected]);

  return {
    config, configurationError, refreshConfiguration: () => setConfigVersion(value => value + 1),
    recording, playback, autoplay, setVoiceReplies,
    busy: ["permission", "recording", "processing", "transcribing"].includes(recording.status),
    startRecording, finishRecording, cancelRecording, retryRecognition, canRetry: !!wav.current,
    clearNote: () => { wav.current = null; setRecording({ ...idleRecording }); },
    play: (runId: string) => player.start(runId), pause: () => player.pause(),
    resume: () => player.resume(), retry: () => player.retry(), stopPlayback: () => player.stop(),
    completed: (runId: string) => {
      const current = availability.current;
      if (autoplayRef.current && current.active && current.connected && current.enabled && mounted.current && !document.hidden) void player.start(runId);
    },
  };
}
export type VoiceConversation = ReturnType<typeof useVoice>;
