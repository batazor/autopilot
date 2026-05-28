"use client";

/**
 * Live H.264 stream client for the approvals UI.
 *
 * Pulls Annex-B NAL packets from the backend WebSocket
 * (`/api/instances/{id}/stream.h264.ws`) and feeds them to WebCodecs
 * `VideoDecoder`. Each decoded `VideoFrame` is handed to `onFrame`, which the
 * caller draws into a `<canvas>` (the existing approval canvas, so overlays
 * keep compositing on top exactly like in the still-image path).
 *
 * Why WebCodecs and not MSE + fragmented MP4:
 *   - scrcpy already gives us Annex-B; MSE would need a remuxer.
 *   - WebCodecs accepts Annex-B directly when `description` is omitted from
 *     `VideoDecoder.configure`.
 *   - One fewer encode/decode hop → ~30-50ms total latency.
 *
 * Browser support: Chrome 94+, Edge 94+, Safari 16.4+. Firefox lacks
 * `VideoDecoder` in stable; callers should fall back to the still-image
 * preview when `isWebCodecsSupported()` returns false.
 */

export type StreamHandshake = {
  codec: string;
  width: number;
  height: number;
};

export type StreamCallbacks = {
  onHandshake?: (info: StreamHandshake) => void;
  onFrame: (frame: VideoFrame) => void;
  onError?: (err: Error) => void;
  onClose?: (reason: string) => void;
};

const FLAG_CONFIG = 0x01;
const FLAG_KEY = 0x02;
const HEADER_BYTES = 9; // u8 flags + u64 PTS
const MAX_DECODE_QUEUE_SIZE = 2;

export function describeWsCloseCode(code: number): string {
  switch (code) {
    case 1000:
      return "connection closed";
    case 1001:
      return "server went away";
    case 1002:
      return "protocol error";
    case 1003:
      return "unsupported data";
    case 1005:
      return "closed with no status";
    case 1006:
      return "connection dropped (network or server crash)";
    case 1007:
      return "invalid frame payload";
    case 1008:
      return "policy violation";
    case 1009:
      return "frame too large";
    case 1010:
      return "required extension missing";
    case 1011:
      return "server error";
    case 1012:
      return "server restarting";
    case 1013:
      return "server busy — try again later";
    case 1014:
      return "bad gateway";
    case 1015:
      return "TLS handshake failed";
    default:
      return `closed (code ${code})`;
  }
}

type H264VideoDecoderConfig = VideoDecoderConfig & {
  avc?: { format: "annexb" | "avc" };
};

export function isWebCodecsSupported(): boolean {
  if (typeof window === "undefined") return false;
  // VideoDecoder is the smallest surface we actually need.
  return typeof (window as unknown as { VideoDecoder?: unknown }).VideoDecoder === "function";
}

export class H264StreamClient {
  private ws: WebSocket | null = null;
  private decoder: VideoDecoder | null = null;
  private synced = false;
  private closed = false;
  // SPS+PPS bytes from the most recent config packet. WebCodecs ``VideoDecoder``
  // without a ``description`` field expects parameter sets in-band, so we
  // prepend these to every keyframe payload before submitting to the decoder.
  // The codec string alone (e.g. ``avc1.42E029``) tells the decoder profile +
  // level but does not supply actual SPS/PPS data.
  private codecConfig: Uint8Array | null = null;

  constructor(
    private readonly url: string,
    private readonly callbacks: StreamCallbacks,
  ) {}

  start(): void {
    if (!isWebCodecsSupported()) {
      this.callbacks.onError?.(new Error("WebCodecs VideoDecoder not supported in this browser"));
      return;
    }
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch (e) {
      this.callbacks.onError?.(e instanceof Error ? e : new Error(String(e)));
      return;
    }
    ws.binaryType = "arraybuffer";
    ws.onmessage = (ev) => this.handleMessage(ev.data);
    ws.onerror = () => {
      // The "error" event carries no usable detail — the next "close" event
      // is where reason/code live, so we let onclose surface it.
    };
    ws.onclose = (ev) => {
      // Intentional stops (component unmount, dropdown switch) detach the
      // handler before calling ws.close(), so reaching here means the server
      // (or the network) closed the socket on its own — that's the only case
      // worth surfacing as an error to the operator.
      if (this.closed) return;
      const reason = ev.reason || describeWsCloseCode(ev.code);
      this.cleanup();
      this.callbacks.onClose?.(reason);
    };
    this.ws = ws;
  }

  stop(): void {
    this.closed = true;
    this.cleanup();
  }

