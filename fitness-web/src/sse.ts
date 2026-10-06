/** Incremental SSE framing; UTF-8 decoding is performed by the fetch reader. */
export class SseParser {
  private buffer = "";

  push(text: string): Array<{ event: string; data: string }> {
    this.buffer = (this.buffer + text).replace(/\r\n/g, "\n");
    if (this.buffer.length > 1_048_576) throw new Error("流式消息超过允许大小");
    const events: Array<{ event: string; data: string }> = [];
    let boundary: number;
    while ((boundary = this.buffer.indexOf("\n\n")) >= 0) {
      const frame = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith(":")) continue;
        const separator = line.indexOf(":");
        const field = separator < 0 ? line : line.slice(0, separator);
        let value = separator < 0 ? "" : line.slice(separator + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "event") event = value;
        if (field === "data") data.push(value);
      }
      if (data.length) events.push({ event, data: data.join("\n") });
    }
    return events;
  }
}
