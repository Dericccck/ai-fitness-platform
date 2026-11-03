import { desktopSimulation } from "../network/vault";
import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather, MaterialCommunityIcons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { BottomNav, C, DemoPill, S } from "../components/ui";
import { useDemo } from "../state/DemoContext";
import { useAccount } from "../state/AccountContext";

export default function Home() {
  const router = useRouter();
  const { createConversation, conversations } = useDemo();
  const { configured, loggedIn } = useAccount();
  const start = () =>
    router.push({
      pathname: "/conversation/[id]",
      params: { id: createConversation() },
    });
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      <ScrollView
        contentContainerStyle={S.content}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.header}>
          <View style={styles.logo}>
            <MaterialCommunityIcons name="dumbbell" size={19} color={C.green} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.brand}>MOVE WELL</Text>
            <Text style={styles.brandSub}>你的智能训练伙伴</Text>
          </View>
          <DemoPill />
        </View>
        <View style={styles.greeting}>
          <Text style={S.eyebrow}>A LITTLE BETTER, EVERY DAY</Text>
          <Text style={[S.h1, { marginTop: 10 }]}>
            每一次训练，{"\n"}都更了解自己。
          </Text>
          <Text style={[S.body, { marginTop: 9 }]}>
            记录动作、发现细节，让训练更从容。
          </Text>
        </View>
        <View style={styles.hero}>
          <View style={styles.heroTop}>
            <View style={styles.heroIcon}>
              <Feather name="activity" size={21} color={C.bright} />
            </View>
            <Text style={styles.heroTag}>AI MOVEMENT REVIEW</Text>
          </View>
          <Text style={styles.heroTitle}>让动作，{"\n"}有迹可循。</Text>
          <Text style={styles.heroBody}>
            上传图片或视频，提出你的问题。{"\n"}从可见细节开始，了解动作。
          </Text>
          <Pressable
            accessibilityRole="button"
            onPress={start}
            style={styles.heroButton}
          >
            <Text style={styles.heroButtonText}>开始动作分析</Text>
            <Feather name="arrow-up-right" size={18} color={C.ink} />
          </Pressable>
          <View style={styles.heroCircle1} />
          <View style={styles.heroCircle2} />
          <MaterialCommunityIcons
            name="dumbbell"
            size={106}
            color="#ffffff12"
            style={styles.watermark}
          />
        </View>
        <View style={styles.section}>
          <View>
            <Text style={S.eyebrow}>HOW IT WORKS</Text>
            <Text style={[S.h2, { marginTop: 5 }]}>三步开始</Text>
          </View>
          <View style={styles.lineBadge}>
            <Feather name="zap" size={12} color={C.green} />
            <Text style={styles.lineBadgeText}>简单上手</Text>
          </View>
        </View>
        <View style={styles.steps}>
          {[
            { icon: "image" as const, title: "选择素材", sub: "图片或视频" },
            {
              icon: "message-circle" as const,
              title: "说出问题",
              sub: "关注什么都可以",
            },
            {
              icon: "file-text" as const,
              title: "查看建议",
              sub: "清晰了解细节",
            },
          ].map((item, index) => (
            <View style={styles.step} key={item.title}>
              <View style={styles.stepIcon}>
                <Feather name={item.icon} size={18} color={C.green} />
              </View>
              <Text style={styles.stepNo}>0{index + 1}</Text>
              <Text style={styles.stepTitle}>{item.title}</Text>
              <Text style={styles.stepSub}>{item.sub}</Text>
            </View>
          ))}
        </View>
        <View style={styles.notice}>
          <Feather name="info" size={16} color="#789067" />
          <Text style={styles.noticeText}>
            页面演示不会上传素材；下方本机联调可能调用真实服务。
          </Text>
        </View>
        {configured && (
          <Pressable
            accessibilityRole="button"
            onPress={() => router.push(loggedIn ? "/live" : "/login")}
            style={[styles.recent, { marginTop: 12 }]}
          >
            <View style={styles.recentIcon}>
              <Feather name="activity" size={18} color={C.green} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.recentTitle}>{desktopSimulation ? "进入本机联调" : "进入真实训练对话"}</Text>
              <Text style={styles.recentSub}>
                {desktopSimulation ? "本机测试环境，请勿上传私人素材" : loggedIn ? "上传素材并查看真实报告" : "登录后使用已配置的服务"}
              </Text>
            </View>
            <Feather name="chevron-right" size={18} color={C.muted} />
          </Pressable>
        )}
        <View style={styles.section}>
          <View>
            <Text style={S.eyebrow}>YOUR ACTIVITY</Text>
            <Text style={[S.h2, { marginTop: 5 }]}>最近的对话</Text>
          </View>
          <Pressable onPress={() => router.push("/records")}>
            <Text style={styles.link}>查看全部 →</Text>
          </Pressable>
        </View>
        {conversations.some((c) => c.runs.length) ? (
          <Pressable
            style={styles.recent}
            onPress={() => router.push("/records")}
          >
            <View style={styles.recentIcon}>
              <Feather name="message-square" size={18} color={C.green} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.recentTitle}>继续你的训练对话</Text>
              <Text style={styles.recentSub}>查看本次演示中的记录</Text>
            </View>
            <Feather name="chevron-right" size={18} color={C.muted} />
          </Pressable>
        ) : (
          <View style={styles.recent}>
            <View style={styles.recentIcon}>
              <Feather name="bookmark" size={18} color={C.green} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.recentTitle}>你的训练记录会出现在这里</Text>
              <Text style={styles.recentSub}>从一次好奇的提问开始</Text>
            </View>
          </View>
        )}
      </ScrollView>
      <BottomNav active="home" />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingTop: 12,
  },
  logo: {
    width: 40,
    height: 40,
    borderRadius: 14,
    backgroundColor: "#e2efd8",
    alignItems: "center",
    justifyContent: "center",
  },
  brand: { color: C.ink, fontWeight: "900", fontSize: 12, letterSpacing: 1.7 },
  brandSub: { color: C.muted, fontSize: 10, marginTop: 3 },
  greeting: { paddingTop: 30, paddingBottom: 24 },
  hero: {
    minHeight: 258,
    backgroundColor: C.green,
    borderRadius: 26,
    padding: 23,
    overflow: "hidden",
  },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 10 },
  heroIcon: {
    width: 35,
    height: 35,
    borderRadius: 12,
    backgroundColor: "#ffffff1a",
    alignItems: "center",
    justifyContent: "center",
  },
  heroTag: {
    color: "#cde6d4",
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 1.4,
  },
  heroTitle: {
    color: "white",
    fontSize: 28,
    fontWeight: "800",
    lineHeight: 37,
    marginTop: 15,
  },
  heroBody: { color: "#c5d9cc", fontSize: 12, lineHeight: 21, marginTop: 8 },
  heroButton: {
    backgroundColor: C.bright,
    borderRadius: 14,
    paddingHorizontal: 15,
    height: 44,
    flexDirection: "row",
    alignItems: "center",
    gap: 18,
    alignSelf: "flex-start",
    marginTop: 19,
    zIndex: 2,
  },
  heroButtonText: { color: C.ink, fontSize: 13, fontWeight: "800" },
  heroCircle1: {
    position: "absolute",
    width: 230,
    height: 230,
    borderWidth: 1,
    borderColor: "#ffffff20",
    borderRadius: 120,
    right: -68,
    top: 42,
  },
  heroCircle2: {
    position: "absolute",
    width: 170,
    height: 170,
    borderWidth: 1,
    borderColor: "#ffffff20",
    borderRadius: 90,
    right: -40,
    top: 72,
  },
  watermark: {
    position: "absolute",
    right: 2,
    bottom: 16,
    transform: [{ rotate: "-35deg" }],
  },
  section: {
    marginTop: 28,
    marginBottom: 14,
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-between",
  },
  lineBadge: { flexDirection: "row", alignItems: "center", gap: 5 },
  lineBadgeText: { color: C.green, fontSize: 11, fontWeight: "700" },
  steps: { flexDirection: "row", gap: 8 },
  step: {
    flex: 1,
    backgroundColor: "white",
    paddingHorizontal: 11,
    paddingVertical: 14,
    borderRadius: 18,
  },
  stepIcon: {
    width: 33,
    height: 33,
    backgroundColor: C.soft,
    borderRadius: 11,
    alignItems: "center",
    justifyContent: "center",
  },
  stepNo: {
    position: "absolute",
    right: 11,
    top: 17,
    fontSize: 9,
    color: "#c4cfc3",
    fontWeight: "800",
  },
  stepTitle: { color: C.ink, fontWeight: "800", fontSize: 12, marginTop: 11 },
  stepSub: { color: C.muted, fontSize: 10, marginTop: 4 },
  notice: {
    flexDirection: "row",
    gap: 9,
    alignItems: "flex-start",
    backgroundColor: "#edf3e7",
    borderRadius: 16,
    padding: 13,
    marginTop: 14,
  },
  noticeText: { flex: 1, color: "#64775c", fontSize: 11, lineHeight: 18 },
  link: { color: C.green, fontSize: 11, fontWeight: "800" },
  recent: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 15,
    borderRadius: 19,
    backgroundColor: "white",
  },
  recentIcon: {
    width: 42,
    height: 42,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 14,
    backgroundColor: C.soft,
  },
  recentTitle: { color: C.ink, fontSize: 12, fontWeight: "800" },
  recentSub: { color: C.muted, fontSize: 11, marginTop: 5 },
});
