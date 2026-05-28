/* eslint-disable @typescript-eslint/no-explicit-any */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { H264StreamClient, isWebCodecsSupported } from "./h264VideoStream";

// ---------- helpers ---------------------------------------------------------

/**
 * Minimal mock for the global `WebSocket` constructor + instance. We capture
 * the constructed ws, expose handlers for the test to drive, and record what
 * the client tried to send (currently nothing — client is receive-only).
 */
function installMockWebSocket(): {
  instances: MockWebSocket[];
  uninstall: () => void;
} {
  const instances: MockWebSocket[] = [];
  class MockWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    url: string;
    binaryType: BinaryType = "blob";
    readyState = 0;
    onmessage: ((ev: { data: string | ArrayBuffer }) => void) | null = null;
    onerror: ((ev: Event) => void) | null = null;
    onclose: ((ev: { code: number; reason: string }) => void) | null = null;
    onopen: ((ev: Event) => void) | null = null;
    close = vi.fn(() => {
      this.readyState = MockWebSocket.CLOSED;
    });
    constructor(url: string) {
      this.url = url;
      instances.push(this);
      this.readyState = MockWebSocket.OPEN;
    }
  }
  const original = (globalThis as any).WebSocket;
  (globalThis as any).WebSocket = MockWebSocket as unknown as typeof WebSocket;
  return {
    instances: instances as unknown as MockWebSocket[],
    uninstall: () => {
      (globalThis as any).WebSocket = original;
    },
  };
}

/**
 * Mock `VideoDecoder` constructor that captures configure/decode/close calls.
 * Records the chunk data so tests can assert on what was submitted (including
 * SPS+PPS prepended to keyframes).
 */
function installMockVideoDecoder(): {
  instances: MockDecoder[];
  uninstall: () => void;
} {
  const instances: MockDecoder[] = [];
  class MockEncodedVideoChunk {
    type: "key" | "delta";
    timestamp: number;
    data: Uint8Array;
    constructor(init: { type: "key" | "delta"; timestamp: number; data: Uint8Array }) {
      this.type = init.type;
      this.timestamp = init.timestamp;
      // Copy because the test's frame buffer may be reused.
      this.data = new Uint8Array(init.data);
    }
  }
  class MockDecoder {
    static isConfigSupported = vi.fn();
    state: "unconfigured" | "configured" | "closed" = "unconfigured";
    decodeQueueSize = 0;
    configureCalls: Array<{
      codec: string;
      avc?: { format: "annexb" | "avc" };
      optimizeForLatency?: boolean;
    }> = [];
    decoded: Array<{ type: string; timestamp: number; data: Uint8Array }> = [];
    resetCalls = 0;
    output: (f: unknown) => void;
    errorCb: (e: Error) => void;
    constructor(init: { output: (f: unknown) => void; error: (e: Error) => void }) {
      this.output = init.output;
      this.errorCb = init.error;
      instances.push(this);
    }
    configure(cfg: {
      codec: string;
      avc?: { format: "annexb" | "avc" };
      optimizeForLatency?: boolean;
    }) {
      this.configureCalls.push(cfg);
      this.state = "configured";
    }
    decode(chunk: { type: string; timestamp: number; data: Uint8Array }) {
      this.decoded.push({
        type: chunk.type,
        timestamp: chunk.timestamp,
        data: new Uint8Array(chunk.data),
      });
    }
    reset() {
      this.resetCalls += 1;
      this.decodeQueueSize = 0;
    }
    close() {
      this.state = "closed";
    }
  }
  const originalDecoder = (globalThis as any).VideoDecoder;
  const originalChunk = (globalThis as any).EncodedVideoChunk;
  (globalThis as any).VideoDecoder = MockDecoder as unknown as typeof VideoDecoder;
  (globalThis as any).EncodedVideoChunk =
    MockEncodedVideoChunk as unknown as typeof EncodedVideoChunk;
  return {
    instances,
    uninstall: () => {
      (globalThis as any).VideoDecoder = originalDecoder;
      (globalThis as any).EncodedVideoChunk = originalChunk;
    },
  };
}

type MockDecoder = ReturnType<typeof installMockVideoDecoder>["instances"][number];
type MockWebSocket = ReturnType<typeof installMockWebSocket>["instances"][number];

const FLAG_CONFIG = 0x01;
const FLAG_KEY = 0x02;

function packPacket(flags: number, pts: bigint, payload: Uint8Array): ArrayBuffer {
  const buf = new ArrayBuffer(9 + payload.byteLength);
  const view = new DataView(buf);
  view.setUint8(0, flags);
  view.setBigUint64(1, pts);
  new Uint8Array(buf, 9).set(payload);
  return buf;
}

