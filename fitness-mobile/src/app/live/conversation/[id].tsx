import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  AppState,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather, MaterialCommunityIcons } from "@expo/vector-icons";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import * as Crypto from "expo-crypto";
import { File } from "expo-file-system";
import { C, S, TopBar } from "../../../components/ui";
import { MediaPreview } from "../../../components/MediaPreview";
import { confirmAction } from "../../../components/confirm";
import {
  DEFAULT_QUESTION,
  MediaItem,
  validateSelection,
} from "../../../domain/types";
import {
  ApiError,
  mobileApi,
  type RemoteMedia,
  type RemoteMessage,
  type RemoteReport,
  type RemoteRun,
} from "../../../network/client";
import {
  clearPending,
  loadPending,
  savePending,
  type PendingSend,
} from "../../../network/pending";
import { useAccount } from "../../../state/AccountContext";
import { desktopSimulation } from "../../../network/vault";

const active = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);
const failures: Record<string, string> = {
  AUTH_EXPIRED: "登录状态已失效，请重新登录后再试。",
  MEDIA_UNAVAILABLE: "素材已失效、被删除或暂时不可访问，请重新选择。",
  MEMORY_CHANGED: "对话设置已变化，请重新发起分析。",
  MODEL_FAILED: "分析服务暂时不可用，请稍后再试。",
  EXECUTION_TIMEOUT: "任务等待超时，请稍后重新分析。",
  INTERNAL_ERROR: "服务暂时不可用，请稍后再试。",
};

