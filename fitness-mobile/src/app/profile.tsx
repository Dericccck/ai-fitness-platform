import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather, MaterialCommunityIcons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { BottomNav, C, DemoPill, S } from "../components/ui";
import { confirmAction } from "../components/confirm";
import { useDemo } from "../state/DemoContext";
import { useAccount } from "../state/AccountContext";

export default function Profile() {
  const { clear } = useDemo();
  const { configured, loggedIn } = useAccount();
  const router = useRouter();
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      <ScrollView contentContainerStyle={S.content}>
        <View style={styles.header}>
          <View>
            <Text style={S.eyebrow}>YOUR SPACE</Text>
            <Text style={[S.h1, { marginTop: 7 }]}>我的空间</Text>
          </View>
          <DemoPill />
        </View>
        <View style={styles.userCard}>
          <View style={styles.avatar}>
            <MaterialCommunityIcons
              name="dumbbell"
              size={25}
              color={C.bright}
            />
          </View>
          <View>
            <Text style={styles.name}>训练体验者</Text>
            <Text style={styles.sub}>在这里，按自己的节奏进步</Text>
          </View>
        </View>
        <Text style={styles.group}>关于这次体验</Text>
        <View style={styles.list}>
          <Pressable
            accessibilityRole="button"
            onPress={() => router.push(loggedIn ? "/live" : "/login")}
            style={styles.row}
          >
            <View style={styles.icon}>
              <Feather name="smartphone" size={18} color={C.green} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>账户与登录</Text>
              <Text style={styles.rowSub}>
                {loggedIn
                  ? "已登录，点击进入训练对话"
                  : configured
                    ? "可使用健身平台账户登录"
                    : "尚未配置真实服务"}
              </Text>
            </View>
            <Feather name="chevron-right" size={17} color={C.muted} />
          </Pressable>
          <View style={styles.divider} />
          <View style={styles.row}>
            <View style={styles.icon}>
              <Feather name="shield" size={18} color={C.green} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>素材与隐私</Text>
              <Text style={styles.rowSub}>
                演示素材不上传；真实分析会上传原文件供模型处理
              </Text>
            </View>
          </View>
          <View style={styles.divider} />
          <View style={styles.row}>
            <View style={styles.icon}>
              <Feather name="activity" size={18} color={C.green} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>关于动作建议</Text>
              <Text style={styles.rowSub}>
                分析只作为训练参考，不替代专业指导
              </Text>
            </View>
          </View>
        </View>
        <Text style={styles.group}>数据管理</Text>
        <View style={styles.list}>
          <Pressable
            accessibilityRole="button"
            style={styles.row}
            onPress={() =>
              confirmAction(
                "清除演示数据",
                "将清除当前运行中所有对话和素材引用。",
                clear,
              )
            }
          >
            <View style={styles.icon}>
              <Feather name="trash-2" size={18} color="#a86c4a" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>清除演示记录</Text>
              <Text style={styles.rowSub}>不会删除系统相册中的原文件</Text>
            </View>
            <Feather name="chevron-right" size={17} color={C.muted} />
          </Pressable>
        </View>
        <Text style={styles.version}>MOVE WELL · PREVIEW 1.0</Text>
      </ScrollView>
      <BottomNav active="profile" />
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
  userCard: {
    marginTop: 25,
    backgroundColor: C.green,
    borderRadius: 23,
    padding: 20,
    flexDirection: "row",
    gap: 14,
    alignItems: "center",
  },
  avatar: {
    width: 56,
    height: 56,
    borderRadius: 19,
    backgroundColor: "#ffffff1c",
    alignItems: "center",
    justifyContent: "center",
  },
  name: { color: "white", fontSize: 16, fontWeight: "800" },
  sub: { color: "#c5d9cc", fontSize: 11, marginTop: 6 },
  group: {
    color: C.ink,
    fontWeight: "800",
    fontSize: 14,
    marginTop: 28,
    marginBottom: 12,
  },
  list: { backgroundColor: "white", borderRadius: 20, paddingHorizontal: 15 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 15,
  },
  icon: {
    width: 42,
    height: 42,
    borderRadius: 14,
    backgroundColor: C.soft,
    alignItems: "center",
    justifyContent: "center",
  },
  rowTitle: { color: C.ink, fontWeight: "800", fontSize: 13 },
  rowSub: { color: C.muted, fontSize: 10, marginTop: 5, lineHeight: 16 },
  divider: { height: 1, backgroundColor: C.line, marginLeft: 54 },
  version: {
    color: "#a9b4ac",
    textAlign: "center",
    fontSize: 9,
    letterSpacing: 1.4,
    marginTop: 30,
  },
});
