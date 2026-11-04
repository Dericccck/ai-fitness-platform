import React, { useCallback, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import { BottomNav, Button, C, S, TopBar } from "./ui";
import { confirmAction } from "./confirm";
import { mobileApi, type RecoverableConversation, type RemoteConversation, type RemoteLegacyConversation } from "../network/client";
import { desktopSimulation } from "../network/vault";
import { useAccount } from "../state/AccountContext";

export default function LiveConversations({ embedded = false }: { embedded?: boolean }) {
  const router = useRouter();
  const { loggedIn, logout, ready } = useAccount();
  const [items, setItems] = useState<RemoteConversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showLink, setShowLink] = useState(false);
  const [legacyItems, setLegacyItems] = useState<RemoteLegacyConversation[]>([]);
  const [legacyLoading, setLegacyLoading] = useState(false);
  const [showRecovery, setShowRecovery] = useState(false);
  const [recoveryEnabled, setRecoveryEnabled] = useState(false);
  const [recoveryItems, setRecoveryItems] = useState<RecoverableConversation[]>([]);
  const [recoveryCursor, setRecoveryCursor] = useState<string | null>(null);
  const [recoveryLoading, setRecoveryLoading] = useState(false);
  const load = useCallback(async () => {
    const version = mobileApi.version;
    try {
      const value = await mobileApi.conversationsPage();
      if (version === mobileApi.version) {
        setItems(value.conversations);
        setCursor(value.next_cursor);
        setError("");
      }
    } catch (reason) {
      if (version === mobileApi.version)
        setError(reason instanceof Error ? reason.message : "无法加载对话。");
    }
  }, []);
  async function loadMore() {
    if (!cursor || loadingMore) return;
    const version = mobileApi.version;
    setLoadingMore(true);
    try {
      const page = await mobileApi.conversationsPage(cursor);
      if (version !== mobileApi.version) return;
      setItems((old) => [...old, ...page.conversations.filter((item) => !old.some((v) => v.conversation_id === item.conversation_id))]);
      setCursor(page.next_cursor);
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "无法加载更多对话。");
    } finally { setLoadingMore(false); }
  }
  useFocusEffect(
    useCallback(() => {
      setItems([]);
      setCursor(null);
      setShowLink(false);
      setLegacyItems([]);
      setShowRecovery(false);
      setRecoveryItems([]);
      setRecoveryCursor(null);
      setRecoveryEnabled(false);
      if (loggedIn) {
        void load();
        const version = mobileApi.version;
        void mobileApi.ownerRecoveryEnabled().then((enabled) => {
          if (version === mobileApi.version) setRecoveryEnabled(enabled);
        }).catch(() => { /* The rest of the conversation list remains available. */ });
      }
    }, [load, loggedIn]),
  );
  async function start() {
    if (busy) return;
    const version = mobileApi.version;
    setBusy(true);
    setError("");
    try {
      const id = await mobileApi.createConversation();
      if (version !== mobileApi.version) return;
      router.push({ pathname: "/live/conversation/[id]", params: { id } });
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "无法创建会话。");
    } finally {
      setBusy(false);
    }
  }
  async function toggleLegacy() {
    if (showLink) { setShowLink(false); return; }
    setShowLink(true);
    setLegacyLoading(true);
    setError("");
    const version = mobileApi.version;
    try { const result = await mobileApi.legacyConversations(); if (version === mobileApi.version) setLegacyItems(result); }
    catch (reason) { if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "无法加载历史对话。"); }
    finally { setLegacyLoading(false); }
  }
  async function toggleRecovery() {
    if (showRecovery) { setShowRecovery(false); return; }
    const version = mobileApi.version;
    setShowRecovery(true);
    setRecoveryLoading(true);
    setRecoveryItems([]);
    setRecoveryCursor(null);
    setError("");
    try {
      const page = await mobileApi.recoverableConversations();
      if (version === mobileApi.version) { setRecoveryItems(page.conversations); setRecoveryCursor(page.next_cursor); }
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "无法查找可恢复对话。");
    } finally { setRecoveryLoading(false); }
  }
  async function moreRecoverable() {
    if (!recoveryCursor || recoveryLoading) return;
    const version = mobileApi.version;
    setRecoveryLoading(true);
    try {
      const page = await mobileApi.recoverableConversations(recoveryCursor);
      if (version === mobileApi.version) {
        setRecoveryItems((old) => [...old, ...page.conversations.filter((item) => !old.some((v) => v.conversation_id === item.conversation_id))]);
        setRecoveryCursor(page.next_cursor);
      }
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "无法加载更多对话。");
    } finally { setRecoveryLoading(false); }
  }
  async function recover(id: string) {
    if (busy) return;
    const version = mobileApi.version;
    setBusy(true);
    setError("");
    try {
      await mobileApi.recoverConversation(id);
      if (version !== mobileApi.version) return;
      setRecoveryItems((old) => old.filter((item) => item.conversation_id !== id));
      await load();
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "恢复未完成。");
    } finally { setBusy(false); }
  }
  async function linkLegacy(threadId: string) {
    if (busy) return;
    const version = mobileApi.version;
    setBusy(true);
    setError("");
    try {
      const id = await mobileApi.linkLegacyConversation(threadId);
      if (version !== mobileApi.version) return;
      setShowLink(false);
      router.push({ pathname: "/live/conversation/[id]", params: { id } });
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "无法关联历史对话。");
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
  if (!ready)
    return <SafeAreaView style={S.screen}><View style={styles.content}><Text style={S.body}>正在恢复账户…</Text></View>{embedded ? <BottomNav active="records" /> : null}</SafeAreaView>;
  if (!loggedIn)
    return (
      <SafeAreaView style={S.screen}>
        {!embedded ? <TopBar title="训练对话" /> : null}
        <View style={styles.content}>
          <Text style={S.body}>请登录后查看真实训练对话。</Text>
          <Button
            label="前往登录"
            onPress={() => router.replace("/login")}
            style={{ marginTop: 20 }}
          />
        </View>
      {embedded ? <BottomNav active="records" /> : null}
      </SafeAreaView>
    );
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      {!embedded ? <TopBar title="训练对话" subtitle={desktopSimulation ? "本机联调 · 测试身份" : "真实服务"} /> : null}
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
        {recoveryEnabled ? <Pressable accessibilityRole="button" onPress={() => { void toggleRecovery(); }} style={{ marginTop: 16 }}>
          <Text style={styles.link}>{showRecovery ? "收起可恢复对话" : "找回身份变化前的对话"}</Text>
        </Pressable> : null}
        {recoveryEnabled && showRecovery ? (
          <View style={{ marginTop: 12, gap: 10 }}>
            <Text style={S.body}>账号权限变化后，可以找回本人在当前机构的训练对话。仍在处理的任务或上传会暂时无法恢复。</Text>
            {recoveryItems.map((item) => (
              <Pressable key={item.conversation_id} accessibilityRole="button" disabled={busy}
                onPress={() => confirmAction("恢复训练对话", "恢复后可使用当前身份继续查看；其他设备上的旧身份可能需要重新登录。", () => { void recover(item.conversation_id); })} style={styles.legacyItem}>
                <Text style={styles.title}>训练对话</Text>
                <Text style={styles.sub}>{new Date(item.updated_at).toLocaleString("zh-CN")}　·　恢复</Text>
              </Pressable>
            ))}
            {recoveryLoading ? <Text style={S.body}>正在查找…</Text> : !recoveryItems.length ? <Text style={S.body}>没有找到可恢复的对话。</Text> : null}
            {recoveryCursor ? <Pressable accessibilityRole="button" disabled={recoveryLoading} onPress={() => { void moreRecoverable(); }}><Text style={styles.link}>加载更多</Text></Pressable> : null}
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
        {cursor ? <Pressable accessibilityRole="button" onPress={() => { void loadMore(); }} disabled={loadingMore}>
          <Text style={styles.link}>{loadingMore ? "正在加载…" : "加载更多对话"}</Text>
        </Pressable> : null}
        <Pressable onPress={signOut} style={styles.signout}>
          <Text style={styles.link}>退出当前账户</Text>
        </Pressable>
      </ScrollView>
      {embedded ? <BottomNav active="records" /> : null}
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
