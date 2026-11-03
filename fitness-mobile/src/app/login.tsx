import React, { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather, MaterialCommunityIcons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { Button, C, S, TopBar } from "../components/ui";
import { useAccount } from "../state/AccountContext";
import { desktopSimulation } from "../network/vault";

export default function Login() {
  const router = useRouter();
  const { configured, login } = useAccount();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [organization, setOrganization] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    if (busy || !username.trim() || !password || !organization.trim()) return;
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password, organization.trim());
      setPassword("");
      router.replace("/live");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败，请重试。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <SafeAreaView style={S.screen} edges={["top", "bottom"]}>
      <TopBar title="账户与登录" />
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView keyboardShouldPersistTaps="handled">
          <View style={styles.content}>
            <View style={styles.icon}>
              <MaterialCommunityIcons
                name="dumbbell"
                size={30}
                color={C.bright}
              />
            </View>
            <Text style={S.eyebrow}>MOVE WELL ACCOUNT</Text>
            <Text style={[S.h1, { marginTop: 12 }]}>
              你的训练，{"\n"}值得被好好记录。
            </Text>
            {configured ? (
              <>
                <Text style={[S.body, { marginTop: 14 }]}>
                  {desktopSimulation ? "本机模拟：账号 demo，密码 demo，机构 org-1。不会调用真实模型。" : "使用健身平台账户登录，查看和管理你的训练对话。"}
                </Text>
                <TextInput
                  accessibilityLabel="账号"
                  placeholder="账号"
                  autoCapitalize="none"
                  value={username}
                  onChangeText={setUsername}
                  style={styles.input}
                />
                <TextInput
                  accessibilityLabel="密码"
                  placeholder="密码"
                  secureTextEntry
                  autoCapitalize="none"
                  value={password}
                  onChangeText={setPassword}
                  style={styles.input}
                />
                <TextInput
                  accessibilityLabel="机构标识"
                  placeholder="机构标识"
                  autoCapitalize="none"
                  value={organization}
                  onChangeText={setOrganization}
                  style={styles.input}
                />
                {error ? (
                  <Text accessibilityLiveRegion="polite" style={styles.error}>
                    {error}
                  </Text>
                ) : null}
                <Button
                  label={busy ? "正在登录…" : "登录"}
                  disabled={
                    busy ||
                    !username.trim() ||
                    !password ||
                    !organization.trim()
                  }
                  onPress={submit}
                  style={{ marginTop: 18 }}
                />
              </>
            ) : (
              <Text style={[S.body, { marginTop: 14 }]}>
                真实服务尚未配置，当前可先体验演示页面，无需提供手机号、密码或模型密钥。
              </Text>
            )}
            <View style={styles.notice}>
              <Feather name="lock" size={18} color={C.green} />
              <Text style={styles.noticeText}>
                {configured
                  ? desktopSimulation ? "仅用于本机模拟，凭证只保存在内存中，刷新页面后需要重新登录。" : "登录凭证通过加密连接发送到已配置的认证服务，不会保存在对话中。"
                  : "当前演示不收集账户凭证，素材和对话仅保留在本次运行中。"}
              </Text>
            </View>
            <Button
              label="继续体验"
              onPress={() => router.back()}
              style={{ marginTop: 24 }}
            />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  content: { paddingHorizontal: 25, paddingTop: 44 },
  icon: {
    width: 65,
    height: 65,
    borderRadius: 23,
    backgroundColor: C.green,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 29,
  },
  notice: {
    marginTop: 28,
    borderRadius: 18,
    padding: 16,
    backgroundColor: "#eaf2e6",
    flexDirection: "row",
    gap: 11,
  },
  noticeText: { color: "#617b61", flex: 1, fontSize: 12, lineHeight: 20 },
  input: {
    backgroundColor: "white",
    borderRadius: 13,
    paddingHorizontal: 15,
    minHeight: 49,
    marginTop: 12,
    color: C.ink,
    fontSize: 14,
  },
  error: { color: "#a34a34", fontSize: 12, marginTop: 10 },
});
