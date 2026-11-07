'use strict';

/**
 * Browser client: account registration, confirmed contacts and WebRTC calls.
 *
 * Text chat and call signaling use the WebSocket server; audio/video flows
 * directly between browsers over WebRTC (mesh, one RTCPeerConnection per peer).
 */

const $ = (selector) => document.querySelector(selector);
const TOKEN_KEY = 'webrtc-chat.token';
const PAGE_ROLE = (() => {
  const path = location.pathname.replace(/\/+$/, '');
  if (path === '/phone') return 'phone';
  if (path === '/device') return 'device';
  return null;
})();

const els = {
  authView: $('#auth-view'),
  appView: $('#app-view'),
  authTabs: $('#auth-tabs'),
  pageRoleTitle: $('#page-role-title'),
  pageSwitch: $('#page-switch'),
  tabPhone: $('#tab-phone'),
  tabDevice: $('#tab-device'),
  phonePanel: $('#phone-panel'),
  devicePanel: $('#device-panel'),
  phoneModeLogin: $('#phone-mode-login'),
  phoneModeRegister: $('#phone-mode-register'),
  phoneLoginMode: $('#phone-login-mode'),
  phoneLoginModePassword: $('#phone-login-mode-password'),
  phoneLoginModeSms: $('#phone-login-mode-sms'),
  phoneSmsLoginForm: $('#phone-sms-login-form'),
  phoneSmsLoginPhone: $('#phone-sms-login-phone'),
  phoneSmsLoginCode: $('#phone-sms-login-code'),
  phoneSmsLoginSend: $('#phone-sms-login-send'),
  phoneRegisterCodeField: $('#phone-register-code-field'),
  phoneRegisterCode: $('#phone-register-code'),
  phoneRegisterSend: $('#phone-register-send'),
  phoneLoginForm: $('#phone-login-form'),
  phoneLoginPhone: $('#phone-login-phone'),
  phoneLoginPassword: $('#phone-login-password'),
  phoneRegisterForm: $('#phone-register-form'),
  phoneRegisterPhone: $('#phone-register-phone'),
  phoneRegisterName: $('#phone-register-name'),
  phoneRegisterPassword: $('#phone-register-password'),
  deviceModeLogin: $('#device-mode-login'),
  deviceModeRegister: $('#device-mode-register'),
  deviceLoginForm: $('#device-login-form'),
  deviceLoginNumber: $('#device-login-number'),
  deviceLoginPassword: $('#device-login-password'),
  deviceRegisterForm: $('#device-register-form'),
  deviceRegisterName: $('#device-register-name'),
  deviceRegisterPassword: $('#device-register-password'),
  deviceSuccess: $('#device-register-success'),
  deviceNumberDisplay: $('#device-number-display'),
  deviceEnterApp: $('#device-enter-app'),
  authError: $('#auth-error'),

  meName: $('#me-name'),
  meRole: $('#me-role'),
  wsStatus: $('#ws-status'),
  settingsBtn: $('#settings-btn'),
  settingsPanel: $('#settings-panel'),
  settingsClose: $('#settings-close'),
  settingsAvatar: $('#settings-avatar'),
  settingsName: $('#settings-name'),
  settingsRole: $('#settings-role'),
  settingsNumber: $('#settings-number'),
  settingsCopy: $('#settings-copy'),
  settingsRefresh: $('#settings-refresh'),
  settingsMedia: $('#settings-media'),
  settingsLogout: $('#settings-logout'),
  settingsHint: $('#settings-hint'),
  addContactHint: $('#add-contact-hint'),
  addContactForm: $('#add-contact-form'),
  addContactInput: $('#add-contact-input'),
  incomingCount: $('#incoming-count'),
  incomingList: $('#incoming-list'),
  outgoingCount: $('#outgoing-count'),
  outgoingList: $('#outgoing-list'),
  contactsCount: $('#contacts-count'),
  contactsList: $('#contacts-list'),
  contactsEmpty: $('#contacts-empty'),

  callOverlay: $('#call-overlay'),
  incomingPanel: $('#incoming-panel'),
  roomView: $('#room-view'),
  connDot: $('#conn-dot'),
  roomName: $('#room-name'),
  peerCount: $('#peer-count'),
  callTimer: $('#call-timer'),
  chatToggle: $('#chat-toggle'),
  chatPanel: $('#chat-panel'),
  chatClose: $('#chat-close'),
  leaveBtn: $('#leave-btn'),
  banner: $('#banner'),
  videos: $('#videos'),
  micBtn: $('#mic-btn'),
  camBtn: $('#cam-btn'),
  messages: $('#messages'),
  chatForm: $('#chat-form'),
  chatInput: $('#chat-input'),

  incomingCallAvatar: $('#incoming-call-avatar'),
  incomingCallName: $('#incoming-call-name'),
  rejectCall: $('#reject-call'),
  acceptCall: $('#accept-call'),
  toast: $('#toast'),
};

const state = {
  token: localStorage.getItem(TOKEN_KEY) || '',
  user: null,
  pendingAuth: null,
  ws: null,
  wsAuthed: false,
  reconnectTimer: null,
  contacts: { contacts: [], incoming: [], outgoing: [] },
  contactsLoading: null,
  room: null,
  callPeer: null,
  callAnswered: false,
  callTimeout: null,
  callStartedAt: 0,
  callTimerInterval: null,
  incomingInvite: null,
  incomingTimer: null,
  peers: new Map(),
  selfId: null,
  localStream: null,
  localTile: null,
  localVideo: null,
  smsEnabled: false,
  smsDevShowCode: false,
  smsLoginMode: 'password',
  iceServers: [],
  mediaError: '',
  iceLoaded: false,
  forceRelay: false,
  leaving: false,
  hasJoinedOnce: false,
  toastTimer: null,
};

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

