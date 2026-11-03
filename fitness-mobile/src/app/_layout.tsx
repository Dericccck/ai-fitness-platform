import React from "react";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { DemoProvider } from "../state/DemoContext";
import { AccountProvider } from "../state/AccountContext";

export default function Layout() {
  return (
    <SafeAreaProvider>
      <AccountProvider>
        <DemoProvider>
          <StatusBar style="dark" />
          <Stack
            screenOptions={{
              headerShown: false,
              animation: "slide_from_right",
            }}
          />
        </DemoProvider>
      </AccountProvider>
    </SafeAreaProvider>
  );
}
