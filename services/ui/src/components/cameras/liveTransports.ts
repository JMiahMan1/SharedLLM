import {
  WEBRTC_CONNECT_MS, mediaSourceCtor, supportedCodecs,
} from '../../lib/cameraLive';

/**
 * The three ways go2rtc's video reaches a `<video>`, each started on an element
 * and stopped by the function it returns. A transport calls `fail` at most once
 * (and never after it was stopped); the player then tries the next mode.
 *
 * Written after go2rtc's own `video-rtc.js` (MIT): the same messages on its
 * WebSocket and the same MSE buffering, without its custom element.
 */
export interface TransportCallbacks {
  fail: (reason: string) => void;
}

export type StopFn = () => void;

function once(cb: TransportCallbacks): { fail: (reason: string) => void; stop: () => void; stopped: () => boolean } {
  let over = false;
  return {
    fail: (reason) => {
      if (over) return;
      over = true;
      cb.fail(reason);
    },
    stop: () => { over = true; },
    stopped: () => over,
  };
}

async function detail(resp: Response): Promise<string> {
  try {
    const body = await resp.json() as { detail?: unknown };
    if (typeof body.detail === 'string' && body.detail) return body.detail;
  } catch { /* not JSON */ }
  return `HTTP ${resp.status}`;
}

/** Wait for ICE gathering (bounded): the offer then carries the browser's own addresses. */
function gathered(pc: RTCPeerConnection, ms: number): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { pc.removeEventListener('icegatheringstatechange', check); clearTimeout(timer); resolve(); };
    const check = () => { if (pc.iceGatheringState === 'complete') done(); };
    const timer = setTimeout(done, ms);
    pc.addEventListener('icegatheringstatechange', check);
  });
}

/**
 * WebRTC: the offer goes to the gateway (`url`), the answer comes back, and the
 * media flows straight from go2rtc. Receive only; the page never asks for a microphone.
 */
export function startWebrtc(video: HTMLVideoElement, url: string, cb: TransportCallbacks): StopFn {
  const state = once(cb);
  const controller = new AbortController();
  const pc = new RTCPeerConnection({ iceServers: [] });
  const stream = new MediaStream();
  let connectTimer: ReturnType<typeof setTimeout> | null = null;

  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });
  pc.addEventListener('track', (ev) => {
    stream.addTrack(ev.track);
    if (video.srcObject !== stream) video.srcObject = stream;
  });
  pc.addEventListener('connectionstatechange', () => {
    if (pc.connectionState === 'connected' && connectTimer) {
      clearTimeout(connectTimer);
      connectTimer = null;
    } else if (pc.connectionState === 'failed') {
      state.fail('WebRTC could not reach the camera server');
    }
  });

  void (async () => {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await gathered(pc, 1500);
    if (state.stopped()) return;
    // The answer waits for the camera to start streaming (a battery camera wakes first).
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'offer', sdp: pc.localDescription?.sdp ?? offer.sdp }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      state.fail(await detail(resp));
      return;
    }
    const answer = await resp.json() as RTCSessionDescriptionInit;
    if (state.stopped()) return;
    await pc.setRemoteDescription(answer);
    // Away from home, go2rtc's port is not reachable and ICE never connects: move on to MSE.
    connectTimer = setTimeout(() => {
      if (pc.connectionState !== 'connected') state.fail('WebRTC did not connect');
    }, WEBRTC_CONNECT_MS);
  })().catch((err: unknown) => {
    if (!state.stopped()) state.fail(err instanceof Error ? err.message : 'WebRTC failed');
  });

  return () => {
    state.stop();
    controller.abort();
    if (connectTimer) clearTimeout(connectTimer);
    pc.close(); // closing is what tells go2rtc the viewer left, so the camera can sleep
    stream.getTracks().forEach((t) => t.stop());
    if (video.srcObject === stream) video.srcObject = null;
  };
}