function initialOf(name) {
  return Array.from(String(name || '?'))[0] || '?';
}

function formatTime(ts) {
  return new Date(ts || Date.now()).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  });
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, 2600);
}

function setAuthError(message) {
  if (!message) {
    els.authError.hidden = true;
    els.authError.textContent = '';
    return;
  }
  els.authError.textContent = message;
  els.authError.hidden = false;
}

function setFormBusy(form, busy) {
  const button = form.querySelector('button[type="submit"]');
  if (button) button.disabled = busy;
}

function makeRoomId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return `call-${globalThis.crypto.randomUUID()}`;
  }
  return `call-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function myNumber() {
  return state.user ? state.user.phone || state.user.deviceNumber : '';
}

async function api(path, options = {}) {
  const { method = 'GET', body, auth = true } = options;
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth && state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const error = new Error((data && data.error && data.error.message) || `请求失败（${response.status}）`);
    error.status = response.status;
    error.code = data && data.error ? data.error.code : 'request-failed';
    throw error;
  }
  return data;
}

/* ------------------------------------------------------------------ */
/* Auth                                                                */
/* ------------------------------------------------------------------ */

function applyPageRole() {
  if (!PAGE_ROLE) return;
  els.authTabs.hidden = true;
  els.pageRoleTitle.hidden = false;
  els.pageRoleTitle.textContent = PAGE_ROLE === 'phone' ? '手机用户' : '设备用户';
  els.pageSwitch.hidden = false;
  const link = document.createElement('a');
  link.href = PAGE_ROLE === 'phone' ? '/device' : '/phone';
  link.textContent =
    PAGE_ROLE === 'phone' ? '我是设备用户，前往设备页面 →' : '我是手机用户，前往手机页面 →';
  els.pageSwitch.replaceChildren(link);
  setAuthRole(PAGE_ROLE);
}

function setAuthRole(role) {
  const isPhone = role === 'phone';
  els.tabPhone.classList.toggle('active', isPhone);
  els.tabDevice.classList.toggle('active', !isPhone);
  els.tabPhone.setAttribute('aria-selected', String(isPhone));
  els.tabDevice.setAttribute('aria-selected', String(!isPhone));
  els.phonePanel.hidden = !isPhone;
  els.devicePanel.hidden = isPhone;
  setAuthError('');
}

function updatePhoneLoginUI() {
  const smsMode = state.smsEnabled && state.smsLoginMode === 'sms';
  els.phoneLoginMode.hidden = !state.smsEnabled;
  els.phoneLoginForm.hidden = smsMode;
  els.phoneSmsLoginForm.hidden = !smsMode;
  els.phoneLoginModePassword.classList.toggle('active', !smsMode);
  els.phoneLoginModeSms.classList.toggle('active', smsMode);
}

function setAuthMode(role, mode) {
  const isLogin = mode === 'login';
  const loginBtn = role === 'phone' ? els.phoneModeLogin : els.deviceModeLogin;
  const registerBtn = role === 'phone' ? els.phoneModeRegister : els.deviceModeRegister;
  const loginForm = role === 'phone' ? els.phoneLoginForm : els.deviceLoginForm;
  const registerForm = role === 'phone' ? els.phoneRegisterForm : els.deviceRegisterForm;
  loginBtn.classList.toggle('active', isLogin);
  registerBtn.classList.toggle('active', !isLogin);
  if (role === 'phone') {
    if (isLogin) {
      updatePhoneLoginUI();
      els.phoneRegisterForm.hidden = true;
    } else {
      els.phoneLoginForm.hidden = true;
      els.phoneSmsLoginForm.hidden = true;
      els.phoneLoginMode.hidden = true;
      els.phoneRegisterForm.hidden = false;
    }
  } else {
    loginForm.hidden = !isLogin;
    registerForm.hidden = isLogin;
    els.deviceSuccess.hidden = true;
  }
  setAuthError('');
}

function applySmsConfig(config) {
  state.smsEnabled = Boolean(config && config.smsEnabled);
  state.smsDevShowCode = Boolean(config && config.smsDevShowCode);
  els.phoneRegisterCodeField.hidden = !state.smsEnabled;
  els.phoneRegisterCode.required = state.smsEnabled;
  if (!state.smsEnabled) state.smsLoginMode = 'password';
  updatePhoneLoginUI();
}

async function loadConfig() {
  try {
    const config = await api('/api/config', { auth: false });
    applySmsConfig(config);
  } catch {
    applySmsConfig({ smsEnabled: false });
  }
}

function startCodeCountdown(button) {
  let remaining = 60;
  button.disabled = true;
  button.textContent = `${remaining}s`;
  const timer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(timer);
      button.disabled = false;
      button.textContent = '获取验证码';
    } else {
      button.textContent = `${remaining}s`;
    }
  }, 1000);
}

async function requestSmsCode(phone, purpose, button) {
  if (!/^1[3-9]\d{9}$/.test(phone)) {
    showToast('请输入有效的 11 位手机号');
    return;
  }
  button.disabled = true;
  try {
    const data = await api('/api/auth/sms/send', {
      method: 'POST',
      auth: false,
      body: { phone, purpose },
    });
    startCodeCountdown(button);
    if (data.devCode) {
      showToast(`测试验证码：${data.devCode}`);
    } else {
      showToast('验证码已发送，请查看短信');
    }
  } catch (err) {
    button.disabled = false;
    showToast(err.message || '验证码发送失败');
  }
}

function saveToken(token) {
  state.token = token;
  localStorage.setItem(TOKEN_KEY, token);
}

function clearToken() {
  state.token = '';
  localStorage.removeItem(TOKEN_KEY);
}

function showAuth(message) {
  els.appView.hidden = true;
  els.authView.hidden = false;
  els.callOverlay.hidden = true;
  els.settingsPanel.hidden = true;
  if (message) setAuthError(message);
  setAuthError(message || '');
  setTimeout(() => {
    if (PAGE_ROLE === 'device') {
      els.deviceLoginNumber.focus({ preventScroll: true });
    } else if (!els.phonePanel.hidden) {
      els.phoneLoginPhone.focus({ preventScroll: true });
    }
  }, 50);
}

function updateIdentity() {
  if (!state.user) return;
  els.meName.textContent = state.user.name;
  els.meRole.textContent = state.user.role === 'phone' ? '手机' : '设备';
  els.settingsName.textContent = state.user.name;
  els.settingsRole.textContent =
    state.user.role === 'phone' ? '手机用户' : '设备用户';
  els.settingsNumber.textContent = myNumber();
  els.settingsAvatar.textContent = initialOf(state.user.name);
  if (state.user.role === 'phone') {
    els.addContactHint.textContent = '输入设备用户的 12 位设备号，对方确认后即可通话。';
    els.addContactInput.placeholder = '12 位设备号';
    els.addContactInput.maxLength = 12;
  } else {
    els.addContactHint.textContent = '输入手机用户的 11 位手机号，对方确认后即可通话。';
    els.addContactInput.placeholder = '11 位手机号';
    els.addContactInput.maxLength = 11;
  }
  els.addContactInput.inputMode = 'numeric';
}

async function completeAuth(result) {
  saveToken(result.token);
  state.user = result.user;
  state.pendingAuth = null;
  if (PAGE_ROLE && result.user.role !== PAGE_ROLE) {
    location.replace(`/${result.user.role}`);
    return;
  }
  els.authView.hidden = true;
  els.appView.hidden = false;
  updateIdentity();
  connectSocket();
  refreshContacts();
  ensureLocalMedia();
}

async function handleSessionExpired(message = '登录状态已失效，请重新登录') {
  clearToken();
  state.user = null;
  closeSocket();
  state.contacts = { contacts: [], incoming: [], outgoing: [] };
  renderContacts();
  leaveCall({ silent: true });
  releaseLocalMedia();
  showAuth(message);
}

async function submitAuth(form, fn) {
  setAuthError('');
  setFormBusy(form, true);
  try {
    await fn();
  } catch (err) {
    setAuthError(err.message || '操作失败，请稍后重试');
  } finally {
    setFormBusy(form, false);
  }
}

async function loginWith(identifier, password) {
  const result = await api('/api/auth/login', {
    method: 'POST',
    auth: false,
    body: { identifier, password },
  });
  await completeAuth(result);
}

async function loginWithSms(phone, code) {
  const result = await api('/api/auth/login/sms', {
    method: 'POST',
    auth: false,
    body: { phone, code },
  });
  await completeAuth(result);
}

async function registerPhone() {
  const result = await api('/api/auth/register/phone', {
    method: 'POST',
    auth: false,
    body: {
      phone: els.phoneRegisterPhone.value.trim(),
      name: els.phoneRegisterName.value.trim(),
      password: els.phoneRegisterPassword.value,
      ...(state.smsEnabled ? { code: els.phoneRegisterCode.value.trim() } : {}),
    },
  });
  await completeAuth(result);
}

async function registerDevice() {
  const result = await api('/api/auth/register/device', {
    method: 'POST',
    auth: false,
    body: {
      name: els.deviceRegisterName.value.trim(),
      password: els.deviceRegisterPassword.value,
    },
  });
  saveToken(result.token);
  state.pendingAuth = result;
  els.deviceRegisterForm.hidden = true;
  els.deviceLoginForm.hidden = true;
  els.deviceModeLogin.classList.remove('active');
  els.deviceModeRegister.classList.remove('active');
  els.deviceNumberDisplay.textContent = result.user.deviceNumber;
  els.deviceSuccess.hidden = false;
}

async function logout() {
  try {
    if (state.token) {
      await api('/api/auth/logout', { method: 'POST' });
    }
  } catch {
    // Ignore network errors while logging out.
  }
  clearToken();
  state.user = null;
  closeSocket();
  leaveCall({ silent: true });
  releaseLocalMedia();
  els.settingsPanel.hidden = true;
  els.settingsHint.textContent = '';
  state.contacts = { contacts: [], incoming: [], outgoing: [] };
  renderContacts();
  els.deviceSuccess.hidden = true;
  els.deviceRegisterForm.hidden = true;
  els.deviceLoginForm.hidden = false;
  setAuthMode('device', 'login');
  setAuthMode('phone', 'login');
  showAuth();
}

/* ------------------------------------------------------------------ */
/* Contacts                                                            */
/* ------------------------------------------------------------------ */

function contactNumber(user) {
  return user.role === 'phone' ? user.phone : user.deviceNumber;
}

function contactRoleLabel(user) {
  return user.role === 'phone' ? '手机用户' : '设备';
}

function makeAvatar(user) {
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = initialOf(user.name);
  return avatar;
}

function makeContactInfo(user) {
  const info = document.createElement('div');
  info.className = 'contact-info';

  const name = document.createElement('div');
  name.className = 'contact-name';
  name.textContent = user.name;

  const number = document.createElement('div');
  number.className = 'contact-number';
  number.textContent = `${contactNumber(user) || ''} · ${contactRoleLabel(user)}`;

  info.append(name, number);
  return info;
}

function renderContacts() {
  const { contacts, incoming, outgoing } = state.contacts;
  els.incomingCount.textContent = incoming.length;
  els.outgoingCount.textContent = outgoing.length;
  els.contactsCount.textContent = contacts.length;
  els.contactsEmpty.hidden = incoming.length + outgoing.length + contacts.length > 0;

  els.contactsList.replaceChildren();
  els.incomingList.replaceChildren();
  els.outgoingList.replaceChildren();

  for (const item of contacts) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'contact-card' + (item.user.online ? ' online' : ' offline');
    card.dataset.userId = item.user.id;
    card.title = item.user.online
      ? `点击呼叫 ${item.user.name}`
      : `${item.user.name} 当前离线`;

    const avatar = makeAvatar(item.user);
    avatar.classList.add('big');

    const dot = document.createElement('span');
    dot.className = 'online-dot' + (item.user.online ? ' online' : '');
    dot.title = item.user.online ? '在线' : '离线';

    const name = document.createElement('span');
    name.className = 'contact-name';
    name.textContent = item.user.name;

    const number = document.createElement('span');
    number.className = 'contact-number';
    number.textContent = contactNumber(item.user) || '';

    card.append(avatar, dot, name, number);
    card.addEventListener('click', () => startCall(item.user));
    els.contactsList.append(card);
  }

  for (const item of incoming) {
    const row = document.createElement('div');
    row.className = 'contact-item';
    row.append(makeAvatar(item.user), makeContactInfo(item.user));

    const actions = document.createElement('div');
    actions.className = 'contact-actions';

    const accept = document.createElement('button');
    accept.type = 'button';
    accept.className = 'accept';
    accept.textContent = '确认';
    accept.addEventListener('click', () => respondContact(item.id, 'confirm'));

    const reject = document.createElement('button');
    reject.type = 'button';
    reject.className = 'reject';
    reject.textContent = '拒绝';
    reject.addEventListener('click', () => respondContact(item.id, 'reject'));

    actions.append(accept, reject);
    row.append(actions);
    els.incomingList.append(row);
  }

  for (const item of outgoing) {
    const row = document.createElement('div');
    row.className = 'contact-item';
    row.append(makeAvatar(item.user), makeContactInfo(item.user));

    const actions = document.createElement('div');
    actions.className = 'contact-actions';

    const stateText = document.createElement('span');
    stateText.className = 'contact-state';
    stateText.textContent = '等待确认';

    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'cancel';
    cancel.textContent = '取消';
    cancel.addEventListener('click', () => removeContact(item.id));

    actions.append(stateText, cancel);
    row.append(actions);
    els.outgoingList.append(row);
  }
}

async function refreshContacts() {
  if (!state.user) return;
  if (state.contactsLoading) return state.contactsLoading;
  state.contactsLoading = (async () => {
    try {
      state.contacts = await api('/api/contacts');
      renderContacts();
    } catch (err) {
      if (err.status === 401) {
        await handleSessionExpired();
      }
    } finally {
      state.contactsLoading = null;
    }
  })();
  return state.contactsLoading;
}

function updatePresence(userId, online) {
  for (const list of Object.values(state.contacts)) {
    for (const item of list) {
      if (item.user.id === userId) item.user.online = online;
    }
  }
  renderContacts();
}

async function respondContact(contactId, action) {
  try {
    await api(`/api/contacts/${encodeURIComponent(contactId)}/${action}`, { method: 'POST' });
    showToast(action === 'confirm' ? '已添加联系人' : '已拒绝请求');
    await refreshContacts();
  } catch (err) {
    showToast(err.message || '操作失败');
  }
}

async function removeContact(contactId) {
  try {
    await api(`/api/contacts/${encodeURIComponent(contactId)}`, { method: 'DELETE' });
    showToast('已取消');
    await refreshContacts();
  } catch (err) {
    showToast(err.message || '操作失败');
  }
}

async function addContact(input) {
  const body = state.user.role === 'phone' ? { deviceNumber: input } : { phone: input };
  try {
    const result = await api('/api/contacts/request', { method: 'POST', body });
    showToast(result.autoAccepted ? '对方也已添加你，已成为联系人' : '请求已发送，等待对方确认');
    els.addContactInput.value = '';
    await refreshContacts();
  } catch (err) {
    showToast(err.message || '添加失败');
  }
}

/* ------------------------------------------------------------------ */
/* WebSocket                                                           */
/* ------------------------------------------------------------------ */

function setWsStatus(online) {
  els.wsStatus.classList.toggle('online', online);
  els.wsStatus.classList.toggle('offline', !online && Boolean(state.token));
  els.wsStatus.title = online ? '实时连接正常' : '实时连接断开';
}

function send(payload) {
  const ws = state.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(payload));
  return true;
}

function connectSocket() {
  if (!state.token) return;
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${location.host}/ws`);
  state.ws = ws;
  state.wsAuthed = false;
  setWsStatus(false);

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'auth', token: state.token }));
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
    state.wsAuthed = false;
    state.selfId = null;
    closeAllPeers();
    setWsStatus(false);
    setConnectionOnline(false);
    if (!state.token) return;
    if (state.room) showBanner('与服务器的连接已断开，正在重连…');
    scheduleReconnect();
  };

  ws.onerror = () => {
    // onclose handles recovery
  };
}