  private cleanup(): void {
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      // Detach handlers BEFORE close() so an intentional stop doesn't fire
      // ``onclose`` and surface "code 1000" as a fake error in the UI. The
      // browser still delivers the close event to detached handlers, just
      // without callbacks.
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      if (ws.readyState !== WebSocket.CLOSED) {
        try {
          ws.close();
        } catch {
          // ignore
        }
      }
    }
    const dec = this.decoder;
    this.decoder = null;
    if (dec && dec.state !== "closed") {
      try {
        dec.close();
      } catch {
        // ignore — closing an already-errored decoder throws on some engines
      }
    }
  }

  private handleMessage(data: ArrayBuffer | string): void {
    if (this.closed) return;
    if (typeof data === "string") {
      // Handshake JSON: arrives first, exactly once.
      try {
        const info = JSON.parse(data) as StreamHandshake;
        this.configureDecoder(info);
        this.callbacks.onHandshake?.(info);
      } catch (e) {
        this.callbacks.onError?.(e instanceof Error ? e : new Error(String(e)));
      }
      return;
    }
    if (!this.decoder) {
      // Binary frame arrived before handshake — protocol violation, ignore.
      return;
    }
    const buf = data as ArrayBuffer;
    if (buf.byteLength < HEADER_BYTES) return;
    const view = new DataView(buf);
    const flags = view.getUint8(0);
    // u64 PTS in microseconds. WebCodecs accepts a regular Number timestamp
    // (also microseconds); scrcpy's PTS fits in 53 bits for any practical
    // session length, so the Number cast is safe.
    const pts = Number(view.getBigUint64(1));
    const payload = new Uint8Array(buf, HEADER_BYTES, buf.byteLength - HEADER_BYTES);

    const isConfig = (flags & FLAG_CONFIG) !== 0;
    const isKey = (flags & FLAG_KEY) !== 0;

    if (isConfig) {
      // Cache for the next keyframe — the decoder needs SPS+PPS in-band.
      // Copy because the buffer view into the WS message will be GC'd.
      this.codecConfig = new Uint8Array(payload);
      return;
    }
    if (!this.synced) {
      // Wait for a keyframe so the first chunk submitted to the decoder is a
      // self-contained access unit. The server already drops deltas until the
      // next key, but we double-check here.
      if (!isKey) return;
      this.synced = true;
    }
    if (this.decoder.decodeQueueSize > MAX_DECODE_QUEUE_SIZE) {
      // If rendering/decoding stalls, do not let WebCodecs build seconds of
      // latency. Drop queued chunks and resume only from a keyframe; continuing
      // after dropping arbitrary deltas would corrupt the H.264 reference chain.
      try {
        this.decoder.reset();
      } catch {
        // Some engines throw if reset races with close/error. Surface the next
        // decode error through the decoder error callback instead of crashing
        // the WebSocket handler.
      }
      this.synced = false;
      if (!isKey) return;
      this.synced = true;
    }
    let chunkData: Uint8Array = payload;
    if (isKey && this.codecConfig) {
      // Prepend SPS+PPS so the keyframe access unit carries its own parameter
      // sets. WebCodecs without ``description`` requires in-band parameter
      // sets; the codec string alone is not enough.
      const cfg = this.codecConfig;
      const merged = new Uint8Array(cfg.length + payload.length);
      merged.set(cfg, 0);
      merged.set(payload, cfg.length);
      chunkData = merged;
    }
    try {
      const chunk = new EncodedVideoChunk({
        type: isKey ? "key" : "delta",
        timestamp: pts,
        data: chunkData,
      });
      this.decoder.decode(chunk);
    } catch (e) {
      this.callbacks.onError?.(e instanceof Error ? e : new Error(String(e)));
    }
  }

  private configureDecoder(info: StreamHandshake): void {
    if (this.decoder) {
      try {
        this.decoder.close();
      } catch {
        // ignore
      }
    }
    const decoder = new VideoDecoder({
      output: (frame) => {
        if (this.closed) {
          frame.close();
          return;
        }
        this.callbacks.onFrame(frame);
      },
      error: (e) => this.callbacks.onError?.(e instanceof Error ? e : new Error(String(e))),
    });
    // scrcpy emits Annex-B NAL units with start codes. Tell WebCodecs that
    // explicitly; otherwise some browsers assume AVCC and decode nothing.
    // `optimizeForLatency` tells the decoder not to reorder frames — scrcpy
    // emits IPP… so reorder isn't a thing here, and avoiding the reorder
    // buffer trims a frame or two of latency.
    const config: H264VideoDecoderConfig = {
      codec: info.codec,
      avc: { format: "annexb" },
      optimizeForLatency: true,
    };
    decoder.configure(config);
    this.decoder = decoder;
  }
}
