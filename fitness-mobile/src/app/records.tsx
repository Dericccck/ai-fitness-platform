import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { BottomNav, C, DemoPill, S } from "../components/ui";
import { confirmAction } from "../components/confirm";
import { titleFor } from "../domain/types";
import { useDemo } from "../state/DemoContext";

export default function Records() {
  const router = useRouter();
  const { conversations, createConversation, removeConversation } = useDemo();
  const records = conversations.filter((c) => c.runs.length > 0);
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      <ScrollView contentContainerStyle={S.content}>
        <View style={styles.header}>
          <View>
            <Text style={S.eyebrow}>YOUR JOURNEY</Text>
            <Text style={[S.h1, { marginTop: 7 }]}>训练对话</Text>
          </View>
          <DemoPill />
        </View>
        <Text style={[S.body, { marginTop: 10 }]}>
          每一个问题，都是更了解自己的开始。
        </Text>
        <View style={styles.info}>
          <Feather name="info" color="#789067" size={15} />
          <Text style={styles.infoText}>
            这些是本次运行中的演示记录，App 进程结束后不会保存。
          </Text>
        </View>
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>最近对话</Text>
          <Text style={styles.count}>{records.length} 条</Text>
        </View>
        {records.length ? (
          records.map((item) => (
            <View style={styles.card} key={item.id}>
              <Pressable
                accessibilityRole="button"
                style={styles.cardContent}
                onPress={() =>
                  router.push({
                    pathname: "/conversation/[id]",
                    params: { id: item.id },
                  })
                }
              >
                <View style={styles.icon}>
                  <Feather name="message-square" size={20} color={C.green} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text numberOfLines={1} style={styles.title}>
                    {titleFor(item)}
                  </Text>
                  <Text style={styles.sub}>
                    {new Date(item.createdAt).toLocaleDateString("zh-CN")} ·{" "}
                    {item.runs.length} 次演示
                  </Text>
                </View>
                <Feather name="chevron-right" size={17} color={C.muted} />
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="删除对话"
                onPress={() =>
                  confirmAction(
                    "删除演示对话",
                    "这会移除当前演示中的对话和素材引用。",
                    () => removeConversation(item.id),
                  )
                }
                style={styles.delete}
              >
                <Feather name="trash-2" size={15} color="#9eaaa1" />
              </Pressable>
            </View>
          ))
        ) : (
          <View style={styles.empty}>
            <View style={styles.emptyIcon}>
              <Feather name="bookmark" size={25} color={C.green} />
            </View>
            <Text style={styles.emptyTitle}>还没有训练对话</Text>
            <Text style={styles.emptySub}>
              挑选一张图片或一段视频，{"\n"}开启你的第一次体验。
            </Text>
            <Pressable
              onPress={() =>
                router.push({
                  pathname: "/conversation/[id]",
                  params: { id: createConversation() },
                })
              }
              style={styles.emptyButton}
            >
              <Text style={styles.emptyButtonText}>开始体验</Text>
              <Feather name="arrow-right" size={15} color={C.green} />
            </Pressable>
          </View>
        )}
      </ScrollView>
      <BottomNav active="records" />
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  header: {
    marginTop: 23,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  info: {
    flexDirection: "row",
    gap: 8,
    backgroundColor: "#edf3e7",
    padding: 14,
    borderRadius: 15,
    marginTop: 24,
  },
  infoText: { color: "#64775c", fontSize: 11, lineHeight: 18, flex: 1 },
  section: {
    marginTop: 28,
    marginBottom: 13,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  sectionTitle: { color: C.ink, fontSize: 15, fontWeight: "800" },
  count: { color: C.muted, fontSize: 11 },
  card: {
    borderRadius: 18,
    backgroundColor: "white",
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 9,
  },
  cardContent: {
    flex: 1,
    padding: 14,
    flexDirection: "row",
    gap: 12,
    alignItems: "center",
  },
  icon: {
    width: 44,
    height: 44,
    backgroundColor: C.soft,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  title: { color: C.ink, fontSize: 13, fontWeight: "800" },
  sub: { color: C.muted, fontSize: 10, marginTop: 6 },
  delete: { padding: 15, borderLeftWidth: 1, borderLeftColor: C.line },
  empty: {
    backgroundColor: "white",
    borderRadius: 23,
    padding: 32,
    alignItems: "center",
  },
  emptyIcon: {
    width: 60,
    height: 60,
    borderRadius: 21,
    backgroundColor: C.soft,
    alignItems: "center",
    justifyContent: "center",
  },
  emptyTitle: { color: C.ink, fontWeight: "800", fontSize: 15, marginTop: 18 },
  emptySub: {
    color: C.muted,
    fontSize: 12,
    lineHeight: 20,
    marginTop: 8,
    textAlign: "center",
  },
  emptyButton: {
    flexDirection: "row",
    gap: 8,
    alignItems: "center",
    marginTop: 18,
  },
  emptyButtonText: { color: C.green, fontSize: 12, fontWeight: "800" },
});
