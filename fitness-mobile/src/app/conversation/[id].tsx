import React, { useEffect, useRef, useState } from "react";
import {
  Image,
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
import { useLocalSearchParams, useRouter } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import * as Crypto from "expo-crypto";
import { C, IconButton, S, TopBar } from "../../components/ui";
import { MediaPreview } from "../../components/MediaPreview";
import { confirmAction } from "../../components/confirm";
import { MediaItem, validateSelection } from "../../domain/types";
import { useDemo } from "../../state/DemoContext";

const prompts = ["帮我看看这个动作", "有哪些地方可以调整？", "需要注意什么？"];
export default function ConversationScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { conversations, send, cancel, removeRun } = useDemo();
  const conversation = conversations.find((item) => item.id === id);
  const [question, setQuestion] = useState("");
  const [draft, setDraft] = useState<MediaItem[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [preview, setPreview] = useState<MediaItem | null>(null);
  const [notice, setNotice] = useState("");
  const sending = useRef(false);
  const busy =
    conversation?.runs.some(
      (r) => r.state === "QUEUED" || r.state === "RUNNING",
    ) ?? false;
  useEffect(() => {
    if (!busy) sending.current = false;
  }, [busy]);

  async function pick(source: "library" | "photo" | "video") {
    setPickerOpen(false);
    try {
      if (source !== "library") {
        const permission = await ImagePicker.requestCameraPermissionsAsync();
        if (!permission.granted) {
          setNotice("未获得相机权限，可从相册选择，或在系统设置中开启。");
          return;
        }
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
      const result =
        source === "library"
          ? await ImagePicker.launchImageLibraryAsync(options)
          : await ImagePicker.launchCameraAsync(options);
      if (result.canceled) return;
      const items: MediaItem[] = result.assets.map((asset) => ({
        id: Crypto.randomUUID(),
        uri: asset.uri,
        kind: asset.type === "video" ? "video" : "image",
        name: asset.fileName || (asset.type === "video" ? "视频" : "图片"),
        mimeType: asset.mimeType || undefined,
        size: asset.fileSize,
        duration: asset.duration || undefined,
      }));
      const error = validateSelection(items);
      if (error) {
        setNotice(error);
        return;
      }
      setNotice("");
      setDraft(items);
    } catch {
      setNotice("无法打开素材选择器，请检查系统权限后重试。");
    }
  }
  function submit() {
    if (busy || sending.current) return;
    sending.current = true;
    try {
      send(id, question, draft);
      setQuestion("");
      setDraft([]);
      setNotice("");
    } catch (error) {
      sending.current = false;
      setNotice(error instanceof Error ? error.message : "暂时无法发送。");
    }
  }
  if (!conversation)
    return (
      <SafeAreaView style={S.screen}>
        <TopBar title="对话不存在" />
        <View style={styles.missing}>
          <Text style={S.body}>这条演示对话已删除或在重启后清除。</Text>
        </View>
      </SafeAreaView>
    );
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      <TopBar
        title="动作分析"
        subtitle="演示体验 · 未连接模型"
        action={
          <IconButton
            name="more-horizontal"
            label="查看对话记录"
            onPress={() => router.push("/records")}
          />
        }
      />
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={0}
      >
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={styles.messages}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.date}>
            <Text style={styles.dateText}>今天 · 演示对话</Text>
          </View>
          <View style={styles.assistantRow}>
            <View style={styles.avatar}>
              <MaterialCommunityIcons
                name="dumbbell"
                size={18}
                color={C.bright}
              />
            </View>
            <View style={styles.intro}>
              <Text style={styles.assistantLabel}>MOVE WELL 助手</Text>
              <Text style={styles.introTitle}>你好，今天练得怎么样？</Text>
              <Text style={styles.introText}>
                选择图片或视频，再告诉我你想了解什么。{"\n"}
                当前是交互演示，素材不会上传，也不会被分析。
              </Text>
              <View style={styles.tip}>
                <Feather name="shield" size={14} color="#5b8061" />
                <Text style={styles.tipText}>演示内容仅保留在当前运行中</Text>
              </View>
            </View>
          </View>
          {conversation.runs.length === 0 && (
            <View style={styles.promptArea}>
              <Text style={styles.promptLabel}>试着这样问</Text>
              {prompts.map((p) => (
                <Pressable
                  key={p}
                  onPress={() => setQuestion(p)}
                  style={styles.prompt}
                >
                  <Text style={styles.promptText}>{p}</Text>
                  <Feather name="arrow-up-right" size={14} color={C.green} />
                </Pressable>
              ))}
            </View>
          )}
          {conversation.runs.map((run) => (
            <View key={run.id} style={styles.run}>
              <View style={styles.userBubble}>
                {run.media.length > 0 && (
                  <View style={styles.thumbs}>
                    {run.media.map((item) => (
                      <Pressable
                        key={item.id}
                        onPress={() => setPreview(item)}
                        style={styles.thumb}
                      >
                        {item.kind === "image" ? (
                          <Image
                            source={{ uri: item.uri }}
                            style={styles.thumbImage}
                          />
                        ) : (
                          <View style={styles.videoThumb}>
                            <Feather
                              name="play-circle"
                              size={25}
                              color="white"
                            />
                            <Text style={styles.videoLabel}>视频</Text>
                          </View>
                        )}
                      </Pressable>
                    ))}
                  </View>
                )}
                <Text style={styles.userText}>{run.question}</Text>
              </View>
              <View style={styles.assistantRow}>
                <View style={styles.avatarSmall}>
                  <MaterialCommunityIcons
                    name="dumbbell"
                    size={15}
                    color={C.bright}
                  />
                </View>
                <View style={styles.answer}>
                  {run.state === "COMPLETED" ? (
                    <>
                      <View style={styles.answerHeader}>
                        <Text style={styles.answerTag}>
                          示例报告 · 非真实分析
                        </Text>
                        <Feather name="star" size={14} color="#83a86c" />
                      </View>
                      <Text style={styles.answerTitle}>报告样式已准备好</Text>
                      <Text style={styles.answerBody}>
                        这里展示报告和建议的呈现方式。接入真实服务后，才会根据你提交的画面生成分析。
                      </Text>
                      <Pressable
                        onPress={() =>
                          router.push({
                            pathname: "/report/[id]",
                            params: { id: run.id, conversationId: id },
                          })
                        }
                        style={styles.reportLink}
                      >
                        <Text style={styles.reportLinkText}>查看示例报告</Text>
                        <Feather name="arrow-right" size={15} color={C.green} />
                      </Pressable>
                      <View style={styles.answerActions}>
                        <Pressable
                          onPress={() => {
                            setDraft(run.media);
                            setQuestion("请继续解释上一轮的内容");
                          }}
                        >
                          <Text style={styles.actionText}>继续提问</Text>
                        </Pressable>
                        <Pressable
                          onPress={() =>
                            confirmAction(
                              "删除演示记录",
                              "将从本次演示中移除这条记录。",
                              () => removeRun(id, run.id),
                            )
                          }
                        >
                          <Text style={styles.actionText}>删除</Text>
                        </Pressable>
                      </View>
                    </>
                  ) : run.state === "CANCELLED" ? (
                    <>
                      <Text style={styles.answerTitle}>已停止演示</Text>
                      <Pressable
                        onPress={() => {
                          setDraft(run.media);
                          setQuestion(run.question);
                        }}
                      >
                        <Text style={styles.linkText}>重新编辑</Text>
                      </Pressable>
                    </>
                  ) : (
                    <>
                      <Text style={styles.answerTitle}>
                        {run.state === "QUEUED"
                          ? "正在准备演示…"
                          : "正在展示报告流程…"}
                      </Text>
                      <Text style={styles.answerBody}>
                        不会上传文件，也没有调用模型。
                      </Text>
                      <Pressable onPress={() => cancel(id, run.id)}>
                        <Text style={styles.linkText}>停止</Text>
                      </Pressable>
                    </>
                  )}
                </View>
              </View>
            </View>
          ))}
        </ScrollView>
        <View style={styles.composerWrap}>
          {notice ? (
            <Text accessibilityLiveRegion="polite" style={styles.notice}>
              {notice}
            </Text>
          ) : null}
          {draft.length > 0 && (
            <View style={styles.drafts}>
              {draft.map((item) => (
                <View key={item.id} style={styles.draftItem}>
                  <Pressable
                    onPress={() => setPreview(item)}
                    style={{
                      flexDirection: "row",
                      alignItems: "center",
                      gap: 5,
                      flexShrink: 1,
                    }}
                  >
                    <Feather
                      name={item.kind === "image" ? "image" : "video"}
                      size={14}
                      color={C.green}
                    />
                    <Text numberOfLines={1} style={styles.draftText}>
                      {item.name}
                    </Text>
                  </Pressable>
                  <Pressable
                    accessibilityLabel="移除素材"
                    onPress={() =>
                      setDraft((old) => old.filter((x) => x.id !== item.id))
                    }
                  >
                    <Feather name="x" size={15} color={C.muted} />
                  </Pressable>
                </View>
              ))}
            </View>
          )}
          <View style={styles.composer}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="添加素材"
              onPress={() => setPickerOpen(true)}
              style={styles.add}
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
              accessibilityRole="button"
              accessibilityLabel="发送"
              disabled={busy || (!question.trim() && !draft.length)}
              onPress={submit}
              style={[
                styles.send,
                (busy || (!question.trim() && !draft.length)) && {
                  opacity: 0.4,
                },
              ]}
            >
              <Feather name="arrow-up" color="white" size={20} />
            </Pressable>
          </View>
          <Text style={styles.footer}>演示模式 · 不会上传素材或调用 AI</Text>
        </View>
      </KeyboardAvoidingView>
      <Modal
        transparent
        visible={pickerOpen}
        animationType="slide"
        onRequestClose={() => setPickerOpen(false)}
      >
        <View style={styles.modalBack}>
          <Pressable style={{ flex: 1 }} onPress={() => setPickerOpen(false)} />
          <View style={styles.sheet}>
            <View style={styles.handle} />
            <Text style={styles.sheetTitle}>添加训练素材</Text>
            <Text style={styles.sheetSub}>最多 3 张图片，或 1 个视频</Text>
            {[
              {
                label: "从相册选择",
                icon: "image" as const,
                type: "library" as const,
              },
              {
                label: "拍摄照片",
                icon: "camera" as const,
                type: "photo" as const,
              },
              {
                label: "拍摄视频",
                icon: "video" as const,
                type: "video" as const,
              },
            ].map((option) => (
              <Pressable
                key={option.type}
                style={styles.sheetOption}
                onPress={() => pick(option.type)}
              >
                <View style={styles.optionIcon}>
                  <Feather name={option.icon} size={18} color={C.green} />
                </View>
                <Text style={styles.optionText}>{option.label}</Text>
                <Feather name="chevron-right" size={18} color={C.muted} />
              </Pressable>
            ))}
            <Text style={styles.sheetHint}>
              视频可能包含声音；当前演示不会上传素材。
            </Text>
            <Pressable
              style={styles.sheetClose}
              onPress={() => setPickerOpen(false)}
            >
              <Text style={styles.optionText}>取消</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
      <MediaPreview media={preview} close={() => setPreview(null)} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  missing: { padding: 24 },
  messages: { paddingHorizontal: 20, paddingBottom: 25 },
  date: { alignItems: "center", marginVertical: 18 },
  dateText: { color: "#a5b1a9", fontSize: 10 },
  assistantRow: { flexDirection: "row", gap: 9, alignItems: "flex-start" },
  avatar: {
    width: 34,
    height: 34,
    borderRadius: 12,
    backgroundColor: C.green,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarSmall: {
    width: 30,
    height: 30,
    borderRadius: 11,
    backgroundColor: C.green,
    alignItems: "center",
    justifyContent: "center",
  },
  intro: {
    flex: 1,
    backgroundColor: "white",
    borderRadius: 20,
    borderTopLeftRadius: 6,
    padding: 17,
  },
  assistantLabel: {
    color: "#8da191",
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 1.2,
  },
  introTitle: { color: C.ink, fontSize: 16, fontWeight: "800", marginTop: 12 },
  introText: { color: "#6f7f75", fontSize: 12, lineHeight: 21, marginTop: 9 },
  tip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
    backgroundColor: "#eff5eb",
    borderRadius: 10,
    padding: 10,
    marginTop: 13,
  },
  tipText: { color: "#64816a", fontSize: 10, flex: 1 },
  promptArea: { marginLeft: 43, marginTop: 22, alignItems: "flex-start" },
  promptLabel: { color: C.muted, fontSize: 11, marginBottom: 9 },
  prompt: {
    backgroundColor: "white",
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 13,
    paddingVertical: 10,
    paddingHorizontal: 13,
    flexDirection: "row",
    alignItems: "center",
    gap: 13,
    marginBottom: 7,
  },
  promptText: { color: C.green, fontSize: 12, fontWeight: "700" },
  run: { marginTop: 18, gap: 15 },
  userBubble: {
    alignSelf: "flex-end",
    maxWidth: "85%",
    backgroundColor: C.green,
    borderRadius: 19,
    borderTopRightRadius: 5,
    padding: 13,
  },
  userText: { color: "white", fontSize: 13, lineHeight: 20 },
  thumbs: { flexDirection: "row", gap: 5, marginBottom: 9 },
  thumb: { width: 72, height: 72, borderRadius: 11, overflow: "hidden" },
  thumbImage: { width: "100%", height: "100%" },
  videoThumb: {
    flex: 1,
    backgroundColor: "#4b6f61",
    alignItems: "center",
    justifyContent: "center",
  },
  videoLabel: { color: "white", fontSize: 9, marginTop: 5 },
  answer: {
    flex: 1,
    backgroundColor: "white",
    borderRadius: 20,
    borderTopLeftRadius: 5,
    padding: 16,
  },
  answerHeader: { flexDirection: "row", justifyContent: "space-between" },
  answerTag: { color: "#75946c", fontWeight: "800", fontSize: 10 },
  answerTitle: { color: C.ink, fontSize: 14, fontWeight: "800", marginTop: 8 },
  answerBody: { color: C.muted, fontSize: 12, lineHeight: 20, marginTop: 8 },
  reportLink: {
    marginTop: 14,
    padding: 12,
    borderRadius: 12,
    backgroundColor: "#edf4e9",
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  reportLinkText: { color: C.green, fontSize: 12, fontWeight: "800" },
  answerActions: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: 20,
    marginTop: 13,
  },
  actionText: { color: C.muted, fontSize: 11 },
  linkText: { color: C.green, fontSize: 11, fontWeight: "700", marginTop: 12 },
  composerWrap: {
    backgroundColor: "white",
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    paddingHorizontal: 16,
    paddingTop: 11,
    paddingBottom: 5,
  },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8 },
  add: {
    width: 40,
    height: 40,
    borderRadius: 13,
    backgroundColor: C.soft,
    alignItems: "center",
    justifyContent: "center",
  },
  input: {
    flex: 1,
    minHeight: 40,
    maxHeight: 100,
    color: C.ink,
    fontSize: 14,
    paddingVertical: 9,
  },
  send: {
    width: 40,
    height: 40,
    borderRadius: 13,
    backgroundColor: C.green,
    alignItems: "center",
    justifyContent: "center",
  },
  footer: { color: "#a8b3aa", fontSize: 9, textAlign: "center", marginTop: 7 },
  notice: { color: "#b16237", fontSize: 11, marginBottom: 8 },
  drafts: { flexDirection: "row", flexWrap: "wrap", gap: 6, paddingBottom: 9 },
  draftItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
    maxWidth: "80%",
    backgroundColor: C.soft,
    borderRadius: 11,
    padding: 8,
  },
  draftText: { color: C.green, fontSize: 11, maxWidth: 130 },
  modalBack: { flex: 1, backgroundColor: "#0a1b1580" },
  sheet: {
    backgroundColor: "white",
    borderTopLeftRadius: 26,
    borderTopRightRadius: 26,
    padding: 22,
    paddingBottom: 35,
  },
  handle: {
    width: 35,
    height: 4,
    backgroundColor: "#d8e1d8",
    borderRadius: 4,
    alignSelf: "center",
    marginBottom: 18,
  },
  sheetTitle: { color: C.ink, fontWeight: "800", fontSize: 18 },
  sheetSub: { color: C.muted, fontSize: 12, marginTop: 6, marginBottom: 15 },
  sheetOption: {
    flexDirection: "row",
    alignItems: "center",
    gap: 13,
    paddingVertical: 10,
  },
  optionIcon: {
    width: 40,
    height: 40,
    borderRadius: 14,
    backgroundColor: C.soft,
    alignItems: "center",
    justifyContent: "center",
  },
  optionText: { color: C.ink, fontSize: 13, fontWeight: "700", flex: 1 },
  sheetHint: { color: C.muted, fontSize: 10, marginTop: 12, lineHeight: 17 },
  sheetClose: {
    backgroundColor: C.soft,
    borderRadius: 13,
    padding: 14,
    marginTop: 15,
    alignItems: "center",
  },
});
