import { test } from "node:test";
import assert from "node:assert/strict";
import { validateSelection } from "../src/domain/types.ts";

const image = {
  id: "1",
  uri: "file://image",
  kind: "image",
  name: "image.jpg",
  mimeType: "image/jpeg",
  size: 1024,
};
const video = {
  id: "2",
  uri: "file://video",
  kind: "video",
  name: "video.mp4",
  mimeType: "video/mp4",
  size: 1024,
  duration: 5000,
};

test("接受三张图片或一个视频", () => {
  assert.equal(
    validateSelection([image, { ...image, id: "3" }, { ...image, id: "4" }]),
    null,
  );
  assert.equal(validateSelection([video]), null);
});
test("拒绝混合素材、超过数量和过大文件", () => {
  assert.match(validateSelection([image, video]), /不能与图片混合/);
  assert.match(validateSelection([image, image, image, image]), /最多/);
  assert.match(
    validateSelection([{ ...image, size: 10 * 1024 * 1024 + 1 }]),
    /10 MB/,
  );
});
test("拒绝不支持的格式和过长视频", () => {
  assert.match(
    validateSelection([{ ...image, mimeType: "image/heic" }]),
    /格式/,
  );
  assert.match(validateSelection([{ ...video, duration: 120001 }]), /时长/);
});