// ---------- tests -----------------------------------------------------------

describe("isWebCodecsSupported", () => {
  let restore: () => void;
  afterEach(() => restore?.());

  it("returns true when VideoDecoder is on window", () => {
    const original = (globalThis as any).VideoDecoder;
    (globalThis as any).VideoDecoder = function () {};
    restore = () => {
      (globalThis as any).VideoDecoder = original;
    };
    expect(isWebCodecsSupported()).toBe(true);
  });

  it("returns false when VideoDecoder is absent", () => {
    const original = (globalThis as any).VideoDecoder;
    delete (globalThis as any).VideoDecoder;
    restore = () => {
      (globalThis as any).VideoDecoder = original;
    };
    expect(isWebCodecsSupported()).toBe(false);
  });
});

describe("H264StreamClient", () => {
  let ws: ReturnType<typeof installMockWebSocket>;
  let dec: ReturnType<typeof installMockVideoDecoder>;

  beforeEach(() => {
    ws = installMockWebSocket();
    dec = installMockVideoDecoder();
  });
  afterEach(() => {
    ws.uninstall();
    dec.uninstall();
  });

  it("reports an error when WebCodecs is unavailable", () => {
    dec.uninstall();
    const onError = vi.fn();
    new H264StreamClient("ws://x/stream", { onFrame: vi.fn(), onError }).start();
    expect(onError).toHaveBeenCalledOnce();
    expect(onError.mock.calls[0][0].message).toMatch(/WebCodecs/i);
  });

  it("configures the decoder with the codec string from the handshake", () => {
    const client = new H264StreamClient("ws://x/stream", {
      onFrame: vi.fn(),
      onHandshake: vi.fn(),
    });
    client.start();
    const sock = ws.instances[0];
    sock.onmessage?.({
      data: JSON.stringify({ codec: "avc1.42E029", width: 720, height: 1280 }),
    });
    const decoder = dec.instances[0];
    expect(decoder.configureCalls).toHaveLength(1);
    expect(decoder.configureCalls[0].codec).toBe("avc1.42E029");
    expect(decoder.configureCalls[0].avc).toEqual({ format: "annexb" });
    expect(decoder.configureCalls[0].optimizeForLatency).toBe(true);
  });

  it("caches the config packet without decoding it, then prepends SPS+PPS to the next keyframe", () => {
    const client = new H264StreamClient("ws://x/stream", { onFrame: vi.fn() });
    client.start();
    const sock = ws.instances[0];
    sock.onmessage?.({
      data: JSON.stringify({ codec: "avc1.42E029", width: 720, height: 1280 }),
    });
    const decoder = dec.instances[0];

    const cfg = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x67, 0x42, 0xE0, 0x29]);
    sock.onmessage?.({ data: packPacket(FLAG_CONFIG, 0n, cfg) });
    expect(decoder.decoded).toHaveLength(0);

    const idr = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x65, 0xAA]);
    sock.onmessage?.({ data: packPacket(FLAG_KEY, 1_000_000n, idr) });
    expect(decoder.decoded).toHaveLength(1);
    expect(decoder.decoded[0].type).toBe("key");
    expect(decoder.decoded[0].timestamp).toBe(1_000_000);
    // Keyframe data must be cfg ++ idr (SPS+PPS prepended to access unit).
    const merged = new Uint8Array(cfg.length + idr.length);
    merged.set(cfg, 0);
    merged.set(idr, cfg.length);
    expect(Array.from(decoder.decoded[0].data)).toEqual(Array.from(merged));
  });

  it("drops delta packets that arrive before the first keyframe", () => {
    const client = new H264StreamClient("ws://x/stream", { onFrame: vi.fn() });
    client.start();
    const sock = ws.instances[0];
    sock.onmessage?.({
      data: JSON.stringify({ codec: "avc1.42E029", width: 720, height: 1280 }),
    });
    const decoder = dec.instances[0];

    const delta = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x41, 0xDE, 0xAD]);
    sock.onmessage?.({ data: packPacket(0, 100n, delta) });
    expect(decoder.decoded).toHaveLength(0);
  });

  it("forwards subsequent deltas as 'delta' without prepending config", () => {
    const client = new H264StreamClient("ws://x/stream", { onFrame: vi.fn() });
    client.start();
    const sock = ws.instances[0];
    sock.onmessage?.({
      data: JSON.stringify({ codec: "avc1.42E029", width: 720, height: 1280 }),
    });
    const decoder = dec.instances[0];
    const cfg = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x67, 0x42, 0xE0, 0x29]);
    const idr = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x65, 0xAA]);
    const delta = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x41, 0xBB]);
    sock.onmessage?.({ data: packPacket(FLAG_CONFIG, 0n, cfg) });
    sock.onmessage?.({ data: packPacket(FLAG_KEY, 1_000n, idr) });
    sock.onmessage?.({ data: packPacket(0, 2_000n, delta) });

    expect(decoder.decoded).toHaveLength(2);
    expect(decoder.decoded[1].type).toBe("delta");
    expect(decoder.decoded[1].timestamp).toBe(2_000);
    // Delta must NOT have config prepended — only keyframes get the SPS+PPS prefix.
    expect(Array.from(decoder.decoded[1].data)).toEqual(Array.from(delta));
  });

  it("drops queued latency and waits for a keyframe when the decoder falls behind", () => {
    const client = new H264StreamClient("ws://x/stream", { onFrame: vi.fn() });
    client.start();
    const sock = ws.instances[0];
    sock.onmessage?.({
      data: JSON.stringify({ codec: "avc1.42E029", width: 720, height: 1280 }),
    });
    const decoder = dec.instances[0];
    const cfg = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x67, 0x42, 0xE0, 0x29]);
    const idr = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x65, 0xAA]);
    const delta = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x41, 0xBB]);

    sock.onmessage?.({ data: packPacket(FLAG_CONFIG, 0n, cfg) });
    sock.onmessage?.({ data: packPacket(FLAG_KEY, 1_000n, idr) });
    decoder.decodeQueueSize = 3;
    sock.onmessage?.({ data: packPacket(0, 2_000n, delta) });
    expect(decoder.resetCalls).toBe(1);
    expect(decoder.decoded).toHaveLength(1);

    sock.onmessage?.({ data: packPacket(0, 3_000n, delta) });
    expect(decoder.decoded).toHaveLength(1);

    sock.onmessage?.({ data: packPacket(FLAG_KEY, 4_000n, idr) });
    expect(decoder.decoded).toHaveLength(2);
    expect(decoder.decoded[1].type).toBe("key");
    expect(decoder.decoded[1].timestamp).toBe(4_000);
  });

  it("ignores binary packets that arrive before the handshake JSON", () => {
    const client = new H264StreamClient("ws://x/stream", { onFrame: vi.fn() });
    client.start();
    const sock = ws.instances[0];
    // No handshake → decoder not constructed yet. Sending a config packet
    // must not crash and must not create a decoder.
    const cfg = new Uint8Array([0x00, 0x00, 0x00, 0x01, 0x67]);
    sock.onmessage?.({ data: packPacket(FLAG_CONFIG, 0n, cfg) });
    expect(dec.instances).toHaveLength(0);
  });

  it("forwards close reasons through onClose", () => {
    const onClose = vi.fn();
    const client = new H264StreamClient("ws://x/stream", { onFrame: vi.fn(), onClose });
    client.start();
    ws.instances[0].onclose?.({ code: 4503, reason: "scrcpy not running" });
    expect(onClose).toHaveBeenCalledWith("scrcpy not running");
  });

  it("closes the WebSocket and decoder on stop()", () => {
    const client = new H264StreamClient("ws://x/stream", { onFrame: vi.fn() });
    client.start();
    ws.instances[0].onmessage?.({
      data: JSON.stringify({ codec: "avc1.42E029", width: 720, height: 1280 }),
    });
    const sock = ws.instances[0];
    const decoder = dec.instances[0];
    client.stop();
    expect(sock.close).toHaveBeenCalled();
    expect(decoder.state).toBe("closed");
  });

  it("does not surface a fake error when the operator stops the stream", () => {
    // Regression guard: dropdown switch / component unmount calls stop().
    // ws.close() then triggers a "normal closure" close event (code 1000).
    // Forwarding that to onClose would display "Live video stream closed:
    // code 1000" in the UI, which is misleading because nothing went wrong.
    const onClose = vi.fn();
    const client = new H264StreamClient("ws://x/stream", { onFrame: vi.fn(), onClose });
    client.start();
    const sock = ws.instances[0];
    client.stop();
    // Simulate the browser dispatching the close event after our explicit
    // ws.close() — should be a no-op now that handlers are detached.
    sock.onclose?.({ code: 1000, reason: "" });
    expect(onClose).not.toHaveBeenCalled();
  });
});
