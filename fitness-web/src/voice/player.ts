import type { VoiceManifest } from "../types.ts";

export type PlaybackState = {
  runId: string | null;
  status: "idle" | "preparing" | "playing" | "paused" | "ready" | "ended" | "error";
  index: number;
  count: number;
  error: string;
};
export const idlePlayback: PlaybackState = { runId: null, status: "idle", index: 0, count: 0, error: "" };

type PlayerDependencies = {
  manifest: (runId: string, signal: AbortSignal) => Promise<VoiceManifest>;
  segment: (runId: string, index: number, revision: string, signal: AbortSignal) => Promise<Blob>;
  audio: () => HTMLAudioElement;
  objectUrl: (blob: Blob) => string;
  revokeUrl: (url: string) => void;
  changed: (state: PlaybackState) => void;
};

// 单播放器：generation 防止迟到响应重启旧音频；最多保留当前及下一段。
export class VoicePlayer {
  private dependencies: PlayerDependencies;
  private generation = 0;
  private controller: AbortController | null = null;
  private audio: HTMLAudioElement | null = null;
  private manifest: VoiceManifest | null = null;
  private pending = new Map<number, Promise<string>>();
  private urls = new Map<number, string>();
  private paused = false;
  state: PlaybackState = { ...idlePlayback };

  constructor(dependencies: PlayerDependencies) { this.dependencies = dependencies; }

  private update(patch: Partial<PlaybackState>) {
    this.state = { ...this.state, ...patch }; this.dependencies.changed(this.state);
  }

  stop(notify = true) {
    this.generation++; this.controller?.abort(); this.controller = null;
    if (this.audio) {
      this.audio.onended = null; this.audio.onerror = null;
      this.audio.pause(); this.audio.removeAttribute("src"); this.audio.load(); this.audio = null;
    }
    for (const url of this.urls.values()) this.dependencies.revokeUrl(url);
    this.urls.clear(); this.pending.clear(); this.manifest = null; this.paused = false;
    this.state = { ...idlePlayback };
    if (notify) this.dependencies.changed(this.state);
  }

  async start(runId: string) {
    this.stop();
    const generation = this.generation;
    this.controller = new AbortController();
    this.audio = this.dependencies.audio();
    this.update({ runId, status: "preparing", index: 0, error: "" });
    try {
      const manifest = await this.dependencies.manifest(runId, this.controller.signal);
      if (generation !== this.generation) return;
      if (!manifest.segment_count || manifest.segments.length !== manifest.segment_count) throw new Error("朗读清单无效");
      this.manifest = manifest; this.update({ count: manifest.segment_count });
      await this.playSegment(0, generation);
    } catch (reason) { this.failed(reason, generation); }
  }

  private failed(reason: unknown, generation: number) {
    if (generation !== this.generation) return;
    if (reason instanceof Error && "status" in reason && reason.status === 409) this.manifest = null;
    this.update({ status: "error", error: reason instanceof Error ? reason.message : "语音播放失败，请重试" });
  }

  private fetchSegment(index: number, generation: number): Promise<string> {
    const existing = this.urls.get(index);
    if (existing) return Promise.resolve(existing);
    const pending = this.pending.get(index);
    if (pending) return pending;
    const manifest = this.manifest!, controller = this.controller!;
    const task = this.dependencies.segment(manifest.run_id, index, manifest.revision, controller.signal)
      .then(blob => {
        if (generation !== this.generation) throw new DOMException("已停止播放", "AbortError");
        const url = this.dependencies.objectUrl(blob); this.urls.set(index, url); return url;
      }).finally(() => {
        if (generation === this.generation) this.pending.delete(index);
      });
    this.pending.set(index, task);
    return task;
  }

  private async playSegment(index: number, generation: number) {
    if (generation !== this.generation) return;
    this.update({ index, status: "preparing", error: "" });
    try {
      const url = await this.fetchSegment(index, generation);
      if (generation !== this.generation || !this.audio) return;
      // 分段播放完就淘汰旧 Blob，长答案不能无限累计浏览器内存。
      for (const [oldIndex, oldUrl] of this.urls) {
        if (oldIndex < index) { this.dependencies.revokeUrl(oldUrl); this.urls.delete(oldIndex); }
      }
      const audio = this.audio;
      audio.src = url;
      audio.onended = () => {
        if (generation !== this.generation) return;
        if (index + 1 < this.manifest!.segment_count) void this.playSegment(index + 1, generation);
        else this.update({ status: "ended" });
      };
      audio.onerror = () => this.failed(new Error("音频播放失败，请重试该段"), generation);
      if (this.paused) { this.update({ status: "paused" }); return; }
      await this.playAudio(generation);
    } catch (reason) { this.failed(reason, generation); }
  }

  private async playAudio(generation: number) {
    if (!this.audio) return;
    try {
      await this.audio.play();
      if (generation !== this.generation) return;
      if (this.paused) { this.audio.pause(); this.update({ status: "paused" }); return; }
      this.update({ status: "playing", error: "" });
      const next = this.state.index + 1;
      if (this.manifest && next < this.manifest.segment_count) {
        // 预取失败不影响当前段；切到下一段时可明确重试。
        void this.fetchSegment(next, generation).catch(() => {});
      }
    } catch (reason) {
      if (generation !== this.generation) return;
      if (reason instanceof Error && reason.name === "NotAllowedError") this.update({ status: "ready", error: "" });
      else this.failed(reason, generation);
    }
  }

  pause() {
    this.paused = true; this.audio?.pause(); this.update({ status: "paused" });
  }
  async resume() {
    this.paused = false;
    // 暂停可能发生在尚未获得音频的时候。
    if (this.audio?.src) await this.playAudio(this.generation);
    else if (this.manifest) await this.playSegment(this.state.index, this.generation);
  }
  async retry() {
    if (!this.manifest) { if (this.state.runId) await this.start(this.state.runId); return; }
    this.paused = false;
    const url = this.urls.get(this.state.index);
    if (url) { this.dependencies.revokeUrl(url); this.urls.delete(this.state.index); }
    await this.playSegment(this.state.index, this.generation);
  }
}