export default function LiveConversation() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { loggedIn } = useAccount();
  const [messages, setMessages] = useState<RemoteMessage[]>([]);
  const [states, setStates] = useState<Record<string, RemoteRun>>({});
  const [question, setQuestion] = useState("");
  const [draft, setDraft] = useState<MediaItem[]>([]);
  const [reference, setReference] = useState<RemoteMedia[]>([]);
  const [pending, setPending] = useState<PendingSend | null>(null);
  const [working, setWorking] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [olderCursor, setOlderCursor] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [preview, setPreview] = useState<MediaItem | null>(null);
  const [previewHeaders, setPreviewHeaders] = useState<Record<string, string> | undefined>();
  const disposablePreview = useRef<string | null>(null);
  const [revision, setRevision] = useState(0);
  const sending = useRef(false);
  const stopRequested = useRef(false);
  const older = useRef<RemoteMessage[]>([]);
  const cursor = useRef<string | null>(null);
  const loadedOlder = useRef(false);

  const closePreview = useCallback(() => {
    if (disposablePreview.current) URL.revokeObjectURL(disposablePreview.current);
    disposablePreview.current = null;
    setPreviewHeaders(undefined);
    setPreview(null);
  }, []);
  useEffect(() => () => {
    if (disposablePreview.current) URL.revokeObjectURL(disposablePreview.current);
  }, []);

  useFocusEffect(
    useCallback(() => {
      if (!loggedIn) return;
      let alive = true;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let refreshing = false;
      let failures = 0;
      const version = mobileApi.version;
      older.current = [];
      loadedOlder.current = false;
      cursor.current = null;
      setOlderCursor(null);
      async function refresh() {
        if (!alive || version !== mobileApi.version || refreshing) return;
        if (AppState.currentState !== "active") {
          timer = setTimeout(refresh, 5000);
          return;
        }
        refreshing = true;
        try {
          const page = await mobileApi.messagesPage(id);
          const history = page.messages;
          const waiting = history.filter(
            (m) => m.role === "assistant" && m.status === "PENDING",
          );
          const runResults = await Promise.all(
            waiting.map(async (item) => {
              try {
                return await mobileApi.run(item.run_id);
              } catch (e) {
                if (e instanceof ApiError && e.status === 404)
                  return {
                    run_id: item.run_id,
                    status: "EXPIRED",
                    response: null,
                    error_code: null,
                  } as RemoteRun;
                throw e;
              }
            }),
          );
          if (!alive || version !== mobileApi.version) return;
          failures = 0;
          setPollError("");
          if (!loadedOlder.current) {
            cursor.current = page.next_cursor;
            setOlderCursor(page.next_cursor);
          }
          const newestIds = new Set(history.map((item) => item.message_id));
          setMessages([...older.current.filter((item) => !newestIds.has(item.message_id)), ...history]);
          setStates(
            Object.fromEntries(runResults.map((run) => [run.run_id, run])),
          );
          const scope = mobileApi.scope;
          if (scope) {
            const saved = await loadPending(scope);
            if (saved?.conversationId === id) {
              try {
                await mobileApi.run(saved.runId);
                await clearPending(scope, saved.runId);
                if (alive && version === mobileApi.version) setPending(null);
              } catch (e) {
                if (alive && e instanceof ApiError && e.status === 404)
                  setPending(saved);
              }
            }
          }
          if (runResults.some((run) => active.has(run.status)))
            timer = setTimeout(refresh, 4000);
        } catch (reason) {
          if (alive && version === mobileApi.version) {
            failures += 1;
            setPollError(reason instanceof Error ? reason.message : "无法读取会话。");
            if (failures <= 6) timer = setTimeout(refresh, Math.min(30000, 2000 * 2 ** (failures - 1)));
          }
        } finally {
          refreshing = false;
        }
      }
      void refresh();
      const sub = AppState.addEventListener("change", (state) => {
        if (state === "active") {
          if (timer) clearTimeout(timer);
          void refresh();
        }
      });
      return () => {
        alive = false;
        if (timer) clearTimeout(timer);
        sub.remove();
      };
    // revision intentionally restarts polling after a submission or deletion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [id, loggedIn, revision]),
  );

  async function loadOlder() {
    const before = cursor.current;
    if (!before || loadingOlder) return;
    const version = mobileApi.version;
    setLoadingOlder(true);
    try {
      const page = await mobileApi.messagesPage(id, before);
      if (version !== mobileApi.version) return;
      loadedOlder.current = true;
      cursor.current = page.next_cursor;
      setOlderCursor(page.next_cursor);
      const ids = new Set(older.current.map((item) => item.message_id));
      older.current = [...page.messages.filter((item) => !ids.has(item.message_id)), ...older.current];
      setMessages((current) => {
        const existing = new Set(current.map((item) => item.message_id));
        return [...page.messages.filter((item) => !existing.has(item.message_id)), ...current];
      });
    } catch (reason) {
      if (version === mobileApi.version) setPollError(reason instanceof Error ? reason.message : "无法加载更早消息。");
    } finally { setLoadingOlder(false); }
  }

  async function choose(source: "library" | "photo" | "video") {
    setPickerOpen(false);
    try {
      if (
        source !== "library" &&
        !(await ImagePicker.requestCameraPermissionsAsync()).granted
      ) {
        setError("未获得相机权限，可在系统设置中开启或从相册选择。");
        return;
      }
      const options: ImagePicker.ImagePickerOptions = {
        mediaTypes:
          source === "photo"
            ? ["images"]
            : source === "video"
              ? ["videos"]
              : ["images", "videos"],
        allowsMultipleSelection: source === "library",
        selectionLimit: 3,
        quality: 1,
        videoMaxDuration: 120,
      };
      const selected =
        source === "library"
          ? await ImagePicker.launchImageLibraryAsync(options)
          : await ImagePicker.launchCameraAsync(options);
      if (selected.canceled) return;
      const result: MediaItem[] = selected.assets.map((asset) => {
        const info = Platform.OS === "web" ? null : new File(asset.uri).info();
        if (info && !info.exists) throw new Error("文件不可用");
        return {
          id: Crypto.randomUUID(),
          uri: asset.uri,
          kind: asset.type === "video" ? "video" : "image",
          name:
            asset.fileName ||
            (asset.type === "video" ? "video.mp4" : "image.jpg"),
          mimeType: asset.mimeType || undefined,
          size: asset.fileSize || info?.size,
          duration: asset.duration ? asset.duration * (Platform.OS === "web" ? 1000 : 1) : undefined,
        };
      });
      const problem = validateSelection(result);
      if (problem) {
        setError(problem);
        return;
      }
      setDraft(result);
      setReference([]);
      setError("");
    } catch {
      setError("无法打开素材选择器，请检查系统权限。");
    }
  }

  async function send(saved?: PendingSend) {
    if (sending.current || !mobileApi.scope) return;
    const scope = mobileApi.scope;
    const version = mobileApi.version;
    sending.current = true;
    stopRequested.current = false;
    setWorking(true);
    setError("");
    let task = saved;
    try {
      const existing = await loadPending(scope);
      if (!saved && existing)
        throw new Error("还有一条未确认的发送，请先返回对应对话继续或清除。");
      if (!task) {
        const issue = validateSelection(draft);
        if (issue) throw new Error(issue);
        if (!draft.length && !reference.length)
          throw new Error("请选择图片或视频。");
        task = {
          conversationId: id,
          runId: Crypto.randomUUID(),
          clientMessageId: Crypto.randomUUID(),
          question: question.trim() || DEFAULT_QUESTION,
          media: [...draft],
          uploadKeys: draft.map(() => Crypto.randomUUID()),
          uploadIds: draft.map(() => ""),
          reservedAssetIds: draft.map(() => ""),
          assetIds: draft.length
            ? draft.map(() => "")
            : reference.map((item) => item.asset_id),
          createdAt: Date.now(),
        };
        await savePending(scope, task, () => version === mobileApi.version);
        setPending(task);
      }
      if (Date.now() - task.createdAt > 7 * 86400000)
        throw new Error("待发送内容已超过恢复期限，请清除后重新发送。");
      for (let index = 0; index < task.media.length; index++) {
        if (task.assetIds[index]) continue;
        const current = task;
        const wasRegistered = Boolean(current.uploadIds[index]);
        setStatus(`正在上传 ${index + 1}/${current.media.length}`);
        const assetId = await mobileApi.upload(
          current.media[index],
          id,
          current.uploadKeys[index],
          (value) => {
            if (version === mobileApi.version)
              setStatus(
                `正在上传 ${index + 1}/${current.media.length} · ${Math.round(value * 100)}%`,
              );
          },
          async (uploadId, reservedAssetId) => {
            if (version !== mobileApi.version) throw new Error("账户已变化。");
            current.uploadIds[index] = uploadId;
            current.reservedAssetIds[index] = reservedAssetId;
            await savePending(
              scope,
              current,
              () => version === mobileApi.version,
            );
            if (stopRequested.current) throw new Error("上传已停止。");
          },
          wasRegistered,
        );
        if (version !== mobileApi.version || stopRequested.current)
          throw new Error("上传已停止。");
        current.assetIds[index] = assetId;
        await savePending(scope, current, () => version === mobileApi.version);
      }
      if (stopRequested.current) throw new Error("上传已停止。");
      setStatus("正在提交分析…");
      await mobileApi.submit(
        id,
        task.runId,
        task.clientMessageId,
        task.question,
        task.assetIds,
      );
      if (version !== mobileApi.version) throw new Error("账户已变化。");
      await clearPending(scope, task.runId);
      setPending(null);
      setQuestion("");
      setDraft([]);
      setReference([]);
      setStatus("");
      setRevision((v) => v + 1);
    } catch (reason) {
      if (version === mobileApi.version) {
        if (task) setPending({ ...task });
        setStatus("");
        setError(
          reason instanceof Error ? reason.message : "发送未完成，请重试。",
        );
      }
    } finally {
      sending.current = false;
      if (version === mobileApi.version) setWorking(false);
    }
  }

  async function discardPending() {
    if (!pending || working) return;
    try {
      for (const asset of pending.reservedAssetIds.filter(Boolean))
        await mobileApi.removeMedia(asset);
      const scope = mobileApi.scope;
      if (scope) await clearPending(scope, pending.runId);
      setPending(null);
      setError("");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "清理未完成，请重试。",
      );
    }
  }
  async function previewMedia(media: RemoteMedia) {
    try {
      const result = await mobileApi.preview(media.asset_id);
      closePreview();
      if (result.disposable) disposablePreview.current = result.uri;
      setPreviewHeaders(result.headers);
      setPreview({
        id: media.asset_id,
        uri: result.uri,
        kind: media.kind,
        name: media.kind === "video" ? "视频" : "图片",
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "素材暂时无法查看。");
    }
  }
  async function deleteMedia(media: RemoteMedia) {
    try {
      await mobileApi.removeMedia(media.asset_id);
      if (preview?.id === media.asset_id) closePreview();
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除未完成。");
    }
  }
  async function cancel(runId: string) {
    try {
      await mobileApi.cancel(runId, Crypto.randomUUID());
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "停止状态未确认。");
    }
  }
  const busy =
    working || Object.values(states).some((run) => active.has(run.status));
  if (!loggedIn)
    return (
      <SafeAreaView style={S.screen}>
        <TopBar title="动作分析" />
        <View style={styles.page}>
          <Text style={S.body}>请先登录。</Text>
        </View>
      </SafeAreaView>
    );
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      <TopBar title="动作分析" subtitle={desktopSimulation ? "本机联调 · 测试身份" : "真实服务"} />
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.page}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.intro}>
            <View style={styles.avatar}>
              <MaterialCommunityIcons
                name="dumbbell"
                size={17}
                color={C.bright}
              />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>从一个问题开始</Text>
              <Text style={styles.muted}>
                图片或视频原文件会上传到私有存储，并提供给模型服务分析；文件可能包含声音和位置信息。请先确认素材适合上传。
              </Text>
            </View>
          </View>
          {olderCursor ? <Pressable accessibilityRole="button" disabled={loadingOlder} onPress={() => { void loadOlder(); }}>
            <Text style={styles.link}>{loadingOlder ? "正在加载…" : "加载更早消息"}</Text>
          </Pressable> : null}
          {messages.map((message) => {
            const run = states[message.run_id];
            if (message.role === "user")
              return (
                <View key={message.message_id} style={styles.user}>
                  {message.source === "legacy" ? <Text style={styles.userLink}>过往文本对话</Text> : null}
                  <Text style={styles.userText}>
                    {typeof message.content === "string" ? message.content : ""}
                  </Text>
                  {message.media.map((media) => (
                    <View key={media.asset_id} style={styles.mediaRow}>
                      <Pressable onPress={() => previewMedia(media)}>
                        <Text style={styles.userLink}>
                          {media.kind === "video" ? "查看视频" : "查看图片"}
                        </Text>
                      </Pressable>
                      <Pressable
                        onPress={() =>
                          confirmAction(
                            "删除素材",
                            "素材及依赖它的分析和后续内容将无法查看，存储版本会异步清理；已签发的预览链接可能在短时间内仍可使用。",
                            () => {
                              void deleteMedia(media);
                            },
                          )
                        }
                      >
                        <Text style={styles.userLink}>删除</Text>
                      </Pressable>
                    </View>
                  ))}
                </View>
              );
            if (message.source === "legacy" && typeof message.content === "string")
              return (
                <View key={message.message_id} style={styles.answer}>
                  <Text style={styles.tag}>过往文本对话</Text>
                  <Text style={styles.title}>{message.content}</Text>
                </View>
              );
            const report =
              typeof message.content === "object" && message.content
                ? (message.content as RemoteReport)
                : run?.response?.report;
            if (report)
              return (
                <View key={message.message_id} style={styles.answer}>
                  <Text style={styles.tag}>动作分析</Text>
                  <Text style={styles.title}>{report.summary}</Text>
                  {report.observations.slice(0, 3).map((item) => (
                    <Text key={item.id} style={styles.muted}>
                      • {item.description}
                    </Text>
                  ))}
                  <Pressable
                    onPress={() =>
                      router.push({
                        pathname: "/live/report/[id]",
                        params: { id: message.run_id, conversationId: id },
                      })
                    }
                  >
                    <Text style={styles.link}>查看完整报告 →</Text>
                  </Pressable>
                  <Pressable
                    onPress={() => {
                      const original = messages.find(
                        (item) =>
                          item.run_id === message.run_id &&
                          item.role === "user",
                      );
                      setReference(original?.media || []);
                      setDraft([]);
                    }}
                  >
                    <Text style={styles.link}>继续分析这些素材</Text>
                  </Pressable>
                </View>
              );
            const label =
              run?.status === "QUEUED"
                ? "排队中…"
                : run?.status === "RUNNING"
                  ? "正在分析素材…"
                  : run?.status === "CANCEL_REQUESTED"
                    ? "正在停止…"
                    : run?.status === "CANCELLED"
                      ? "已停止"
                      : run?.status === "UNKNOWN"
                        ? "本次结果未确认；重新分析可能产生额外费用。"
                        : run?.status === "FAILED"
                          ? failures[run.error_code || ""] || "分析未完成，请稍后重试。"
                          : run?.status === "EXPIRED"
                            ? "任务已过期"
                            : "正在准备…";
            return (
              <View key={message.message_id} style={styles.answer}>
                <Text style={styles.title}>{label}</Text>
                {run && active.has(run.status) && (
                  <Pressable onPress={() => cancel(message.run_id)}>
                    <Text style={styles.link}>停止分析</Text>
                  </Pressable>
                )}
                {run &&
                  ["FAILED", "UNKNOWN", "CANCELLED"].includes(run.status) && (
                    <Pressable
                      onPress={() => {
                        const original = messages.find(
                          (item) =>
                            item.run_id === message.run_id &&
                            item.role === "user",
                        );
                        setReference(original?.media || []);
                        setQuestion(
                          typeof original?.content === "string"
                            ? original.content
                            : "",
                        );
                      }}
                    >
                      <Text style={styles.link}>重新分析</Text>
                    </Pressable>
                  )}
              </View>
            );
          })}
          {pending && (
            <View style={styles.pending}>
              <Text style={styles.title}>有一条发送尚未确认</Text>
              <Text style={styles.muted}>
                已保留任务标识与素材信息。临时文件失效时需要重新选择。
              </Text>
              <View style={styles.actions}>
                <Pressable disabled={working} onPress={() => send(pending)}>
                  <Text style={styles.link}>继续发送</Text>
                </Pressable>
                <Pressable
                  disabled={working}
                  onPress={() =>
                    confirmAction(
                      "清除待发送内容",
                      "已创建的素材将进入清理流程。",
                      () => {
                        void discardPending();
                      },
                    )
                  }
                >
                  <Text style={styles.link}>清除</Text>
                </Pressable>
              </View>
            </View>
          )}
        </ScrollView>
        <View style={styles.composerWrap}>
          {status ? (
            <View style={styles.actions}>
              <Text style={styles.status}>{status}</Text>
              {working && status.startsWith("正在上传") && (
                <Pressable
                  onPress={() => {
                    stopRequested.current = true;
                    mobileApi.abortUploads();
                  }}
                >
                  <Text style={styles.link}>停止上传</Text>
                </Pressable>
              )}
            </View>
          ) : null}
          {error ? (
            <Text accessibilityLiveRegion="polite" style={styles.error}>
              {error}
            </Text>
          ) : null}
          {pollError ? (
            <View style={styles.actions}>
              <Text accessibilityLiveRegion="polite" style={styles.error}>{pollError}</Text>
              <Pressable accessibilityRole="button" onPress={() => setRevision((v) => v + 1)}>
                <Text style={styles.link}>刷新状态</Text>
              </Pressable>
            </View>
          ) : null}
          {reference.length > 0 && (
            <View style={styles.reference}>
              <Text style={styles.refText}>
                继续分析：{reference.length} 个素材
              </Text>
              <Pressable
                accessibilityLabel="移除关联素材"
                onPress={() => setReference([])}
              >
                <Feather name="x" size={16} color={C.green} />
              </Pressable>
            </View>
          )}
          {draft.length > 0 && (
            <View style={styles.draftRow}>
              {draft.map((item) => (
                <View style={styles.draft} key={item.id}>
                  <Pressable onPress={() => { closePreview(); setPreview(item); }}>
                    <Text numberOfLines={1} style={styles.refText}>
                      {item.kind === "image" ? "图片" : "视频"} · {item.name}
                    </Text>
                  </Pressable>
                  <Pressable
                    accessibilityLabel="移除素材"
                    onPress={() =>
                      setDraft((old) => old.filter((x) => x.id !== item.id))
                    }
                  >
                    <Feather name="x" size={15} color={C.green} />
                  </Pressable>
                </View>
              ))}
            </View>
          )}
          <View style={styles.composer}>
            <Pressable
              accessibilityLabel="添加素材"
              accessibilityRole="button"
              onPress={() => setPickerOpen(true)}
              style={styles.iconButton}
            >
              <Feather name="plus" size={21} color={C.green} />
            </Pressable>
            <TextInput
              value={question}
              onChangeText={setQuestion}
              placeholder="输入你的问题…"
              placeholderTextColor="#9ba9a0"
              style={styles.input}
              multiline
              maxLength={4000}
            />
            <Pressable
              accessibilityLabel="发送"
              accessibilityRole="button"
              disabled={
                busy || !!pending || (!draft.length && !reference.length)
              }
              onPress={() => send()}
              style={[
                styles.send,
                (busy || !!pending || (!draft.length && !reference.length)) && {
                  opacity: 0.4,
                },
              ]}
            >
              <Feather name="arrow-up" size={20} color="white" />
            </Pressable>
          </View>
          <Text style={styles.footer}>
            动作建议仅供训练参考，不替代专业指导
          </Text>
        </View>
      </KeyboardAvoidingView>
      <Modal
        visible={pickerOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setPickerOpen(false)}
      >
        <View style={styles.modal}>
          <Pressable style={{ flex: 1 }} onPress={() => setPickerOpen(false)} />
          <View style={styles.sheet}>
            <Text style={styles.title}>添加训练素材</Text>
            <Text style={styles.muted}>最多 3 张图片，或 1 个视频</Text>
            {[
              { label: "从相册选择", source: "library" as const },
              { label: "拍摄照片", source: "photo" as const },
              { label: "拍摄视频", source: "video" as const },
            ].map((item) => (
              <Pressable
                style={styles.option}
                key={item.source}
                onPress={() => choose(item.source)}
              >
                <Text style={styles.title}>{item.label}</Text>
                <Feather name="chevron-right" size={18} color={C.muted} />
              </Pressable>
            ))}
            <Pressable
              style={styles.option}
              onPress={() => setPickerOpen(false)}
            >
              <Text style={styles.link}>取消</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
      <MediaPreview
        media={preview}
        headers={previewHeaders}
        close={closePreview}
        caption={
          preview?.uri.startsWith("https://")
            ? "通过短期授权地址查看"
            : "在本机预览，发送后才会上传"
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  page: { padding: 20, paddingBottom: 30 },
  intro: {
    flexDirection: "row",
    gap: 12,
    backgroundColor: "white",
    borderRadius: 20,
    padding: 16,
  },
  avatar: {
    width: 34,
    height: 34,
    borderRadius: 12,
    backgroundColor: C.green,
    alignItems: "center",
    justifyContent: "center",
  },
  title: { color: C.ink, fontSize: 14, fontWeight: "800", lineHeight: 21 },
  muted: { color: C.muted, fontSize: 12, lineHeight: 20, marginTop: 8 },
  user: {
    alignSelf: "flex-end",
    backgroundColor: C.green,
    borderRadius: 18,
    padding: 14,
    marginTop: 16,
    maxWidth: "85%",
  },
  userText: { color: "white", fontSize: 13, lineHeight: 20 },
  mediaRow: { flexDirection: "row", gap: 18, marginTop: 10 },
  userLink: { color: C.bright, fontSize: 11, fontWeight: "700" },
  answer: {
    backgroundColor: "white",
    borderRadius: 20,
    padding: 16,
    marginTop: 12,
    marginRight: 24,
  },
  tag: { color: "#75946c", fontSize: 10, fontWeight: "800", marginBottom: 8 },
  link: { color: C.green, fontSize: 12, fontWeight: "800", marginTop: 12 },
  pending: {
    backgroundColor: "#fbf1dc",
    borderRadius: 18,
    padding: 16,
    marginTop: 15,
  },
  actions: { flexDirection: "row", gap: 24 },
  composerWrap: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: "white",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
  },
  status: { color: C.green, fontSize: 11, marginBottom: 8 },
  error: { color: "#a34a34", fontSize: 11, lineHeight: 17, marginBottom: 8 },
  reference: {
    backgroundColor: C.soft,
    borderRadius: 11,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    padding: 8,
    marginBottom: 8,
  },
  refText: { color: C.green, fontSize: 11, maxWidth: 180 },
  draftRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginBottom: 8 },
  draft: {
    backgroundColor: C.soft,
    borderRadius: 10,
    padding: 8,
    flexDirection: "row",
    gap: 8,
  },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8 },
  iconButton: {
    width: 40,
    height: 40,
    backgroundColor: C.soft,
    borderRadius: 13,
    alignItems: "center",
    justifyContent: "center",
  },
  input: {
    flex: 1,
    minHeight: 40,
    maxHeight: 100,
    paddingVertical: 9,
    color: C.ink,
  },
  send: {
    width: 40,
    height: 40,
    backgroundColor: C.green,
    borderRadius: 13,
    alignItems: "center",
    justifyContent: "center",
  },
  footer: { color: C.muted, fontSize: 9, textAlign: "center", marginTop: 7 },
  modal: { flex: 1, backgroundColor: "#0a1b1580" },
  sheet: {
    backgroundColor: "white",
    padding: 24,
    paddingBottom: 35,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
  },
  option: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 17,
    borderBottomWidth: 1,
    borderBottomColor: C.line,
  },
});
