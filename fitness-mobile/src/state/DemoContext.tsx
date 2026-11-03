import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import * as Crypto from "expo-crypto";
import {
  Conversation,
  DemoRun,
  MediaItem,
  DEFAULT_QUESTION,
  validateSelection,
} from "../domain/types";

type DemoContextValue = {
  conversations: Conversation[];
  createConversation: () => string;
  send: (
    conversationId: string,
    question: string,
    media: MediaItem[],
  ) => string;
  cancel: (conversationId: string, runId: string) => void;
  removeConversation: (id: string) => void;
  removeRun: (conversationId: string, runId: string) => void;
  clear: () => void;
};
const Context = createContext<DemoContextValue | null>(null);

export function DemoProvider({ children }: { children: React.ReactNode }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>[]>());
  const clearTimers = useCallback((id: string) => {
    timers.current.get(id)?.forEach(clearTimeout);
    timers.current.delete(id);
  }, []);
  useEffect(() => {
    const current = timers.current;
    return () => {
      current.forEach((items) => items.forEach(clearTimeout));
      current.clear();
    };
  }, []);
  const createConversation = useCallback(() => {
    const id = Crypto.randomUUID();
    setConversations((current) => [
      { id, createdAt: Date.now(), runs: [] },
      ...current,
    ]);
    return id;
  }, []);
  const updateRun = useCallback(
    (conversationId: string, runId: string, state: DemoRun["state"]) => {
      setConversations((current) =>
        current.map((c) =>
          c.id !== conversationId
            ? c
            : {
                ...c,
                runs: c.runs.map((run) =>
                  run.id !== runId || run.state === "CANCELLED"
                    ? run
                    : { ...run, state },
                ),
              },
        ),
      );
    },
    [],
  );
  const send = useCallback(
    (conversationId: string, question: string, media: MediaItem[]) => {
      const error = validateSelection(media);
      if (error) throw new Error(error);
      if (!question.trim() && !media.length)
        throw new Error("请输入问题或选择素材。");
      const id = Crypto.randomUUID();
      const run: DemoRun = {
        id,
        question: question.trim() || DEFAULT_QUESTION,
        media: [...media],
        createdAt: Date.now(),
        state: "QUEUED",
      };
      setConversations((current) =>
        current.map((c) =>
          c.id === conversationId ? { ...c, runs: [...c.runs, run] } : c,
        ),
      );
      timers.current.set(id, [
        setTimeout(() => updateRun(conversationId, id, "RUNNING"), 700),
        setTimeout(() => {
          updateRun(conversationId, id, "COMPLETED");
          timers.current.delete(id);
        }, 2100),
      ]);
      return id;
    },
    [updateRun],
  );
  const cancel = useCallback(
    (conversationId: string, runId: string) => {
      clearTimers(runId);
      updateRun(conversationId, runId, "CANCELLED");
    },
    [clearTimers, updateRun],
  );
  const removeRun = useCallback(
    (conversationId: string, runId: string) => {
      clearTimers(runId);
      setConversations((current) =>
        current.map((c) =>
          c.id === conversationId
            ? { ...c, runs: c.runs.filter((r) => r.id !== runId) }
            : c,
        ),
      );
    },
    [clearTimers],
  );
  const removeConversation = useCallback(
    (id: string) => {
      conversations
        .find((c) => c.id === id)
        ?.runs.forEach((r) => clearTimers(r.id));
      setConversations((current) => current.filter((c) => c.id !== id));
    },
    [clearTimers, conversations],
  );
  const clear = useCallback(() => {
    timers.current.forEach((_, id) => clearTimers(id));
    setConversations([]);
  }, [clearTimers]);
  const value = useMemo(
    () => ({
      conversations,
      createConversation,
      send,
      cancel,
      removeConversation,
      removeRun,
      clear,
    }),
    [
      conversations,
      createConversation,
      send,
      cancel,
      removeConversation,
      removeRun,
      clear,
    ],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useDemo() {
  const value = useContext(Context);
  if (!value) throw new Error("DemoProvider is missing");
  return value;
}
