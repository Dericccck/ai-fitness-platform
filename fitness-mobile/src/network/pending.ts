import * as SecureStore from "./vault";
import * as Crypto from "expo-crypto";
import type { MediaItem } from "../domain/types";

const POINTER = "fitness.pending.current.v1";
let queue: Promise<unknown> = Promise.resolve();
function serialized<T>(operation: () => Promise<T>): Promise<T> {
  const result = queue.then(operation);
  queue = result.catch(() => undefined);
  return result;
}
type Pointer = { prefix: string; count: number; scope: string; runId: string };
export type PendingSend = {
  conversationId: string;
  runId: string;
  clientMessageId: string;
  question: string;
  media: MediaItem[];
  uploadKeys: string[];
  uploadIds: string[];
  reservedAssetIds: string[];
  assetIds: string[];
  createdAt: number;
};

async function scopeHash(scope: string): Promise<string> {
  return Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, scope);
}

async function pointer(): Promise<Pointer | null> {
  const raw = await SecureStore.getItemAsync(POINTER);
  if (!raw) return null;
  try {
    const parsed: Pointer = JSON.parse(raw);
    return /^fitness\.pending\.[0-9a-f-]{36}$/.test(parsed.prefix) &&
      Number.isInteger(parsed.count) &&
      parsed.count > 0 &&
      parsed.count <= 100 &&
      /^[0-9a-f]{64}$/.test(parsed.scope) &&
      /^[0-9a-f-]{36}$/.test(parsed.runId)
      ? parsed
      : null;
  } catch {
    return null;
  }
}

async function removeChunks(value: Pointer | null): Promise<void> {
  if (!value) return;
  for (let i = 0; i < value.count; i++)
    await SecureStore.deleteItemAsync(`${value.prefix}.${i}`);
}

/** 内容仅放入平台安全存储；提交点通过单个指针切换，避免掉电读到半份草稿。 */
export async function savePending(
  scope: string,
  value: PendingSend,
  isCurrent: () => boolean = () => true,
): Promise<void> {
  return serialized(async () => {
    const old = await pointer();
    const hashed = await scopeHash(scope);
    if (old && old.scope !== hashed)
      throw new Error("设备中有另一账户未完成的发送，请先切回原账户处理。");
    if (old && old.runId !== value.runId)
      throw new Error("还有一条未完成的发送，请先处理。");
    if (!isCurrent()) throw new Error("账户已变化。");
    const prefix = `fitness.pending.${Crypto.randomUUID()}`;
    const chars = Array.from(JSON.stringify(value));
    const chunks = Array.from(
      { length: Math.ceil(chars.length / 300) },
      (_, i) => chars.slice(i * 300, (i + 1) * 300).join(""),
    );
    if (chunks.length > 100) throw new Error("待发送内容过长，无法安全保存。");
    try {
      for (let i = 0; i < chunks.length; i++)
        await SecureStore.setItemAsync(`${prefix}.${i}`, chunks[i], {
          keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
        });
      if (!isCurrent()) throw new Error("账户已变化。");
      await SecureStore.setItemAsync(
        POINTER,
        JSON.stringify({
          prefix,
          count: chunks.length,
          scope: hashed,
          runId: value.runId,
        }),
        { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY },
      );
    } catch (error) {
      await removeChunks({
        prefix,
        count: chunks.length,
        scope: "",
        runId: value.runId,
      });
      throw error;
    }
    await removeChunks(old);
  });
}

export async function loadPending(scope: string): Promise<PendingSend | null> {
  const meta = await pointer();
  if (!meta || meta.scope !== (await scopeHash(scope))) return null;
  const pieces = [];
  for (let i = 0; i < meta.count; i++) {
    const item = await SecureStore.getItemAsync(`${meta.prefix}.${i}`);
    if (item === null) return null;
    pieces.push(item);
  }
  try {
    const parsed: PendingSend = JSON.parse(pieces.join(""));
    if (
      !parsed.conversationId ||
      !parsed.runId ||
      !parsed.clientMessageId ||
      !Array.isArray(parsed.media) ||
      !Array.isArray(parsed.uploadKeys) ||
      !Array.isArray(parsed.uploadIds) ||
      !Array.isArray(parsed.reservedAssetIds) ||
      !Array.isArray(parsed.assetIds)
    )
      return null;
    return parsed;
  } catch {
    return null;
  }
}

export async function clearPending(
  scope?: string,
  runId?: string,
): Promise<void> {
  return serialized(async () => {
    const old = await pointer();
    if (scope && old && old.scope !== (await scopeHash(scope))) return;
    if (runId && old && old.runId !== runId) return;
    await SecureStore.deleteItemAsync(POINTER);
    await removeChunks(old);
  });
}
