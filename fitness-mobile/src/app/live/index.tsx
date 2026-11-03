import React, { useCallback, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import { Button, C, S, TopBar } from "../../components/ui";
import { confirmAction } from "../../components/confirm";
import { mobileApi, type RemoteConversation, type RemoteLegacyConversation } from "../../network/client";
import { desktopSimulation } from "../../network/vault";
import { useAccount } from "../../state/AccountContext";

export default function LiveConversations() {
  const router = useRouter();
  const { loggedIn, logout } = useAccount();
  const [items, setItems] = useState<RemoteConversation[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showLink, setShowLink] = useState(false);
  const [legacyItems, setLegacyItems] = useState<RemoteLegacyConversation[]>([]);
  const [legacyLoading, setLegacyLoading] = useState(false);
  const load = useCallback(async () => {
    const version = mobileApi.version;
    try {
      const value = await mobileApi.conversations();
      if (version === mobileApi.version) {
        setItems(value);
        setError("");
      }
    } catch (reason) {
      if (version === mobileApi.version)
        setError(reason instanceof Error ? reason.message : "无法加载对话。");
    }
  }, []);
  useFocusEffect(
    useCallback(() => {
      if (loggedIn) void load();
    }, [load, loggedIn]),
  );
  async function start() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const id = await mobileApi.createConversation();
      router.push({ pathname: "/live/conversation/[id]", params: { id } });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法创建会话。");
    } finally {
      setBusy(false);
    }
  }
  async function toggleLegacy() {
    if (showLink) { setShowLink(false); return; }
    setShowLink(true);
    setLegacyLoading(true);
    setError("");
    try { setLegacyItems(await mobileApi.legacyConversations()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法加载历史对话。"); }
    finally { setLegacyLoading(false); }
  }
  async function linkLegacy(threadId: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const id = await mobileApi.linkLegacyConversation(threadId);
      setShowLink(false);
      router.push({ pathname: "/live/conversation/[id]", params: { id } });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法关联历史对话。");
    } finally { setBusy(false); }
  }
  async function unlinkLegacy(id: string) {
    try {
      await mobileApi.unlinkLegacyConversation(id);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "解除关联未完成。");
    }
  }
  async function remove(id: string) {
    try {
      await mobileApi.deleteConversation(id);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除未完成。");
    }
  }
  async function signOut() {
    try {
      await logout();
      router.replace("/");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "退出状态未确认。");
    }
  }
  if (!loggedIn)
    return (
      <SafeAreaView style={S.screen}>
        <TopBar title="训练对话" />
        <View style={styles.content}>
          <Text style={S.body}>请登录后查看真实训练对话。</Text>
          <Button
            label="前往登录"
            onPress={() => router.replace("/login")}
            style={{ marginTop: 20 }}
          />
        </View>
      </SafeAreaView>
    );
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      <TopBar title="训练对话" subtitle={desktopSimulation ? "本机联调 · 测试身份" : "真实服务"} />
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={S.eyebrow}>YOUR TRAINING JOURNEY</Text>
        <Text style={[S.h1, { marginTop: 8 }]}>
          每一次训练，{"\n"}都有迹可循。
        </Text>
        <Text style={[S.body, { marginTop: 8 }]}>
          选择会话继续，或开始新的动作分析。
        </Text>
        <Button
          label={busy ? "正在创建…" : "新建对话"}
          disabled={busy}
          icon="plus"
          onPress={start}
          style={{ marginTop: 22 }}
        />
        <Pressable accessibilityRole="button" onPress={() => { void toggleLegacy(); }} style={{ marginTop: 16 }}>
          <Text style={styles.link}>{showLink ? "收起历史对话" : "关联已有文本对话"}</Text>
        </Pressable>
        {showLink ? (
          <View style={{ marginTop: 12, gap: 10 }}>
            <Text style={S.body}>选择当前账户已完成的文本对话，关联后可参考最近的文字记录。</Text>
            {legacyLoading ? <Text style={S.body}>正在查找历史对话…</Text> : legacyItems.length ? legacyItems.map((item) => (
              <Pressable key={item.thread_id} accessibilityRole="button" disabled={busy}
                onPress={() => { void linkLegacy(item.thread_id); }} style={styles.legacyItem}>
                <Text style={styles.title}>历史文本对话</Text>
                <Text style={styles.sub}>{new Date(item.updated_at).toLocaleString("zh-CN")}　·　关联</Text>
              </Pressable>
            )) : <Text style={S.body}>没有可关联的历史对话。</Text>}
          </View>
        ) : null}
        {error ? (
          <View style={styles.error}>
            <Text style={styles.errorText}>{error}</Text>
            <Pressable onPress={load}>
              <Text style={styles.link}>重试</Text>
            </Pressable>
          </View>
        ) : null}
        <View style={styles.section}>
          <Text style={S.h2}>最近对话</Text>
          <Pressable onPress={load}>
            <Feather name="refresh-cw" size={17} color={C.green} />
          </Pressable>
        </View>
        {items.length ? (
          items.map((item) => (
            <View key={item.conversation_id} style={styles.item}>
              <Pressable
                style={styles.itemContent}
                onPress={() =>
                  router.push({
                    pathname: "/live/conversation/[id]",
                    params: { id: item.conversation_id },
                  })
                }
              >
                <View style={styles.icon}>
                  <Feather name="message-square" size={18} color={C.green} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.title}>{item.legacy_linked ? "训练对话 · 已关联文本" : "训练对话"}</Text>
                  <Text style={styles.sub}>
                    {new Date(item.updated_at).toLocaleString("zh-CN")}
                  </Text>
                </View>
                <Feather name="chevron-right" size={17} color={C.muted} />
              </Pressable>
              {item.legacy_linked ? (
                <Pressable accessibilityRole="button" accessibilityLabel="解除文本对话关联" style={styles.delete}
                  onPress={() => confirmAction("解除关联", "原文本对话和本次分析都会保留，之后不会再读取原文本历史。", () => { void unlinkLegacy(item.conversation_id); })}>
                  <Feather name="link-2" size={16} color={C.green} />
                </Pressable>
              ) : null}
              <Pressable
                accessibilityLabel="删除对话"
                accessibilityRole="button"
                style={styles.delete}
                onPress={() =>
                  confirmAction(
                    "删除训练对话",
                    item.legacy_linked ? "本次分析和关联素材将无法查看，素材会异步清理；原文本对话仍会保留。" : "对话和关联素材将无法查看，素材会异步清理。",
                    () => {
                      void remove(item.conversation_id);
                    },
                  )
                }
              >
                <Feather name="trash-2" size={16} color="#9eaaa1" />
              </Pressable>
            </View>
          ))
        ) : (
          <View style={styles.empty}>
            <Feather name="bookmark" size={22} color={C.green} />
            <Text style={styles.title}>还没有训练对话</Text>
            <Text style={S.body}>从一个想了解的动作开始。</Text>
          </View>
        )}
        <Pressable onPress={signOut} style={styles.signout}>
          <Text style={styles.link}>退出当前账户</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  content: { padding: 22, paddingBottom: 40 },
  legacyItem: { backgroundColor: "white", borderRadius: 12, padding: 12, borderWidth: 1, borderColor: C.line },
  section: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 30,
    marginBottom: 14,
  },
  error: {
    padding: 13,
    borderRadius: 14,
    backgroundColor: "#fbf1dc",
    marginTop: 14,
    gap: 8,
  },
  errorText: { color: "#8a6a33", fontSize: 12, lineHeight: 19 },
  link: { color: C.green, fontSize: 12, fontWeight: "800" },
  item: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "white",
    borderRadius: 18,
    marginBottom: 9,
  },
  itemContent: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 14,
  },
  icon: {
    width: 43,
    height: 43,
    backgroundColor: C.soft,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  title: { color: C.ink, fontSize: 13, fontWeight: "800" },
  sub: { color: C.muted, fontSize: 10, marginTop: 5 },
  delete: { padding: 15, borderLeftWidth: 1, borderLeftColor: C.line },
  empty: {
    alignItems: "center",
    gap: 12,
    backgroundColor: "white",
    padding: 30,
    borderRadius: 20,
  },
  signout: { alignSelf: "center", marginTop: 30, padding: 10 },
});
