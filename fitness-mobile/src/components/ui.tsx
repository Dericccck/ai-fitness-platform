import React from "react";
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type ViewStyle,
} from "react-native";
import { Feather } from "@expo/vector-icons";
import { useRouter } from "expo-router";

export const C = {
  ink: "#132923",
  muted: "#718078",
  green: "#174b3e",
  bright: "#cdec9f",
  cream: "#f7f8f3",
  white: "#ffffff",
  line: "#e9eee8",
  orange: "#f5a86b",
  soft: "#ecf2e8",
};
export const S = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.cream },
  content: { paddingHorizontal: 22, paddingBottom: 30 },
  eyebrow: {
    fontSize: 10,
    color: C.muted,
    fontWeight: "800",
    letterSpacing: 1.7,
  },
  h1: { color: C.ink, fontSize: 27, fontWeight: "800", letterSpacing: 0.2 },
  h2: { color: C.ink, fontSize: 20, fontWeight: "800" },
  body: { color: C.muted, fontSize: 13, lineHeight: 21 },
  card: { backgroundColor: C.white, borderRadius: 22, padding: 18 },
  row: { flexDirection: "row", alignItems: "center" },
});

export function IconButton({
  name,
  onPress,
  label,
  dark = false,
}: {
  name: keyof typeof Feather.glyphMap;
  onPress: () => void;
  label: string;
  dark?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      style={[styles.iconButton, dark && { backgroundColor: C.green }]}
    >
      <Feather name={name} size={19} color={dark ? "white" : C.ink} />
    </Pressable>
  );
}

export function Button({
  label,
  onPress,
  icon,
  secondary = false,
  disabled = false,
  style,
}: {
  label: string;
  onPress: () => void;
  icon?: keyof typeof Feather.glyphMap;
  secondary?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={[
        styles.button,
        secondary && styles.secondary,
        disabled && { opacity: 0.45 },
        style,
      ]}
    >
      {icon && (
        <Feather name={icon} size={17} color={secondary ? C.green : "white"} />
      )}
      <Text style={[styles.buttonText, secondary && { color: C.green }]}>
        {label}
      </Text>
    </Pressable>
  );
}

export function TopBar({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  const router = useRouter();
  return (
    <View style={styles.topBar}>
      <IconButton
        name="arrow-left"
        label="返回"
        onPress={() => router.back()}
      />
      <View style={{ flex: 1, alignItems: "center" }}>
        <Text style={styles.topTitle}>{title}</Text>
        {subtitle && <Text style={styles.topSub}>{subtitle}</Text>}
      </View>
      {action || <View style={{ width: 40 }} />}
    </View>
  );
}

export function DemoPill() {
  return (
    <View style={styles.demo}>
      <View style={styles.dot} />
      <Text style={styles.demoText}>演示模式</Text>
    </View>
  );
}

export function BottomNav({
  active,
}: {
  active: "home" | "records" | "profile";
}) {
  const router = useRouter();
  const links = [
    {
      id: "home" as const,
      label: "首页",
      icon: "grid" as const,
      onPress: () => router.replace("/"),
    },
    {
      id: "records" as const,
      label: "记录",
      icon: "layers" as const,
      onPress: () => router.replace("/records"),
    },
    {
      id: "profile" as const,
      label: "我的",
      icon: "user" as const,
      onPress: () => router.replace("/profile"),
    },
  ];
  return (
    <View style={styles.nav}>
      {links.map((link) => (
        <Pressable
          key={link.id}
          accessibilityRole="button"
          accessibilityLabel={link.label}
          onPress={link.onPress}
          style={styles.navItem}
        >
          <Feather
            name={link.icon}
            size={20}
            color={active === link.id ? C.green : "#9ca9a1"}
          />
          <Text
            style={[
              styles.navLabel,
              active === link.id && { color: C.green, fontWeight: "800" },
            ]}
          >
            {link.label}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  iconButton: {
    width: 40,
    height: 40,
    borderRadius: 15,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "white",
  },
  button: {
    minHeight: 50,
    borderRadius: 15,
    backgroundColor: C.green,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 9,
    paddingHorizontal: 16,
  },
  secondary: { backgroundColor: C.soft },
  buttonText: { color: "white", fontSize: 14, fontWeight: "800" },
  topBar: {
    height: 66,
    paddingHorizontal: 20,
    flexDirection: "row",
    alignItems: "center",
  },
  topTitle: { fontSize: 16, fontWeight: "800", color: C.ink },
  topSub: { fontSize: 10, color: C.muted, marginTop: 2 },
  demo: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: 9,
    paddingVertical: 6,
    borderRadius: 20,
    backgroundColor: "#eaf3df",
  },
  dot: { width: 6, height: 6, backgroundColor: "#76aa59", borderRadius: 4 },
  demoText: { color: "#477c40", fontSize: 10, fontWeight: "700" },
  nav: {
    minHeight: 65,
    backgroundColor: "white",
    borderTopWidth: 1,
    borderTopColor: C.line,
    flexDirection: "row",
    justifyContent: "space-around",
    paddingTop: 9,
    paddingBottom: 8,
  },
  navItem: { flex: 1, alignItems: "center", justifyContent: "center", gap: 4 },
  navLabel: { color: "#9ca9a1", fontSize: 10 },
});
