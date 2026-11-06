'use strict';

/**
 * Browser client for the WebRTC LAN chat.
 *
 * Text chat goes through the signaling server; audio/video flow directly
 * between browsers over WebRTC (mesh topology, one RTCPeerConnection per peer).
 */

const $ = (selector) => document.querySelector(selector);

const joinView = $('#join-view');
const roomView = $('#room-view');
const joinForm = $('#join-form');
const nameInput = $('#name-input');
const roomInput = $('#room-input');
const joinBtn = $('#join-btn');
const joinError = $('#join-error');
const roomNameEl = $('#room-name');
const peerCountEl = $('#peer-count');
const connDot = $('#conn-dot');
const videosEl = $('#videos');
const messagesEl = $('#messages');
const chatForm = $('#chat-form');
const chatInput = $('#chat-input');
const chatStatus = $('#chat-status');
const micBtn = $('#mic-btn');
const camBtn = $('#cam-btn');
const retryMediaBtn = $('#retry-media');
const leaveBtn = $('#leave-btn');
const copyLinkBtn = $('#copy-link');
const bannerEl = $('#banner');
const toastEl = $('#toast');

const state = {
  ws: null,
  selfId: null,
  room: null,
  myName: null,
  joined: false,
  hasJoinedOnce: false,
  leaving: false,
  peers: new Map(),
  localStream: null,
  localTile: null,
  localVideo: null,
  iceServers: [],
  iceLoaded: false,
  forceRelay: false,
  reconnectTimer: null,
};

let toastTimer = null;

/* ------------------------------------------------------------------ */
/* Small UI helpers                                                    */
/* ------------------------------------------------------------------ */

function initialOf(name) {
  return Array.from(String(name || '?'))[0] || '?';
}

function formatTime(ts) {
  const date = new Date(ts || Date.now());
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function showToast(message) {
  toastEl.textContent = message;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastEl.hidden = true;
  }, 2200);
}

function showBanner(message) {
  bannerEl.textContent = message;
  bannerEl.hidden = false;
}

function hideBanner() {
  bannerEl.hidden = true;
  bannerEl.textContent = '';
}

function showJoinError(message) {
  joinError.textContent = message;
  joinError.hidden = false;
}

function setJoinBusy(busy) {
  joinBtn.disabled = busy;
  joinBtn.querySelector('.spinner').hidden = !busy;
  joinBtn.querySelector('.btn-label').textContent = busy ? '正在加入…' : '进入房间';
}

function setConnectionOnline(online) {
  connDot.classList.toggle('online', online);
  connDot.classList.toggle('offline', !online && state.joined);
  connDot.title = online ? '已连接信令服务器' : '未连接信令服务器';
}

function updateCount() {
  const people = 1 + state.peers.size;
  peerCountEl.textContent = `${people} 人`;
}

