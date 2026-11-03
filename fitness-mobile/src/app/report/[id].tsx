import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Button, C, S, TopBar } from "../../components/ui";
import { useDemo } from "../../state/DemoContext";

export default function Report() {
  const { id, conversationId } = useLocalSearchParams<{
    id: string;
    conversationId: string;
  }>();
  const { conversations } = useDemo();
  const router = useRouter();
  const run = conversations
    .find((c) => c.id === conversationId)
    ?.runs.find((r) => r.id === id && r.state === "COMPLETED");
  if (!run)
    return (
      <SafeAreaView style={S.screen}>
        <TopBar title="报告详情" />
        <Text style={[S.body, { padding: 22 }]}>报告不存在或已移除。</Text>
      </SafeAreaView>
    );
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      <TopBar title="报告详情" subtitle="演示示例" />
      <ScrollView contentContainerStyle={S.content}>
        <View style={styles.banner}>
          <Feather name="alert-circle" size={16} color="#8a6a33" />
          <Text style={styles.bannerText}>
            这是一份固定的页面示例，与你选择的图片或视频无关，不构成动作判断。
          </Text>
        </View>
        <View style={styles.heading}>
          <Text style={S.eyebrow}>MOVEMENT REVIEW</Text>
          <Text style={[S.h1, { marginTop: 8 }]}>训练动作报告</Text>
          <Text style={[S.body, { marginTop: 7 }]}>问题：{run.question}</Text>
        </View>
        <View style={styles.summary}>
          <View style={styles.summaryIcon}>
            <Feather name="activity" size={19} color={C.bright} />
          </View>
          <Text style={styles.summaryTag}>报告结论 · 示例</Text>
          <Text style={styles.summaryTitle}>真实分析尚未接入</Text>
          <Text style={styles.summaryText}>
            接入后，这里将呈现基于画面的观察、可改进之处与无法判断的内容。
          </Text>
        </View>
        <View style={styles.sectionHeader}>
          <Text style={S.h2}>观察与建议</Text>
          <Text style={styles.sectionTag}>示例结构</Text>
        </View>
        {[
          {
            n: "01",
            title: "画面观察",
            body: "根据可见画面描述具体观察；素材不足时会明确说明。",
          },
          {
            n: "02",
            title: "调整建议",
            body: "结合观察给出容易理解的建议，并说明其依据。",
          },
          {
            n: "03",
            title: "判断限制",
            body: "说明视角、遮挡或静态图片等可能带来的限制。",
          },
        ].map((item) => (
          <View key={item.n} style={styles.item}>
            <Text style={styles.number}>{item.n}</Text>
            <View style={{ flex: 1 }}>
              <Text style={styles.itemTitle}>{item.title}</Text>
              <Text style={styles.itemBody}>{item.body}</Text>
            </View>
          </View>
        ))}
        <View style={styles.knowledge}>
          <View style={styles.knowledgeIcon}>
            <Feather name="book-open" size={18} color={C.green} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.itemTitle}>知识依据</Text>
            <Text style={styles.itemBody}>
              演示报告没有引用资料。真实报告只展示实际检索到的来源。
            </Text>
          </View>
        </View>
        <View style={styles.disclaimer}>
          <Feather name="info" size={16} color="#7f937d" />
          <Text style={styles.disclaimerText}>
            健身建议不能替代专业诊断。如训练中出现疼痛或不适，请停止相关动作并寻求专业帮助。
          </Text>
        </View>
        <Button
          label="回到对话，继续提问"
          icon="message-circle"
          onPress={() => router.back()}
          style={{ marginTop: 22 }}
        />
        <Pressable
          onPress={() => router.replace("/records")}
          style={styles.backRecords}
        >
          <Text style={styles.backText}>查看所有记录</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  banner: {
    marginTop: 10,
    padding: 13,
    borderRadius: 15,
    backgroundColor: "#fbf1dc",
    flexDirection: "row",
    gap: 9,
  },
  bannerText: { flex: 1, color: "#8a6a33", fontSize: 11, lineHeight: 18 },
  heading: { marginTop: 25, marginBottom: 19 },
  summary: { backgroundColor: C.green, borderRadius: 23, padding: 21 },
  summaryIcon: {
    backgroundColor: "#ffffff1c",
    width: 38,
    height: 38,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 13,
  },
  summaryTag: {
    color: C.bright,
    fontSize: 10,
    fontWeight: "800",
    marginTop: 17,
  },
  summaryTitle: {
    color: "white",
    fontSize: 21,
    fontWeight: "800",
    marginTop: 8,
  },
  summaryText: { color: "#c6d9cd", lineHeight: 21, fontSize: 12, marginTop: 9 },
  sectionHeader: {
    marginTop: 27,
    marginBottom: 12,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  sectionTag: { color: C.muted, fontSize: 10 },
  item: {
    flexDirection: "row",
    gap: 14,
    backgroundColor: "white",
    borderRadius: 18,
    padding: 16,
    marginBottom: 8,
  },
  number: { color: "#84aa78", fontSize: 12, fontWeight: "900", marginTop: 2 },
  itemTitle: { color: C.ink, fontSize: 13, fontWeight: "800" },
  itemBody: { color: C.muted, fontSize: 11, lineHeight: 19, marginTop: 6 },
  knowledge: {
    flexDirection: "row",
    gap: 13,
    padding: 16,
    borderRadius: 18,
    backgroundColor: "#eaf2e6",
    marginTop: 8,
  },
  knowledgeIcon: {
    width: 37,
    height: 37,
    borderRadius: 12,
    backgroundColor: "#dcebd5",
    alignItems: "center",
    justifyContent: "center",
  },
  disclaimer: { flexDirection: "row", gap: 9, marginTop: 20 },
  disclaimerText: { flex: 1, color: "#7f937d", fontSize: 11, lineHeight: 18 },
  backRecords: { padding: 16, alignItems: "center" },
  backText: { fontSize: 12, color: C.muted, fontWeight: "700" },
});
