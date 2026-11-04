import React, { useCallback, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { Feather } from "@expo/vector-icons";
import { Button, C, S, TopBar } from "../../../components/ui";
import { mobileApi, type RemoteReport } from "../../../network/client";

const labels: Record<string, string> = {
  NEEDS_ADJUSTMENT: "有值得关注的细节",
  NO_OBVIOUS_ISSUE_OBSERVED: "可见部分未发现明显问题",
  INSUFFICIENT_EVIDENCE: "当前画面信息不足",
  NEEDS_CLARIFICATION: "需要进一步了解",
};
export default function LiveReport() {
  const { id, conversationId } = useLocalSearchParams<{
    id: string;
    conversationId: string;
  }>();
  const router = useRouter();
  const [report, setReport] = useState<RemoteReport | null>(null);
  const [error, setError] = useState("");
  useFocusEffect(
    useCallback(() => {
      let alive = true;
      const version = mobileApi.version;
      setReport(null);
      setError("");
      (async () => {
        try {
          // 历史投影的保留期可能长于运行状态，因此优先从会话读取。
          const messages = await mobileApi.messages(conversationId, id);
          const item = messages.find(
            (message) =>
              message.run_id === id &&
              message.role === "assistant" &&
              typeof message.content === "object" &&
              message.content,
          );
          if (!item || !item.content || typeof item.content === "string")
            throw new Error("报告不存在或已被删除。");
          if (alive && version === mobileApi.version) {
            setReport(item.content);
            setError("");
          }
        } catch (reason) {
          if (alive && version === mobileApi.version) {
            setReport(null);
            setError(
              reason instanceof Error ? reason.message : "无法读取报告。",
            );
          }
        }
      })();
      return () => {
        alive = false;
      };
    }, [id, conversationId]),
  );
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      <TopBar title={report && "key_points" in report ? "知识解读报告" : "训练动作报告"} />
      <ScrollView contentContainerStyle={styles.content}>
        {error ? (
          <Text style={styles.error}>{error}</Text>
        ) : !report ? (
          <Text style={S.body}>正在加载报告…</Text>
        ) : (
          <>
            <Text style={S.eyebrow}>MOVEMENT REVIEW</Text>
            <Text style={[S.h1, { marginTop: 8 }]}>
              {"key_points" in report ? "健身知识解读" : report.exercise.name || "训练动作"}
            </Text>
            <View style={styles.summary}>
              <View style={styles.icon}>
                <Feather name="activity" size={19} color={C.bright} />
              </View>
              <Text style={styles.tag}>
                {"key_points" in report ? "素材解读" : labels[report.assessment] || "分析结果"}
              </Text>
              <Text style={styles.summaryText}>{report.summary}</Text>
            </View>
            <Text style={styles.section}>画面观察</Text>
            {report.observations.length ? (
              report.observations.map((item, index) => (
                <View style={styles.card} key={item.id}>
                  <Text style={styles.number}>0{index + 1}</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.body}>{item.description}</Text>
                    {item.time_range && (
                      <Text style={styles.small}>
                        {item.time_range.start_seconds.toFixed(1)}–
                        {item.time_range.end_seconds.toFixed(1)} 秒
                      </Text>
                    )}
                  </View>
                </View>
              ))
            ) : (
              <Text style={S.body}>本次没有足够的画面观察。</Text>
            )}
            {"key_points" in report ? <>
              <Text style={styles.section}>知识要点</Text>
              {report.key_points.length ? report.key_points.map((item, index) => (
                <View style={styles.card} key={`${index}-${item.text}`}><View style={{ flex: 1 }}>
                  <Text style={styles.body}>{item.text}</Text>
                  <Text style={styles.small}>{item.basis_type === "MEDIA" ? "素材可见内容" : "一般训练说明"}</Text>
                </View></View>
              )) : <Text style={S.body}>本次没有提取到可确认的知识要点。</Text>}
            </> : <>
            <Text style={styles.section}>调整建议</Text>
            {report.adjustments.length ? (
              report.adjustments.map((item, index) => (
                <View style={styles.card} key={`${index}-${item.advice}`}>
                  <Feather name="check-circle" size={17} color="#78a270" />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.body}>{item.advice}</Text>
                    <Text style={styles.small}>
                      {item.basis_type === "GENERAL_GUIDANCE"
                        ? "一般训练说明"
                        : "基于画面观察"}
                    </Text>
                  </View>
                </View>
              ))
            ) : (
              <Text style={S.body}>本次没有生成调整建议。</Text>
            )}
            </>}
            {report.limitations.length > 0 && (
              <>
                <Text style={styles.section}>判断限制</Text>
                <View style={styles.card}>
                  <View style={{ flex: 1 }}>
                    {report.limitations.map((item, index) => (
                      <Text key={`${index}-${item}`} style={styles.body}>
                        • {item}
                      </Text>
                    ))}
                  </View>
                </View>
              </>
            )}
            {report.follow_up_question && (
              <View style={styles.note}>
                <Feather name="message-circle" size={17} color={C.green} />
                <Text style={styles.noteText}>{report.follow_up_question}</Text>
              </View>
            )}
            <Text style={styles.section}>知识依据</Text>
            <View style={styles.card}>
              <Feather name="book-open" size={17} color={C.green} />
              <View style={{ flex: 1 }}>
                {report.knowledge_citations.length ? (
                  report.knowledge_citations.map((item) => (
                    <Text key={item.citation_id} style={styles.body}>
                      {item.title}
                      {item.source_page ? ` · 第 ${item.source_page} 页` : ""}
                    </Text>
                  ))
                ) : (
                  <Text style={styles.body}>
                    {report.knowledge_status === "UNAVAILABLE"
                      ? "本次知识检索暂不可用。"
                      : "本次没有引用知识资料。"}
                  </Text>
                )}
              </View>
            </View>
            <View style={styles.disclaimer}>
              <Feather name="info" size={16} color="#7f937d" />
              <Text style={styles.disclaimerText}>
                本报告仅供训练参考，不能保证动作安全或替代专业诊断。如有疼痛或不适，请停止相关动作并寻求专业帮助。
              </Text>
            </View>
            <Button
              label="回到对话，继续提问"
              onPress={() => router.back()}
              icon="message-circle"
              style={{ marginTop: 22 }}
            />
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  content: { padding: 22, paddingBottom: 36 },
  summary: {
    backgroundColor: C.green,
    borderRadius: 23,
    padding: 20,
    marginTop: 20,
  },
  icon: {
    width: 38,
    height: 38,
    borderRadius: 13,
    backgroundColor: "#ffffff1c",
    alignItems: "center",
    justifyContent: "center",
  },
  tag: { color: C.bright, fontSize: 13, fontWeight: "800", marginTop: 16 },
  summaryText: { color: "white", fontSize: 14, lineHeight: 23, marginTop: 8 },
  section: {
    color: C.ink,
    fontSize: 17,
    fontWeight: "800",
    marginTop: 24,
    marginBottom: 11,
  },
  card: {
    backgroundColor: "white",
    borderRadius: 17,
    padding: 16,
    marginBottom: 8,
    flexDirection: "row",
    gap: 12,
  },
  number: { color: "#84aa78", fontSize: 12, fontWeight: "900" },
  body: { color: C.ink, fontSize: 13, lineHeight: 21 },
  small: { color: C.muted, fontSize: 10, marginTop: 7 },
  note: {
    backgroundColor: C.soft,
    borderRadius: 17,
    padding: 16,
    marginTop: 14,
    flexDirection: "row",
    gap: 10,
  },
  noteText: { color: C.green, flex: 1, fontSize: 12, lineHeight: 20 },
  disclaimer: { flexDirection: "row", gap: 9, marginTop: 23 },
  disclaimerText: { color: "#7f937d", flex: 1, fontSize: 11, lineHeight: 18 },
  error: { color: "#a34a34", fontSize: 13 },
});
