import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

// Web has no native secure enclave: the explicitly enabled localhost simulator
// keeps all credentials and pending drafts in memory and loses them on reload.
const memory = new Map<string, string>();
export const desktopSimulation =
  __DEV__ && Platform.OS === "web" && process.env.EXPO_PUBLIC_DESKTOP_SIMULATION === "1";
export const WHEN_UNLOCKED_THIS_DEVICE_ONLY = SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY;
export async function getItemAsync(key: string): Promise<string | null> {
  return desktopSimulation ? memory.get(key) ?? null : SecureStore.getItemAsync(key);
}
export async function setItemAsync(key: string, value: string, options?: SecureStore.SecureStoreOptions): Promise<void> {
  if (desktopSimulation) { memory.set(key, value); return; }
  return SecureStore.setItemAsync(key, value, options);
}
export async function deleteItemAsync(key: string): Promise<void> {
  if (desktopSimulation) { memory.delete(key); return; }
  return SecureStore.deleteItemAsync(key);
}