function addSystem(text) {
  const el = document.createElement('div');
  el.className = 'msg system';
  el.textContent = text;
  messagesEl.append(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendMessage({ name, text, ts, self }) {
  const el = document.createElement('div');
  el.className = 'msg' + (self ? ' self' : '');

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = `${name} · ${formatTime(ts)}`;

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;

  el.append(meta, bubble);
  el.dataset.from = name;
  messagesEl.append(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function createTile({ id, name, local }) {
  const tile = document.createElement('div');
  tile.className = 'tile' + (local ? ' local' : '');
  tile.dataset.peerId = id;
  tile.dataset.connection = local ? 'connected' : 'new';

  const video = document.createElement('video');
  video.autoplay = true;
  video.playsInline = true;
  video.muted = Boolean(local);
  if (local) video.setAttribute('aria-label', '本地画面');

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = initialOf(name);

  const waiting = document.createElement('div');
  waiting.className = 'waiting';
  waiting.textContent = local ? '' : '等待对方开启音视频…';

  const nameEl = document.createElement('div');
  nameEl.className = 'name';
  nameEl.textContent = name;

  const stateEl = document.createElement('span');
  stateEl.className = 'state';
  stateEl.textContent = local ? '本地' : '连接中';

  tile.append(video, avatar, waiting, nameEl, stateEl);

  tile.addEventListener('click', () => {
    video.play().catch(() => {});
    tile.classList.remove('needs-play');
  });

  return tile;
}

function ensureLocalTile() {
  if (state.localTile) return;
  const name = `我（${state.myName || '我'}）`;
  const tile = createTile({ id: 'local', name, local: true });
  videosEl.prepend(tile);
  state.localTile = tile;
  state.localVideo = tile.querySelector('video');

  if (state.localStream) {
    state.localVideo.srcObject = state.localStream;
    state.localVideo.play().catch(() => {});
    tile.classList.add('has-video');
  }
}

/* ------------------------------------------------------------------ */
/* Media                                                               */
/* ------------------------------------------------------------------ */

function applyLocalTracks(pc) {
  if (!state.localStream) return;
  const senders = pc.getSenders();
  for (const track of state.localStream.getTracks()) {
    if (senders.some((sender) => sender.track === track)) continue;
    pc.addTrack(track, state.localStream);
  }
}

function syncMediaButtons() {
  const hasMedia = Boolean(state.localStream);
  micBtn.disabled = !hasMedia;
  camBtn.disabled = !hasMedia;

  const audioOn = hasMedia
    ? state.localStream.getAudioTracks().some((track) => track.enabled)
    : false;
  const videoOn = hasMedia
    ? state.localStream.getVideoTracks().some((track) => track.enabled)
    : false;

  micBtn.classList.toggle('off', !audioOn);
  camBtn.classList.toggle('off', !videoOn);
  micBtn.setAttribute('aria-pressed', String(audioOn));
  camBtn.setAttribute('aria-pressed', String(videoOn));
}

function releaseLocalMedia() {
  if (state.localStream) {
    for (const track of state.localStream.getTracks()) track.stop();
    state.localStream = null;
  }
  if (state.localVideo) {
    state.localVideo.srcObject = null;
    state.localTile.classList.remove('has-video');
  }
  retryMediaBtn.hidden = true;
  hideBanner();
  syncMediaButtons();
}

function mediaErrorMessage(err) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return '当前页面不是 HTTPS 安全上下文，浏览器禁止访问摄像头/麦克风。请通过 https:// 或 localhost 打开本页。';
  }
  switch (err && err.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return '摄像头/麦克风权限被拒绝，当前为文字模式。可点击“重试音视频”重新授权。';
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return '没有找到可用的摄像头或麦克风，当前为文字模式。';
    case 'NotReadableError':
    case 'TrackStartError':
      return '摄像头或麦克风被其他程序占用，当前为文字模式。关闭占用程序后可重试。';
    case 'OverconstrainedError':
      return '摄像头不支持所需分辨率，当前为文字模式。';
    default:
      return `无法打开摄像头/麦克风（${(err && err.message) || '未知错误'}），当前为文字模式。`;
  }
}

async function ensureLocalMedia() {
  if (state.localStream) return true;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showBanner(mediaErrorMessage());
    retryMediaBtn.hidden = false;
    syncMediaButtons();
    return false;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { max: 30 } },
    });
    state.localStream = stream;

    if (state.localVideo) {
      state.localVideo.srcObject = stream;
      state.localVideo.play().catch(() => {});
      state.localTile.classList.add('has-video');
    }

    for (const peer of state.peers.values()) {
      applyLocalTracks(peer.pc);
    }

    hideBanner();
    retryMediaBtn.hidden = true;
    syncMediaButtons();
    return true;
  } catch (err) {
    showBanner(mediaErrorMessage(err));
    retryMediaBtn.hidden = false;
    syncMediaButtons();
    return false;
  }
}

function toggleMic() {
  if (!state.localStream) return;
  const tracks = state.localStream.getAudioTracks();
  if (tracks.length === 0) return;
  const nextEnabled = !tracks.every((track) => track.enabled);
  tracks.forEach((track) => {
    track.enabled = nextEnabled;
  });
  syncMediaButtons();
}

function toggleCamera() {
  if (!state.localStream) return;
  const tracks = state.localStream.getVideoTracks();
  if (tracks.length === 0) return;
  const nextEnabled = !tracks.every((track) => track.enabled);
  tracks.forEach((track) => {
    track.enabled = nextEnabled;
  });
  syncMediaButtons();
}

/* ------------------------------------------------------------------ */
/* WebRTC mesh                                                         */
/* ------------------------------------------------------------------ */

