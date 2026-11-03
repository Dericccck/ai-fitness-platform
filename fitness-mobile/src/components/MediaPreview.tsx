import React from "react";
import { Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { VideoView, useVideoPlayer } from "expo-video";
import { MediaItem } from "../domain/types";

function Video({ uri, headers }: { uri: string; headers?: Record<string, string> }) {
  const player = useVideoPlayer(headers ? { uri, headers, useCaching: false } : uri);
  return (
    <VideoView
      player={player}
      style={styles.media}
      nativeControls
      contentFit="contain"
    />
  );
}

export function MediaPreview({
  media,
  close,
  headers,
  caption = "仅在本机预览 · 演示模式不会上传素材",
}: {
  media: MediaItem | null;
  close: () => void;
  headers?: Record<string, string>;
  caption?: string;
}) {
  return (
    <Modal visible={!!media} animationType="fade" onRequestClose={close}>
      <SafeAreaView style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.title}>
            {media?.kind === "video" ? "视频预览" : "图片预览"}
          </Text>
          <Pressable
            accessibilityLabel="关闭预览"
            accessibilityRole="button"
            onPress={close}
          >
            <Feather name="x" size={24} color="white" />
          </Pressable>
        </View>
        {media &&
          (media.kind === "image" ? (
            <Image
              source={{ uri: media.uri, headers, cache: "reload" }}
              style={styles.media}
              resizeMode="contain"
            />
          ) : (
            <Video uri={media.uri} headers={headers} />
          ))}
        <Text style={styles.caption}>{caption}</Text>
      </SafeAreaView>
    </Modal>
  );
}
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#101b17" },
  header: {
    height: 56,
    flexDirection: "row",
    paddingHorizontal: 22,
    alignItems: "center",
    justifyContent: "space-between",
  },
  title: { color: "white", fontWeight: "700", fontSize: 15 },
  media: { flex: 1, width: "100%" },
  caption: { textAlign: "center", padding: 20, color: "#a8b9ad", fontSize: 11 },
});