function closeSocket() {
  clearTimeout(state.reconnectTimer);
  state.reconnectTimer = null;
  const ws = state.ws;
  state.ws = null;
  state.wsAuthed = false;
  if (ws) {
    try {
      ws.close();
    } catch {
      // ignore
    }
  }
  setWsStatus(false);
}

function scheduleReconnect() {
  if (state.reconnectTimer || !state.token) return;
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    connectSocket();
  }, 2000);
}

function handleServerMessage(msg) {
  switch (msg.type) {
    case 'auth-ok':
      state.wsAuthed = true;
      setWsStatus(true);
      refreshContacts();
      if (state.room) sendJoin();
      break;

    case 'auth-error':
      handleSessionExpired(msg.message || '登录状态已失效，请重新登录');
      break;

    case 'presence':
      updatePresence(msg.userId, Boolean(msg.online));
      break;

    case 'contact-updated':
      refreshContacts();
      break;

    case 'contact-request':
      refreshContacts();
      showToast(`${msg.from.name} 请求添加你为联系人`);
      break;

    case 'call-invite':
      handleIncomingCall(msg);
      break;

    case 'call-response':
      handleCallResponse(msg);
      break;

    case 'call-cancel':
      handleCallCancel(msg);
      break;

    case 'call-error':
      showToast(msg.message || '通话发起失败');
      if (state.room && !state.callAnswered) leaveCall({ silent: true });
      break;

    case 'welcome':
      handleWelcome(msg);
      break;

    case 'peer-joined':
      state.callAnswered = true;
      clearTimeout(state.callTimeout);
      state.callTimeout = null;
      startCallTimer();
      updateRoomTitle();
      createPeer(msg.peer);
      addSystem(`${msg.peer.name} 加入了通话`);
      break;

    case 'peer-left': {
      const peer = state.peers.get(msg.peerId);
      if (!peer) break;
      const name = peer.name;
      closePeer(peer);
      addSystem(`${name} 离开了通话`);
      updateCount();
      if (state.room && state.callPeer && state.peers.size === 0) {
        const roomId = state.room;
        setTimeout(() => {
          if (state.room === roomId && state.peers.size === 0) {
            leaveCall({ reason: '对方已结束通话' });
          }
        }, 250);
      }
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
      if (!els.chatPanel.classList.contains('open')) {
        els.chatToggle.classList.add('unread');
      }
      break;

    case 'error':
      showToast(msg.message || '服务器返回错误');
      break;

    default:
      break;
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
  els.micBtn.disabled = !hasMedia;
  els.camBtn.disabled = !hasMedia;
  const audioOn = hasMedia
    ? state.localStream.getAudioTracks().some((track) => track.enabled)
    : false;
  const videoOn = hasMedia
    ? state.localStream.getVideoTracks().some((track) => track.enabled)
    : false;
  els.micBtn.classList.toggle('off', !audioOn);
  els.camBtn.classList.toggle('off', !videoOn);
  els.micBtn.setAttribute('aria-pressed', String(audioOn));
  els.camBtn.setAttribute('aria-pressed', String(videoOn));
}

function mediaErrorMessage(err) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return '当前页面不是 HTTPS 安全上下文，浏览器禁止访问摄像头/麦克风。请通过 https:// 或 localhost 打开本页。';
  }
  switch (err && err.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return '摄像头/麦克风权限被拒绝，当前为文字模式。可在“设置 → 重新授权摄像头/麦克风”中重试。';
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

function releaseLocalMedia() {
  if (state.localStream) {
    for (const track of state.localStream.getTracks()) track.stop();
    state.localStream = null;
  }
  if (state.localVideo) {
    state.localVideo.srcObject = null;
    if (state.localTile) state.localTile.classList.remove('has-video');
  }
  state.mediaError = '';
  hideBanner();
  if (els.settingsHint) els.settingsHint.textContent = '';
  syncMediaButtons();
}

async function ensureLocalMedia() {
  if (state.localStream) return true;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    state.mediaError = mediaErrorMessage();
    if (els.settingsHint) els.settingsHint.textContent = state.mediaError;
    if (state.room) showBanner(state.mediaError);
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
    for (const peer of state.peers.values()) applyLocalTracks(peer.pc);
    state.mediaError = '';
    hideBanner();
    if (els.settingsHint) {
      els.settingsHint.textContent = '摄像头/麦克风已开启；浏览器允许后通话不会再询问。';
    }
    syncMediaButtons();
    return true;
  } catch (err) {
    state.mediaError = mediaErrorMessage(err);
    if (els.settingsHint) els.settingsHint.textContent = state.mediaError;
    if (state.room) showBanner(state.mediaError);
    syncMediaButtons();
    return false;
  }
}

function toggleMic() {
  if (!state.localStream) return;
  const tracks = state.localStream.getAudioTracks();
  if (tracks.length === 0) return;
  const enabled = !tracks.every((track) => track.enabled);
  tracks.forEach((track) => {
    track.enabled = enabled;
  });
  syncMediaButtons();
}

function toggleCamera() {
  if (!state.localStream) return;
  const tracks = state.localStream.getVideoTracks();
  if (tracks.length === 0) return;
  const enabled = !tracks.every((track) => track.enabled);
  tracks.forEach((track) => {
    track.enabled = enabled;
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
}

function sendSignal(to, data) {
  send({ type: 'signal', to, data });
}

async function makeOffer(peer) {
  const pc = peer.pc;
  if (peer.makingOffer || pc.signalingState !== 'stable') return;
  try {
    peer.makingOffer = true;
    await pc.setLocalDescription();
    sendSignal(peer.id, { description: pc.localDescription });
  } catch (err) {
    console.warn('[webrtc] negotiation failed', err);
  } finally {
    peer.makingOffer = false;
  }
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
  const name = `我（${(state.user && state.user.name) || '我'}）`;
  const tile = createTile({ id: 'local', name, local: true });
  els.videos.prepend(tile);
  state.localTile = tile;
  state.localVideo = tile.querySelector('video');
  if (state.localStream) {
    state.localVideo.srcObject = state.localStream;
    state.localVideo.play().catch(() => {});
    tile.classList.add('has-video');
  }
}

function createPeer({ id, name }) {
  if (state.peers.has(id)) return state.peers.get(id);

  const pc = new RTCPeerConnection({
    iceServers: state.iceServers,
    iceTransportPolicy: state.forceRelay ? 'relay' : 'all',
  });
  const tile = createTile({ id, name, local: false });
  els.videos.append(tile);

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

  pc.onnegotiationneeded = () => {
    makeOffer(peer);
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
/* Room / call                                                         */
/* ------------------------------------------------------------------ */

function updateCount() {
  els.peerCount.textContent = `${1 + state.peers.size} 人`;
}

function setConnectionOnline(online) {
  els.connDot.classList.toggle('online', Boolean(online));
  els.connDot.classList.toggle('offline', !online && Boolean(state.room));
  els.connDot.title = online ? '已连接信令服务器' : '未连接信令服务器';
}

function addSystem(text) {
  if (!els.messages) return;
  const el = document.createElement('div');
  el.className = 'msg system';
  el.textContent = text;
  els.messages.append(el);
  els.messages.scrollTop = els.messages.scrollHeight;
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
  els.messages.append(el);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function updateRoomTitle() {
  if (state.callPeer) {
    els.roomName.textContent = state.callAnswered
      ? `与 ${state.callPeer.name} 通话`
      : `正在呼叫 ${state.callPeer.name}…`;
  } else if (state.room) {
    els.roomName.textContent = state.room;
  }
}

function showRoomView() {
  els.callOverlay.hidden = false;
  els.incomingPanel.hidden = true;
  els.roomView.hidden = false;
  els.settingsPanel.hidden = true;
}

function showIncomingView() {
  els.callOverlay.hidden = false;
  els.incomingPanel.hidden = false;
  els.roomView.hidden = true;
  els.settingsPanel.hidden = true;
}

function hideCallOverlay() {
  els.callOverlay.hidden = true;
  els.roomView.hidden = true;
  els.incomingPanel.hidden = true;
  closeChat();
}

function openChat() {
  els.chatPanel.classList.add('open');
  els.chatToggle.classList.remove('unread');
}

function closeChat() {
  els.chatPanel.classList.remove('open');
}

function startCallTimer() {
  if (state.callTimerInterval) return;
  state.callStartedAt = Date.now();
  els.callTimer.textContent = '00:00';
  state.callTimerInterval = setInterval(() => {
    const seconds = Math.floor((Date.now() - state.callStartedAt) / 1000);
    els.callTimer.textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  }, 1000);
}

function stopCallTimer() {
  clearInterval(state.callTimerInterval);
  state.callTimerInterval = null;
  state.callStartedAt = 0;
  els.callTimer.textContent = '00:00';
}

function sendJoin() {
  if (!state.room || !state.wsAuthed) return;
  send({ type: 'join', room: state.room, name: (state.user && state.user.name) || '访客' });
}

async function enterCall(roomId, peer, answered) {
  state.room = roomId;
  state.callPeer = peer || null;
  state.callAnswered = Boolean(answered);
  state.selfId = null;
  state.hasJoinedOnce = false;
  closeAllPeers();
  els.videos.replaceChildren();
  els.messages.replaceChildren();
  state.localTile = null;
  state.localVideo = null;
  hideBanner();
  closeChat();
  stopCallTimer();

  showRoomView();
  updateRoomTitle();
  updateCount();
  setConnectionOnline(Boolean(state.wsAuthed));
  syncMediaButtons();

  await ensureLocalMedia();
  if (state.room !== roomId) return;
  if (state.wsAuthed) sendJoin();
}

function startCall(contact) {
  if (!state.user) return;
  if (state.room) {
    showToast('请先结束当前通话');
    return;
  }
  if (!contact.online) {
    showToast('对方不在线');
    return;
  }
  const roomId = makeRoomId();
  if (!send({ type: 'call-invite', to: contact.id, room: roomId })) {
    showToast('实时连接已断开，无法发起通话');
    return;
  }
  state.callTimeout = setTimeout(() => {
    if (state.room !== roomId || state.callAnswered) return;
    send({ type: 'call-cancel', to: contact.id, room: roomId });
    leaveCall({ reason: '对方未接听' });
  }, 30000);
  enterCall(roomId, contact, false);
}

function handleWelcome(msg) {
  state.selfId = msg.self.id;
  showRoomView();
  if (!state.callPeer) els.roomName.textContent = msg.room;
  updateRoomTitle();
  if (state.mediaError) showBanner(state.mediaError);
  else hideBanner();
  ensureLocalTile();
  addSystem(state.hasJoinedOnce ? '已重新连接' : '已加入通话');
  state.hasJoinedOnce = true;
  for (const peer of msg.peers || []) createPeer(peer);
  updateCount();
  setConnectionOnline(true);
  if (state.callAnswered) startCallTimer();
}

function leaveCall({ reason, silent } = {}) {
  const room = state.room;
  const peer = state.callPeer;
  clearTimeout(state.callTimeout);
  state.callTimeout = null;
  clearTimeout(state.incomingTimer);
  state.incomingTimer = null;

  if (room) {
    send({ type: 'leave' });
    if (peer) send({ type: 'call-cancel', to: peer.id, room });
  }

  stopCallTimer();
  closeAllPeers();
  state.room = null;
  state.callPeer = null;
  state.callAnswered = false;
  state.selfId = null;
  state.hasJoinedOnce = false;
  state.incomingInvite = null;
  els.videos.replaceChildren();
  els.messages.replaceChildren();
  state.localTile = null;
  state.localVideo = null;
  hideCallOverlay();
  updateCount();
  setConnectionOnline(Boolean(state.wsAuthed));
  if (reason && !silent) showToast(reason);
}

function handleIncomingCall(msg) {
  if (!msg || !msg.from) return;
  if (state.room || state.incomingInvite) {
    send({
      type: 'call-response',
      to: msg.from.id,
      room: msg.room,
      accepted: false,
      reason: 'busy',
    });
    return;
  }
  state.incomingInvite = msg;
  els.incomingCallName.textContent = msg.from.name;
  els.incomingCallAvatar.textContent = initialOf(msg.from.name);
  showIncomingView();
  clearTimeout(state.incomingTimer);
  state.incomingTimer = setTimeout(() => {
    if (!state.incomingInvite) return;
    send({
      type: 'call-response',
      to: msg.from.id,
      room: msg.room,
      accepted: false,
      reason: 'timeout',
    });
    state.incomingInvite = null;
    hideCallOverlay();
  }, 30000);
}

function acceptIncomingCall() {
  const invite = state.incomingInvite;
  if (!invite) return;
  clearTimeout(state.incomingTimer);
  state.incomingTimer = null;
  state.incomingInvite = null;
  send({
    type: 'call-response',
    to: invite.from.id,
    room: invite.room,
    accepted: true,
  });
  enterCall(invite.room, invite.from, true);
  startCallTimer();
}

function rejectIncomingCall() {
  const invite = state.incomingInvite;
  if (!invite) return;
  clearTimeout(state.incomingTimer);
  state.incomingTimer = null;
  state.incomingInvite = null;
  send({
    type: 'call-response',
    to: invite.from.id,
    room: invite.room,
    accepted: false,
  });
  hideCallOverlay();
  showToast('已拒绝通话');
}

function handleCallResponse(msg) {
  if (!state.room || msg.room !== state.room) return;
  if (msg.accepted) {
    state.callAnswered = true;
    clearTimeout(state.callTimeout);
    state.callTimeout = null;
    startCallTimer();
    updateRoomTitle();
  } else if (!state.peers.size) {
    leaveCall({ reason: '对方拒绝了通话' });
  }
}

function handleCallCancel(msg) {
  if (state.incomingInvite && state.incomingInvite.room === msg.room) {
    clearTimeout(state.incomingTimer);
    state.incomingTimer = null;
    state.incomingInvite = null;
    hideCallOverlay();
    showToast('对方已取消呼叫');
    return;
  }
  if (state.room === msg.room) {
    leaveCall({ reason: state.callAnswered ? '对方已结束通话' : '对方已取消呼叫' });
  }
}

function openSettings() {
  if (!state.user) return;
  els.settingsName.textContent = state.user.name;
  els.settingsRole.textContent = state.user.role === 'phone' ? '手机用户' : '设备用户';
  els.settingsNumber.textContent = myNumber();
  els.settingsAvatar.textContent = initialOf(state.user.name);
  els.settingsHint.textContent = state.localStream
    ? '摄像头/麦克风已开启；浏览器允许后通话不会再询问。'
    : state.mediaError || '摄像头/麦克风尚未开启。';
  els.settingsPanel.hidden = false;
}

function closeSettings() {
  els.settingsPanel.hidden = true;
}

function hideBanner() {
  els.banner.hidden = true;
  els.banner.textContent = '';
}

function showBanner(message) {
  els.banner.textContent = message;
  els.banner.hidden = false;
}

/* ------------------------------------------------------------------ */
/* Wiring                                                              */
/* ------------------------------------------------------------------ */

els.tabPhone.addEventListener('click', () => setAuthRole('phone'));
els.tabDevice.addEventListener('click', () => setAuthRole('device'));
els.phoneModeLogin.addEventListener('click', () => setAuthMode('phone', 'login'));
els.phoneModeRegister.addEventListener('click', () => setAuthMode('phone', 'register'));
els.deviceModeLogin.addEventListener('click', () => setAuthMode('device', 'login'));
els.deviceModeRegister.addEventListener('click', () => setAuthMode('device', 'register'));

els.phoneLoginForm.addEventListener('submit', (event) => {
  event.preventDefault();
  submitAuth(els.phoneLoginForm, () =>
    loginWith(els.phoneLoginPhone.value.trim(), els.phoneLoginPassword.value),
  );
});

els.phoneRegisterForm.addEventListener('submit', (event) => {
  event.preventDefault();
  submitAuth(els.phoneRegisterForm, registerPhone);
});

els.phoneLoginModePassword.addEventListener('click', () => {
  state.smsLoginMode = 'password';
  updatePhoneLoginUI();
});
els.phoneLoginModeSms.addEventListener('click', () => {
  state.smsLoginMode = 'sms';
  updatePhoneLoginUI();
});
els.phoneSmsLoginForm.addEventListener('submit', (event) => {
  event.preventDefault();
  submitAuth(els.phoneSmsLoginForm, () =>
    loginWithSms(els.phoneSmsLoginPhone.value.trim(), els.phoneSmsLoginCode.value.trim()),
  );
});
els.phoneRegisterSend.addEventListener('click', () => {
  requestSmsCode(els.phoneRegisterPhone.value.trim(), 'register', els.phoneRegisterSend);
});
els.phoneSmsLoginSend.addEventListener('click', () => {
  requestSmsCode(els.phoneSmsLoginPhone.value.trim(), 'login', els.phoneSmsLoginSend);
});

els.deviceLoginForm.addEventListener('submit', (event) => {
  event.preventDefault();
  submitAuth(els.deviceLoginForm, () =>
    loginWith(els.deviceLoginNumber.value.trim(), els.deviceLoginPassword.value),
  );
});

els.deviceRegisterForm.addEventListener('submit', (event) => {
  event.preventDefault();
  submitAuth(els.deviceRegisterForm, registerDevice);
});

els.deviceEnterApp.addEventListener('click', () => {
  if (state.pendingAuth) {
    completeAuth(state.pendingAuth);
  }
});

els.settingsBtn.addEventListener('click', openSettings);
els.settingsClose.addEventListener('click', closeSettings);
els.settingsPanel.addEventListener('click', (event) => {
  if (event.target === els.settingsPanel) closeSettings();
});
els.settingsCopy.addEventListener('click', async () => {
  const value = myNumber();
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    showToast('号码已复制');
  } catch {
    window.prompt('复制号码：', value);
  }
});
els.settingsRefresh.addEventListener('click', async () => {
  await refreshContacts();
  showToast('通讯录已刷新');
});
els.settingsMedia.addEventListener('click', async () => {
  const ok = await ensureLocalMedia();
  showToast(ok ? '摄像头/麦克风已开启' : state.mediaError || '无法开启摄像头/麦克风');
});
els.settingsLogout.addEventListener('click', async () => {
  closeSettings();
  await logout();
});

els.chatToggle.addEventListener('click', () => {
  if (els.chatPanel.classList.contains('open')) closeChat();
  else openChat();
});
els.chatClose.addEventListener('click', closeChat);

els.addContactForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const value = els.addContactInput.value.trim();
  if (!value) return;
  addContact(value);
});

els.leaveBtn.addEventListener('click', () => leaveCall());
els.micBtn.addEventListener('click', toggleMic);
els.camBtn.addEventListener('click', toggleCamera);

els.chatForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const text = els.chatInput.value.trim();
  if (!text) return;
  if (!send({ type: 'chat', text })) {
    showToast('连接已断开，消息未发送');
    return;
  }
  els.chatInput.value = '';
});

els.acceptCall.addEventListener('click', acceptIncomingCall);
els.rejectCall.addEventListener('click', rejectIncomingCall);

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
      user: state.user,
      room: state.room,
      callPeer: state.callPeer,
      callAnswered: state.callAnswered,
      wsAuthed: state.wsAuthed,
      peers: [...state.peers.values()].map((peer) => ({
        id: peer.id,
        name: peer.name,
        connectionState: peer.pc.connectionState,
        iceConnectionState: peer.pc.iceConnectionState,
        signalingState: peer.pc.signalingState,
        iceGatheringState: peer.pc.iceGatheringState,
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
      contacts: state.contacts,
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
/* ICE + boot                                                          */
/* ------------------------------------------------------------------ */

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

(async function boot() {
  const params = new URLSearchParams(location.search);
  state.forceRelay = params.get('ice') === 'relay';
  applyPageRole();
  loadIceServers();
  await loadConfig();

  if (state.token) {
    try {
      const data = await api('/api/me');
      if (PAGE_ROLE && data.user.role !== PAGE_ROLE) {
        location.replace(`/${data.user.role}`);
        return;
      }
      state.user = data.user;
      els.authView.hidden = true;
      els.appView.hidden = false;
      updateIdentity();
      connectSocket();
      refreshContacts();
      ensureLocalMedia();
      return;
    } catch (err) {
      if (err.status === 401) {
        clearToken();
      } else {
        showAuth('无法连接服务器，请检查网络后重试');
        return;
      }
    }
  }

  showAuth();
})();