function setPeerState(peer, connectionState) {
  const labels = {
    new: '等待连接',
    connecting: '连接中',
    connected: '已连接',
    disconnected: '已断开',
    failed: '连接失败',
    closed: '已关闭',
  };
  peer.tile.dataset.connection = connectionState;
  peer.stateEl.textContent = labels[connectionState] || connectionState;
  if (connectionState === 'failed') {
    peer.tile.title = '音视频连接失败，请检查网络或 TURN 服务';
  }
}

function sendSignal(to, data) {
  send({ type: 'signal', to, data });
}

function createPeer({ id, name }) {
  if (state.peers.has(id)) return state.peers.get(id);

  const pc = new RTCPeerConnection({
    iceServers: state.iceServers,
    iceTransportPolicy: state.forceRelay ? 'relay' : 'all',
  });
  const tile = createTile({ id, name, local: false });
  videosEl.append(tile);

  const peer = {
    id,
    name,
    pc,
    tile,
    video: tile.querySelector('video'),
    stateEl: tile.querySelector('.state'),
    polite: state.selfId < id,
    makingOffer: false,
    ignoreOffer: false,
    pendingCandidates: [],
  };
  state.peers.set(id, peer);
  updateCount();

  pc.onnegotiationneeded = async () => {
    try {
      peer.makingOffer = true;
      await pc.setLocalDescription();
      sendSignal(peer.id, { description: pc.localDescription });
    } catch (err) {
      console.warn('[webrtc] negotiation failed', err);
    } finally {
      peer.makingOffer = false;
    }
  };

  pc.onicecandidate = ({ candidate }) => {
    if (candidate) sendSignal(peer.id, { candidate });
  };

  pc.ontrack = ({ streams }) => {
    const stream = streams && streams[0];
    if (!stream) return;
    peer.video.srcObject = stream;
    peer.tile.classList.add('has-video');
    peer.video.play().catch(() => {
      peer.tile.classList.add('needs-play');
    });
  };

  pc.onconnectionstatechange = () => {
    setPeerState(peer, pc.connectionState);
    if (pc.connectionState === 'connected') {
      peer.tile.classList.remove('needs-play');
      peer.video.play().catch(() => {});
    }
  };

  pc.oniceconnectionstatechange = () => {
    if (pc.iceConnectionState === 'failed') setPeerState(peer, 'failed');
  };

  applyLocalTracks(pc);
  return peer;
}

function closePeer(peer) {
  try {
    peer.pc.onnegotiationneeded = null;
    peer.pc.onicecandidate = null;
    peer.pc.ontrack = null;
    peer.pc.onconnectionstatechange = null;
    peer.pc.oniceconnectionstatechange = null;
    peer.pc.close();
  } catch {
    // ignore
  }
  peer.tile.remove();
  state.peers.delete(peer.id);
}

function closeAllPeers() {
  for (const peer of [...state.peers.values()]) closePeer(peer);
  updateCount();
}

async function handleSignal(from, data) {
  const peer = state.peers.get(from);
  if (!peer) return;
  const pc = peer.pc;

  try {
    if (data.description) {
      const description = data.description;
      const offerCollision =
        description.type === 'offer' && (peer.makingOffer || pc.signalingState !== 'stable');

      peer.ignoreOffer = !peer.polite && offerCollision;
      if (peer.ignoreOffer) return;

      if (offerCollision) {
        await Promise.all([
          pc.setLocalDescription({ type: 'rollback' }),
          pc.setRemoteDescription(description),
        ]);
      } else {
        await pc.setRemoteDescription(description);
      }

      if (description.type === 'offer') {
        await pc.setLocalDescription();
        sendSignal(from, { description: pc.localDescription });
      }

      for (const candidate of peer.pendingCandidates.splice(0)) {
        try {
          await pc.addIceCandidate(candidate);
        } catch {
          // stale candidate
        }
      }
    } else if (data.candidate) {
      if (pc.remoteDescription) {
        try {
          await pc.addIceCandidate(data.candidate);
        } catch (err) {
          if (!peer.ignoreOffer) console.warn('[webrtc] ICE candidate failed', err);
        }
      } else {
        peer.pendingCandidates.push(data.candidate);
      }
    }
  } catch (err) {
    console.warn('[webrtc] signal handling failed', err);
  }
}

/* ------------------------------------------------------------------ */
/* Signaling socket                                                    */
/* ------------------------------------------------------------------ */

function send(payload) {
  const ws = state.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(payload));
  return true;
}

