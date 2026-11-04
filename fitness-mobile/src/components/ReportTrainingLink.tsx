import React, { useCallback, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { C, S } from "./ui";
import { mobileApi, type TrainingLinkedReport, type TrainingPlanSelection, type TrainingReportLink } from "../network/client";

/** A report association is personal; training completion is never written here. */
export function ReportTrainingLink({ runId }: { runId: string }) {
  const router = useRouter();
  const [enabled, setEnabled] = useState(false);
  const [existing, setExisting] = useState<TrainingReportLink | null>(null);
  const [planId, setPlanId] = useState("");
  const [plan, setPlan] = useState<TrainingPlanSelection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [canClear, setCanClear] = useState(false);
  const [executions, setExecutions] = useState<{ status: string; execution_date: string }[] | null>(null);
  const [reports, setReports] = useState<TrainingLinkedReport[]>([]);
  const [reportsCursor, setReportsCursor] = useState<string | null>(null);
  useFocusEffect(useCallback(() => {
    let alive = true;
    const version = mobileApi.version;
    setEnabled(false);
    setExisting(null);
    setPlan(null);
    setPlanId("");
    setError("");
    setCanClear(false);
    setExecutions(null);
    setReports([]); setReportsCursor(null);
    void mobileApi.trainingLinksEnabled().then(async (value) => {
      if (!alive || version !== mobileApi.version) return;
      setEnabled(value);
      if (value) {
        const link = await mobileApi.trainingLink(runId);
        if (alive && version === mobileApi.version) setExisting(link);
      }
    }).catch((reason) => {
      if (alive && version === mobileApi.version) { setError(reason instanceof Error ? reason.message : "无法读取关联信息。"); setCanClear(true); }
    });
    return () => { alive = false; };
  }, [runId]));
  async function loadPlan() {
    if (busy) return;
    const version = mobileApi.version;
    setBusy(true); setError(""); setPlan(null);
    try {
      const [value, page] = await Promise.all([mobileApi.trainingPlan(planId.trim()), mobileApi.trainingPlanReports(planId.trim())]);
      if (version === mobileApi.version) { setPlan(value); setReports(page.reports); setReportsCursor(page.next_cursor); }
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "无法读取计划。");
    } finally { setBusy(false); }
  }
  async function moreReports() {
    if (!plan || !reportsCursor || busy) return;
    const version = mobileApi.version;
    setBusy(true); setError("");
    try {
      const page = await mobileApi.trainingPlanReports(plan.plan_id, reportsCursor);
      if (version === mobileApi.version) { setReports((old) => [...old, ...page.reports.filter((r) => !old.some((v) => v.run_id === r.run_id))]); setReportsCursor(page.next_cursor); }
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "无法读取更多报告。");
    } finally { setBusy(false); }
  }
  async function choose(dayId: string, itemId: string) {
    if (!plan || busy) return;
    const version = mobileApi.version;
    setBusy(true); setError("");
    try {
      await mobileApi.saveTrainingLink(runId, plan.plan_id, dayId, itemId);
      const link = await mobileApi.trainingLink(runId);
      if (version === mobileApi.version) { setExisting(link); setPlan(null); setExecutions(null); }
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "关联未完成。");
    } finally { setBusy(false); }
  }
  async function remove() {
    if (busy) return;
    const version = mobileApi.version;
    setBusy(true); setError("");
    try {
      await mobileApi.removeTrainingLink(runId);
      if (version === mobileApi.version) { setExisting({ linked: false }); setCanClear(false); setExecutions(null); }
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "解除关联未完成。");
    } finally { setBusy(false); }
  }
  async function loadExecutions() {
    if (busy) return;
    const version = mobileApi.version;
    setBusy(true); setError("");
    try {
      const items = await mobileApi.trainingExecutions(runId);
      if (version === mobileApi.version) setExecutions(items);
    } catch (reason) {
      if (version === mobileApi.version) setError(reason instanceof Error ? reason.message : "无法读取训练记录。");
    } finally { setBusy(false); }
  }
  if (!enabled) return null;
  return <View style={styles.container}>
    <Text style={styles.title}>关联训练计划</Text>
    <Text style={S.body}>仅关联当前报告，不会修改计划或记录训练完成。</Text>
    {existing?.linked ? <View style={styles.card}>
      <Text style={styles.text}>{existing.plan_title} · {existing.day_title} · {existing.exercise_name}</Text>
      <Pressable accessibilityRole="button" disabled={busy} onPress={() => { void loadExecutions(); }}><Text style={styles.link}>查看该训练日记录</Text></Pressable>
      {executions ? executions.length ? executions.map((item, index) => <Text key={`${item.execution_date}-${index}`} style={styles.text}>{item.execution_date} · {item.status === "COMPLETED" ? "已完成" : "已跳过"}</Text>) : <Text style={S.body}>该训练日尚无完成或跳过记录。</Text> : null}
      <Pressable accessibilityRole="button" disabled={busy} onPress={() => { void remove(); }}><Text style={styles.link}>解除关联</Text></Pressable>
    </View> : null}
    {canClear && !existing?.linked ? <Pressable accessibilityRole="button" disabled={busy} onPress={() => { void remove(); }}><Text style={styles.link}>清除不可读取的关联</Text></Pressable> : null}
    <TextInput value={planId} onChangeText={setPlanId} placeholder="输入训练计划编号" autoCapitalize="none" maxLength={128} style={styles.input} />
    <Pressable accessibilityRole="button" disabled={busy || !planId.trim()} onPress={() => { void loadPlan(); }}><Text style={styles.link}>{busy ? "处理中…" : "查找我的计划"}</Text></Pressable>
    {plan ? <View style={styles.card}><Text style={styles.text}>{plan.title}</Text>{plan.days.map((day) => <View key={day.day_id}>
      <Text style={styles.text}>{day.title}</Text>{day.items.map((item) => <Pressable key={item.item_id} accessibilityRole="button" disabled={busy} onPress={() => { void choose(day.day_id, item.item_id); }}><Text style={styles.link}>{item.exercise_name} · 关联</Text></Pressable>)}
    </View>)}
      <Text style={styles.text}>该计划关联的报告</Text>
      {reports.length ? reports.map((item) => <Pressable key={item.run_id} accessibilityRole="button" onPress={() => router.push({ pathname: "/live/report/[id]", params: { id: item.run_id, conversationId: item.conversation_id } })}><Text style={styles.link}>{new Date(item.created_at).toLocaleString("zh-CN")} · 查看报告</Text></Pressable>) : <Text style={S.body}>暂无关联报告。</Text>}
      {reportsCursor ? <Pressable accessibilityRole="button" disabled={busy} onPress={() => { void moreReports(); }}><Text style={styles.link}>加载更多报告</Text></Pressable> : null}
    </View> : null}
    {error ? <Text accessibilityLiveRegion="polite" style={styles.error}>{error}</Text> : null}
  </View>;
}
const styles = StyleSheet.create({
  container: { marginTop: 20, gap: 10 },
  title: { color: C.ink, fontSize: 17, fontWeight: "800" },
  card: { backgroundColor: "white", borderRadius: 14, padding: 14, gap: 10 },
  text: { color: C.ink, fontSize: 13, lineHeight: 21 },
  input: { backgroundColor: "white", color: C.ink, borderRadius: 12, padding: 12 },
  link: { color: C.green, fontWeight: "800", fontSize: 12, paddingVertical: 6 },
  error: { color: "#a34a34", fontSize: 12 },
});