/** MSE: go2rtc's fragmented MP4 over the gateway's WebSocket. */
export function startMse(video: HTMLVideoElement, url: string, cb: TransportCallbacks): StopFn {
  const state = once(cb);
  const MS = mediaSourceCtor();
  if (!MS) {
    cb.fail('This browser cannot play MSE video');
    return () => {};
  }
  const managed = 'ManagedMediaSource' in window;
  const ms = new MS();
  let objectUrl: string | null = null;
  if (managed) {
    // Safari's ManagedMediaSource needs this to stream at all.
    (video as HTMLVideoElement & { disableRemotePlayback: boolean }).disableRemotePlayback = true;
    video.srcObject = ms as unknown as MediaProvider;
  } else {
    objectUrl = URL.createObjectURL(ms);
    video.src = objectUrl;
  }

  const ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';
  let sb: SourceBuffer | null = null;
  const pending = new Uint8Array(2 * 1024 * 1024);
  let pendingLen = 0;
  let sourceOpen = false;

  const ask = () => {
    if (sourceOpen && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'mse', value: supportedCodecs((t) => MS.isTypeSupported(t)) }));
    }
  };
  ms.addEventListener('sourceopen', () => {
    sourceOpen = true;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    ask();
  }, { once: true });
  ws.addEventListener('open', ask);

  const trim = () => {
    if (!sb || sb.updating || !sb.buffered.length) return;
    const end = sb.buffered.end(sb.buffered.length - 1);
    const start = end - 5;
    const start0 = sb.buffered.start(0);
    if (start > start0) {
      sb.remove(start0, start);
      try { ms.setLiveSeekableRange(start, end); } catch { /* not in every browser */ }
    }
    if (video.currentTime < start) video.currentTime = start;
    // Catch up gently instead of drifting behind live.
    const gap = end - video.currentTime;
    video.playbackRate = gap > 0.1 ? gap : 0.1;
  };

  ws.addEventListener('message', (ev: MessageEvent) => {
    if (typeof ev.data === 'string') {
      let msg: { type?: string; value?: string };
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === 'mse' && msg.value && !sb) {
        try {
          sb = ms.addSourceBuffer(msg.value);
        } catch {
          state.fail('This browser cannot play the camera’s video format');
          return;
        }
        sb.mode = 'segments';
        sb.addEventListener('updateend', () => {
          if (sb && !sb.updating && pendingLen > 0) {
            try {
              sb.appendBuffer(pending.slice(0, pendingLen));
              pendingLen = 0;
            } catch { /* the next segment retries */ }
          }
          trim();
        });
      } else if (msg.type === 'error') {
        state.fail(msg.value || 'The camera server refused the stream');
      }
      return;
    }
    if (!sb) return;
    const data = new Uint8Array(ev.data as ArrayBuffer);
    if (sb.updating || pendingLen > 0) {
      if (pendingLen + data.byteLength <= pending.byteLength) {
        pending.set(data, pendingLen);
        pendingLen += data.byteLength;
      }
    } else {
      try { sb.appendBuffer(data); } catch { /* a full buffer drops one segment */ }
    }
  });
  ws.addEventListener('close', (ev: CloseEvent) => {
    state.fail(ev.reason || 'The video connection closed');
  });

  return () => {
    state.stop();
    ws.close();
    if (managed) video.srcObject = null;
    else video.removeAttribute('src');
    video.load();
  };
}

/** One long MP4 for the `<video>` itself: the simplest player, for browsers without MSE. */
export function startMp4(video: HTMLVideoElement, url: string, cb: TransportCallbacks): StopFn {
  const state = once(cb);
  const onError = () => state.fail('The video stream would not play');
  video.addEventListener('error', onError);
  video.src = url;
  return () => {
    state.stop();
    video.removeEventListener('error', onError);
    video.removeAttribute('src');
    video.load(); // drops the connection, which is what stops the camera
  };
}