function loadIceServers() {
  if (state.iceLoaded) return Promise.resolve();
  state.iceLoaded = true;
  return fetch('/api/ice', { cache: 'no-store' })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (data && Array.isArray(data.iceServers)) state.iceServers = data.iceServers;
    })
    .catch(() => {
      // Host candidates are enough for same-LAN peers.
    });
}

function openSocket(room) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${location.host}/ws`);
  state.ws = ws;

  ws.onopen = () => {
    setConnectionOnline(true);
    ws.send(JSON.stringify({ type: 'join', room, name: state.myName }));
    chatStatus.textContent = '房间消息';
  };

  ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    handleServerMessage(msg);
  };

  ws.onclose = () => {
    if (state.ws !== ws) return;
    state.ws = null;
    handleSocketClosed();
  };

  ws.onerror = () => {
    // onclose handles recovery
  };
}

function handleServerMessage(msg) {
  switch (msg.type) {
    case 'welcome': {
      state.selfId = msg.self.id;
      state.room = msg.room;
      state.joined = true;
      state.hasJoinedOnce = true;
      setJoinBusy(false);
      joinError.hidden = true;
      joinView.hidden = true;
      roomView.hidden = false;
      roomNameEl.textContent = msg.room;
      hideBanner();
      ensureLocalTile();
      addSystem(`已加入房间「${msg.room}」`);
      for (const peer of msg.peers || []) createPeer(peer);
      updateCount();
      setConnectionOnline(true);
      break;
    }

    case 'peer-joined': {
      if (!state.joined) break;
      createPeer(msg.peer);
      addSystem(`${msg.peer.name} 加入了房间`);
      break;
    }

    case 'peer-left': {
      const peer = state.peers.get(msg.peerId);
      if (!peer) break;
      const name = peer.name;
      closePeer(peer);
      addSystem(`${name} 离开了房间`);
      updateCount();
      break;
    }

    case 'signal':
      handleSignal(msg.from, msg.data);
      break;

    case 'chat':
      appendMessage({
        name: msg.from.name,
        text: msg.text,
        ts: msg.ts,
        self: msg.from.id === state.selfId,
      });
      break;

    case 'error': {
      const message = msg.message || '发生未知错误';
      if (!state.joined) {
        const ws = state.ws;
        state.ws = null;
        try {
          if (ws) ws.close();
        } catch {
          // ignore
        }
        setJoinBusy(false);
        showJoinError(message);
      } else {
        addSystem(`服务器：${message}`);
        showToast(message);
      }
      break;
    }

    default:
      break;
  }
}

function handleSocketClosed() {
  if (state.leaving) return;
  setConnectionOnline(false);
  const wasJoined = state.joined;
  state.joined = false;
  state.selfId = null;
  closeAllPeers();

  if (!wasJoined && !state.hasJoinedOnce) {
    // The very first connection failed: let the user retry from the join form.
    setJoinBusy(false);
    releaseLocalMedia();
    showJoinError('无法连接信令服务器，请确认服务已启动、地址和网络正常后重试。');
    return;
  }

  setJoinBusy(false);
  chatStatus.textContent = '正在重连…';
  showBanner('与信令服务器的连接已断开，正在尝试重连…');
  if (wasJoined) addSystem('连接已断开，正在重连…');

  if (state.reconnectTimer) return;
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    if (state.leaving) return;
    openSocket(state.room);
  }, 2000);
}

/* ------------------------------------------------------------------ */
/* Join / leave                                                        */
/* ------------------------------------------------------------------ */

async function onJoinSubmit(event) {
  event.preventDefault();
  if (joinBtn.disabled || state.ws || state.joined) return;
  const name = nameInput.value.trim();
  const room = roomInput.value.trim();
  if (!name || !room) return;

  state.myName = name;
  state.room = room;
  setJoinBusy(true);
  joinError.hidden = true;

  await loadIceServers();
  await ensureLocalMedia();

  openSocket(room);
}

function leaveRoom() {
  state.leaving = true;
  clearTimeout(state.reconnectTimer);
  state.reconnectTimer = null;

  if (state.ws) {
    send({ type: 'leave' });
    const ws = state.ws;
    state.ws = null;
    try {
      ws.close();
    } catch {
      // ignore
    }
  }

  closeAllPeers();
  releaseLocalMedia();
  state.localVideo = null;
  state.localTile = null;

  state.joined = false;
  state.hasJoinedOnce = false;
  state.selfId = null;
  state.room = null;
  state.leaving = false;

  videosEl.innerHTML = '';
  messagesEl.innerHTML = '';
  roomView.hidden = true;
  joinView.hidden = false;
  retryMediaBtn.hidden = true;
  hideBanner();
  setConnectionOnline(false);
  setJoinBusy(false);
  syncMediaButtons();
  updateCount();
}

/* ------------------------------------------------------------------ */
/* Wiring                                                              */
/* ------------------------------------------------------------------ */

joinForm.addEventListener('submit', onJoinSubmit);
leaveBtn.addEventListener('click', leaveRoom);

micBtn.addEventListener('click', toggleMic);
camBtn.addEventListener('click', toggleCamera);

retryMediaBtn.addEventListener('click', async () => {
  retryMediaBtn.disabled = true;
  const ok = await ensureLocalMedia();
  retryMediaBtn.disabled = false;
  if (ok) showToast('音视频已开启');
});

chatForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  if (!send({ type: 'chat', text })) {
    showToast('连接已断开，消息未发送');
    return;
  }
  chatInput.value = '';
});

copyLinkBtn.addEventListener('click', async () => {
  const url = `${location.origin}${location.pathname}?room=${encodeURIComponent(state.room || '')}`;
  try {
    await navigator.clipboard.writeText(url);
    showToast('邀请链接已复制');
  } catch {
    window.prompt('复制邀请链接：', url);
  }
});

window.addEventListener('beforeunload', () => {
  if (state.ws) {
    try {
      state.ws.close();
    } catch {
      // ignore
    }
  }
});

// Debug hook used by the Playwright end-to-end test.
window.__webrtcDebug = {
  state() {
    return {
      joined: state.joined,
      selfId: state.selfId,
      room: state.room,
      peers: [...state.peers.values()].map((peer) => ({
        id: peer.id,
        name: peer.name,
        connectionState: peer.pc.connectionState,
        iceConnectionState: peer.pc.iceConnectionState,
        signalingState: peer.pc.signalingState,
        iceGatheringState: peer.pc.iceGatheringState,
        localDescription: peer.pc.localDescription ? peer.pc.localDescription.type : null,
        remoteDescription: peer.pc.remoteDescription ? peer.pc.remoteDescription.type : null,
        senders: peer.pc.getSenders().length,
        receivers: peer.pc.getReceivers().length,
        remote: peer.video.srcObject
          ? {
              videoWidth: peer.video.videoWidth,
              videoHeight: peer.video.videoHeight,
              videoTracks: peer.video.srcObject.getVideoTracks().length,
              audioTracks: peer.video.srcObject.getAudioTracks().length,
            }
          : null,
      })),
      local: state.localStream
        ? {
            audioTracks: state.localStream.getAudioTracks().length,
            videoTracks: state.localStream.getVideoTracks().length,
          }
        : null,
    };
  },

  async selectedCandidatePairs() {
    const result = [];
    for (const peer of state.peers.values()) {
      const stats = await peer.pc.getStats();
      let pair = null;
      stats.forEach((report) => {
        if (report.type === 'candidate-pair' && (report.selected || report.state === 'succeeded')) {
          pair = report;
        }
      });
      if (!pair) {
        result.push({ peerId: peer.id, pair: null });
        continue;
      }
      const localCandidate = stats.get(pair.localCandidateId);
      const remoteCandidate = stats.get(pair.remoteCandidateId);
      result.push({
        peerId: peer.id,
        localType: localCandidate && localCandidate.candidateType,
        remoteType: remoteCandidate && remoteCandidate.candidateType,
        protocol: localCandidate && localCandidate.protocol,
        relayProtocol: localCandidate && localCandidate.relayProtocol,
      });
    }
    return result;
  },
};

/* ------------------------------------------------------------------ */
/* Boot                                                                */
/* ------------------------------------------------------------------ */

(function boot() {
  const params = new URLSearchParams(location.search);
  const presetRoom = params.get('room');
  if (presetRoom) roomInput.value = presetRoom;
  nameInput.value = params.get('name') || `用户${Math.floor(1000 + Math.random() * 9000)}`;
  state.forceRelay = params.get('ice') === 'relay';
  syncMediaButtons();
  loadIceServers();
})();
