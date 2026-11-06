'use strict';

/**
 * Tiny WebRTC signaling server.
 *
 * - Serves the static web client over HTTPS (or HTTP for local development).
 * - Relays WebRTC offers/answers/ICE candidates between room members.
 * - Broadcasts text chat messages inside a room.
 *
 * It intentionally has no accounts or persistence: it is meant for a trusted
 * LAN demo. See README.md before exposing it to the internet.
 */

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');
const { WebSocketServer } = require('ws');
const { createStore, StoreError } = require('./lib/store');
const { createSmsService, SmsError } = require('./lib/sms');

const PUBLIC_DIR = path.join(__dirname, 'public');
const DEFAULT_PORT = 8443;
const DEFAULT_HTTP_PORT = 8080;
const MAX_PEERS_PER_ROOM = 8;
const MAX_CHAT_CHARS = 2000;
const MAX_MESSAGE_BYTES = 64 * 1024;
const HEARTBEAT_INTERVAL_MS = 30000;

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
};

function splitUrls(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * Build the ICE server list handed to browsers.
 *
 * STUN_URLS / TURN_URLS are comma separated. TURN_USER / TURN_PASSWORD are
 * shared credentials for the TURN URLs (fine for a trusted LAN demo).
 *
 * URLs may contain a {host} placeholder. It is replaced with the hostname the
 * client used to load the page, so the same deployment works via a LAN IP and
 * via a public domain without hard-coding either one.
 */
function readIceServers(env, hostname) {
  const withHost = (url) => (hostname ? url.split('{host}').join(hostname) : url);

  const servers = [];
  const stunUrls = splitUrls(env.STUN_URLS).map(withHost);
  if (stunUrls.length > 0) {
    servers.push({ urls: stunUrls });
  }
  const turnUrls = splitUrls(env.TURN_URLS).map(withHost);
  if (turnUrls.length > 0) {
    const server = { urls: turnUrls };
    if (env.TURN_USER) server.username = env.TURN_USER;
    if (env.TURN_PASSWORD) server.credential = env.TURN_PASSWORD;
    servers.push(server);
  }
  return servers;
}

function requestHostname(req) {
  try {
    return new URL(`http://${req.headers.host || 'localhost'}`).hostname;
  } catch {
    return 'localhost';
  }
}

function resolvePublicFile(pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  if (decoded.includes('\0')) return null;
  if (decoded === '/' || decoded === '') decoded = '/index.html';
  const file = path.resolve(PUBLIC_DIR, '.' + decoded);
  if (file !== PUBLIC_DIR && !file.startsWith(PUBLIC_DIR + path.sep)) return null;
  return file;
}

function writeJson(res, statusCode, body) {
  const payload = JSON.stringify(body);
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
    'Cache-Control': 'no-store',
  });
  res.end(payload);
}

function writeText(res, statusCode, text) {
  res.writeHead(statusCode, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Content-Length': Buffer.byteLength(text),
  });
  res.end(text);
}

/**
 * Create the app without listening. Exported so tests can run it on an
 * ephemeral port.
 */
function createApp(options = {}) {
  const env = options.env || process.env;
  const log = options.log || console;
  const iceServers = options.iceServers || readIceServers(env);
  const iceServersForHost = (hostname) => options.iceServers || readIceServers(env, hostname);
  const maxPeers = Math.max(
    1,
    Math.min(64, Number(options.maxPeers || env.MAX_PEERS || MAX_PEERS_PER_ROOM) || MAX_PEERS_PER_ROOM),
  );

  const store = options.store || createStore({ dbPath: options.dbPath || env.DB_PATH });
  const sms = options.sms || createSmsService({ env, log });

  /** @type {Map<string, Map<string, {id: string, name: string, roomId: string, ws: import('ws').WebSocket}>>} */
  const rooms = new Map();
  /** @type {Map<string, Set<import('ws').WebSocket>>} */
  const presence = new Map();
  /** @type {Map<string, {count: number, resetAt: number}>} */
  const authAttempts = new Map();
  /** @type {Map<string, {count: number, windowStart: number, lastSent: number}>} */
  const smsPhoneAttempts = new Map();

  function send(ws, payload) {
    if (ws.readyState !== ws.OPEN) return;
    try {
      ws.send(JSON.stringify(payload));
    } catch (err) {
      log.warn?.('failed to send websocket message:', err.message);
    }
  }

  function sendError(ws, code, message) {
    send(ws, { type: 'error', code, message });
  }

  function broadcast(room, payload, except) {
    for (const peer of room.values()) {
      if (peer === except) continue;
      send(peer.ws, payload);
    }
  }

  function isOnline(userId) {
    const sockets = presence.get(userId);
    return Boolean(sockets && sockets.size > 0);
  }

  function sendToUser(userId, payload) {
    const sockets = presence.get(userId);
    if (!sockets) return;
    for (const ws of sockets) send(ws, payload);
  }

  function broadcastPresence(userId, online) {
    for (const contactId of store.getActiveContactIds(userId)) {
      sendToUser(contactId, { type: 'presence', userId, online });
    }
  }

  function addPresence(ws) {
    const user = ws.user;
    if (!user) return;
    let sockets = presence.get(user.id);
    if (!sockets) {
      sockets = new Set();
      presence.set(user.id, sockets);
    }
    sockets.add(ws);
    if (sockets.size === 1) broadcastPresence(user.id, true);
  }

  function removePresence(ws) {
    const user = ws.user;
    if (!user) return;
    const sockets = presence.get(user.id);
    if (!sockets) return;
    sockets.delete(ws);
    if (sockets.size === 0) {
      presence.delete(user.id);
      broadcastPresence(user.id, false);
    }
  }

  function decorateContacts(data) {
    const wrap = (item) => ({
      ...item,
      user: { ...item.user, online: isOnline(item.user.id) },
    });
    return {
      contacts: data.contacts.map(wrap),
      incoming: data.incoming.map(wrap),
      outgoing: data.outgoing.map(wrap),
    };
  }

  function notifyContactUpdated(userId, otherId) {
    sendToUser(userId, { type: 'contact-updated' });
    sendToUser(otherId, { type: 'contact-updated' });
  }

  function bearerToken(req) {
    const header = String(req.headers.authorization || '');
    const match = /^Bearer\s+(.+)$/i.exec(header);
    return match ? match[1].trim() : null;
  }

  function requireUser(req) {
    const token = bearerToken(req);
    const user = token ? store.getUserByToken(token) : null;
    if (!user) throw new StoreError('unauthorized', '请先登录', 401);
    return user;
  }

  async function readJsonBody(req) {
    const chunks = [];
    let size = 0;
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 16 * 1024) {
        throw new StoreError('payload-too-large', '请求体过大', 413);
      }
      chunks.push(chunk);
    }
    if (size === 0) return {};
    let parsed;
    try {
      parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch {
      throw new StoreError('invalid-json', '请求格式错误');
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new StoreError('invalid-json', '请求格式错误');
    }
    return parsed;
  }

  function checkAuthRateLimit(req) {
    if (authAttempts.size > 5000) authAttempts.clear();
    const key = req.socket.remoteAddress || 'unknown';
    const now = Date.now();
    let entry = authAttempts.get(key);
    if (!entry || entry.resetAt <= now) {
      entry = { count: 0, resetAt: now + 5 * 60 * 1000 };
      authAttempts.set(key, entry);
    }
    entry.count += 1;
    if (entry.count > 40) {
      throw new StoreError('rate-limited', '请求过于频繁，请稍后再试', 429);
    }
  }

  function normalizePhoneInput(value) {
    const phone = String(value == null ? '' : value).replace(/[\s-]/g, '');
    if (!/^1[3-9]\d{9}$/.test(phone)) {
      throw new StoreError('invalid-phone', '请输入有效的 11 位手机号');
    }
    return phone;
  }

  function checkSmsSendRate(phone, purpose) {
    if (smsPhoneAttempts.size > 5000) smsPhoneAttempts.clear();
    const key = `${purpose}:${phone}`;
    const now = Date.now();
    let entry = smsPhoneAttempts.get(key);
    if (!entry || now - entry.windowStart > 60 * 60 * 1000) {
      entry = { count: 0, windowStart: now, lastSent: 0 };
      smsPhoneAttempts.set(key, entry);
    }
    if (now - entry.lastSent < 60 * 1000) {
      throw new StoreError('sms-too-frequent', '验证码发送过于频繁，请稍后再试', 429);
    }
    if (entry.count >= 5) {
      throw new StoreError('sms-limit', '该手机号验证码发送次数过多，请稍后再试', 429);
    }
    entry.count += 1;
    entry.lastSent = now;
  }

  function verifySmsCode(phone, purpose, codeInput) {
    const code = String(codeInput == null ? '' : codeInput).trim();
    if (!/^\d{6}$/.test(code)) {
      throw new StoreError('invalid-code', '请输入 6 位短信验证码');
    }
    const result = store.verifySmsCode(phone, purpose, sms.hashCode(phone, purpose, code));
    if (!result.ok) {
      throw new StoreError('invalid-code', '验证码错误或已过期');
    }
  }

  function sendApiError(res, err) {
    if (res.headersSent) return;
    if (err instanceof StoreError) {
      writeJson(res, err.status || 400, {
        error: { code: err.code, message: err.message },
      });
      return;
    }
    if (err instanceof SmsError) {
      writeJson(res, err.status || 502, {
        error: { code: err.code, message: err.message },
      });
      return;
    }
    log.error?.('api request failed:', err);
    writeJson(res, 500, {
      error: { code: 'internal', message: '服务器内部错误' },
    });
  }

  function handleAuth(ws, msg) {
    if (ws.user) return;
    const user = store.getUserByToken(typeof msg.token === 'string' ? msg.token : '');
    if (!user) {
      send(ws, { type: 'auth-error', message: '登录状态已失效，请重新登录' });
      return;
    }
    ws.user = user;
    addPresence(ws);
    send(ws, { type: 'auth-ok', user });
  }

  function handleCallInvite(ws, msg) {
    if (!ws.user) {
      send(ws, { type: 'call-error', code: 'unauthorized', message: '请先登录' });
      return;
    }
    const to = typeof msg.to === 'string' ? msg.to : '';
    const roomId = typeof msg.room === 'string' ? msg.room.trim().slice(0, 64) : '';
    if (!to || !roomId) return;
    if (!store.getActiveContact(ws.user.id, to)) {
      send(ws, { type: 'call-error', code: 'not-contact', message: '对方不是你的联系人' });
      return;
    }
    if (!isOnline(to)) {
      send(ws, { type: 'call-error', code: 'offline', message: '对方不在线' });
      return;
    }
    sendToUser(to, {
      type: 'call-invite',
      from: { id: ws.user.id, name: ws.user.name, role: ws.user.role },
      room: roomId,
    });
  }

  function handleCallResponse(ws, msg) {
    if (!ws.user) return;
    const to = typeof msg.to === 'string' ? msg.to : '';
    const roomId = typeof msg.room === 'string' ? msg.room.trim().slice(0, 64) : '';
    if (!to || !roomId) return;
    if (!store.getActiveContact(ws.user.id, to)) return;
    sendToUser(to, {
      type: 'call-response',
      from: { id: ws.user.id, name: ws.user.name, role: ws.user.role },
      room: roomId,
      accepted: Boolean(msg.accepted),
    });
  }

  function handleCallCancel(ws, msg) {
    if (!ws.user) return;
    const to = typeof msg.to === 'string' ? msg.to : '';
    const roomId = typeof msg.room === 'string' ? msg.room.trim().slice(0, 64) : '';
    if (!to || !roomId) return;
    if (!store.getActiveContact(ws.user.id, to)) return;
    sendToUser(to, {
      type: 'call-cancel',
      from: { id: ws.user.id, name: ws.user.name },
      room: roomId,
    });
  }

  function handleJoin(ws, msg) {
    if (ws.peer) {
      sendError(ws, 'already-joined', '已经在房间中');
      return;
    }
    const roomId = typeof msg.room === 'string' ? msg.room.trim().slice(0, 48) : '';
    const fallbackName = typeof msg.name === 'string' ? msg.name.trim().slice(0, 32) : '';
    const name = ws.user ? ws.user.name : fallbackName;
    if (!roomId || !name) {
      sendError(ws, 'invalid-join', '房间号和昵称不能为空');
      return;
    }

    let room = rooms.get(roomId);
    if (!room) {
      room = new Map();
      rooms.set(roomId, room);
    }
    if (room.size >= maxPeers) {
      sendError(ws, 'room-full', `房间已满（最多 ${maxPeers} 人）`);
      return;
    }

    const peer = {
      id: crypto.randomUUID(),
      name,
      roomId,
      ws,
    };
    ws.peer = peer;
    room.set(peer.id, peer);

    const existingPeers = [...room.values()]
      .filter((item) => item !== peer)
      .map((item) => ({ id: item.id, name: item.name }));

    send(ws, {
      type: 'welcome',
      self: { id: peer.id, name: peer.name },
      room: roomId,
      peers: existingPeers,
      maxPeers,
    });

    for (const other of room.values()) {
      if (other === peer) continue;
      send(other.ws, { type: 'peer-joined', peer: { id: peer.id, name: peer.name } });
    }

    log.info?.(`[join] ${name} joined "${roomId}" (${room.size} peer(s))`);
  }

  function handleSignal(ws, msg) {
    const peer = ws.peer;
    if (!peer) return;
    const room = rooms.get(peer.roomId);
    if (!room) return;

    const target = room.get(typeof msg.to === 'string' ? msg.to : '');
    if (!target) return;

    const data = msg.data;
    if (!data || typeof data !== 'object') return;
    const description = data.description;
    const candidate = data.candidate;
    if (!description && !candidate) return;

    send(target.ws, { type: 'signal', from: peer.id, data });
  }

  function handleChat(ws, msg) {
    const peer = ws.peer;
    if (!peer) return;
    const room = rooms.get(peer.roomId);
    if (!room) return;
    const text = typeof msg.text === 'string' ? msg.text.trim() : '';
    if (!text) return;

    broadcast(room, {
      type: 'chat',
      from: { id: peer.id, name: peer.name },
      text: text.slice(0, MAX_CHAT_CHARS),
      ts: Date.now(),
    });
  }

  function handleLeave(ws) {
    const peer = ws.peer;
    if (!peer) return;
    ws.peer = null;

    const room = rooms.get(peer.roomId);
    if (!room) return;
    room.delete(peer.id);
    broadcast(room, { type: 'peer-left', peerId: peer.id });
    if (room.size === 0) rooms.delete(peer.roomId);
    log.info?.(`[leave] ${peer.name} left "${peer.roomId}"`);
  }

  function handleMessage(ws, msg) {
    if (!msg || typeof msg.type !== 'string') return;
    switch (msg.type) {
      case 'auth':
        handleAuth(ws, msg);
        break;
      case 'join':
        handleJoin(ws, msg);
        break;
      case 'call-invite':
        handleCallInvite(ws, msg);
        break;
      case 'call-response':
        handleCallResponse(ws, msg);
        break;
      case 'call-cancel':
        handleCallCancel(ws, msg);
        break;
      case 'signal':
        handleSignal(ws, msg);
        break;
      case 'chat':
        handleChat(ws, msg);
        break;
      case 'leave':
        handleLeave(ws);
        break;
      default:
        break;
    }
  }

  async function handleApiRequest(req, res, url) {
    const pathName = url.pathname;

    if (req.method === 'GET' && pathName === '/api/ice') {
      writeJson(res, 200, { iceServers: iceServersForHost(requestHostname(req)) });
      return;
    }

    if (req.method === 'GET' && pathName === '/api/config') {
      writeJson(res, 200, {
        smsEnabled: sms.enabled,
        smsProvider: sms.provider,
        smsDevShowCode: sms.devShowCode,
      });
      return;
    }

    if (req.method === 'POST' && pathName === '/api/auth/sms/send') {
      checkAuthRateLimit(req);
      if (!sms.enabled) {
        throw new StoreError('sms-disabled', '短信验证码服务未启用', 503);
      }
      const body = await readJsonBody(req);
      const phone = normalizePhoneInput(body.phone);
      const purpose = body.purpose === 'login' ? 'login' : 'register';
      const existing = store.getUserByPhone(phone);
      if (purpose === 'register' && existing) {
        throw new StoreError('phone-taken', '该手机号已注册，请直接登录', 409);
      }
      if (purpose === 'login' && !existing) {
        throw new StoreError('user-not-found', '该手机号未注册', 404);
      }
      checkSmsSendRate(phone, purpose);
      const code = sms.generateCode();
      store.createSmsCode(phone, purpose, sms.hashCode(phone, purpose, code), sms.ttlSeconds * 1000);
      await sms.send(phone, code, purpose);
      const payload = { ok: true, expiresIn: sms.ttlSeconds };
      if (sms.devShowCode) payload.devCode = code;
      writeJson(res, 200, payload);
      return;
    }

    if (req.method === 'POST' && pathName === '/api/auth/register/phone') {
      checkAuthRateLimit(req);
      const body = await readJsonBody(req);
      if (sms.enabled) {
        const phone = normalizePhoneInput(body.phone);
        verifySmsCode(phone, 'register', body.code);
        body.phone = phone;
      }
      writeJson(res, 201, store.registerPhone(body));
      return;
    }

    if (req.method === 'POST' && pathName === '/api/auth/login/sms') {
      checkAuthRateLimit(req);
      if (!sms.enabled) {
        throw new StoreError('sms-disabled', '短信验证码服务未启用', 503);
      }
      const body = await readJsonBody(req);
      const phone = normalizePhoneInput(body.phone);
      verifySmsCode(phone, 'login', body.code);
      const result = store.loginByPhone(phone);
      if (!result) throw new StoreError('user-not-found', '该手机号未注册', 404);
      writeJson(res, 200, result);
      return;
    }
    if (req.method === 'POST' && pathName === '/api/auth/register/device') {
      checkAuthRateLimit(req);
      const body = await readJsonBody(req);
      writeJson(res, 201, store.registerDevice(body));
      return;
    }
    if (req.method === 'POST' && pathName === '/api/auth/login') {
      checkAuthRateLimit(req);
      const body = await readJsonBody(req);
      writeJson(res, 200, store.login(body));
      return;
    }
    if (req.method === 'POST' && pathName === '/api/auth/logout') {
      store.logout(bearerToken(req));
      writeJson(res, 200, { ok: true });
      return;
    }
    if (req.method === 'GET' && pathName === '/api/me') {
      const user = requireUser(req);
      writeJson(res, 200, { user });
      return;
    }
    if (req.method === 'GET' && pathName === '/api/contacts') {
      const user = requireUser(req);
      writeJson(res, 200, decorateContacts(store.listContacts(user.id)));
      return;
    }
    if (req.method === 'POST' && pathName === '/api/contacts/request') {
      const user = requireUser(req);
      const body = await readJsonBody(req);
      const result = store.requestContact(user.id, body);
      if (result.contact && result.contact.status === 'pending') {
        sendToUser(result.otherId, { type: 'contact-request', from: result.requester });
      }
      notifyContactUpdated(user.id, result.otherId);
      writeJson(res, 201, {
        contact: result.contact,
        autoAccepted: result.autoAccepted,
      });
      return;
    }

    const respondMatch = /^\/api\/contacts\/([^/]+)\/(confirm|reject)$/.exec(pathName);
    if (req.method === 'POST' && respondMatch) {
      const user = requireUser(req);
      const contactId = decodeURIComponent(respondMatch[1]);
      const result =
        respondMatch[2] === 'confirm'
          ? store.confirmContact(user.id, contactId)
          : store.rejectContact(user.id, contactId);
      notifyContactUpdated(user.id, result.otherId);
      writeJson(res, 200, { contact: result.contact });
      return;
    }

    const deleteMatch = /^\/api\/contacts\/([^/]+)$/.exec(pathName);
    if (req.method === 'DELETE' && deleteMatch) {
      const user = requireUser(req);
      const result = store.removeContact(user.id, decodeURIComponent(deleteMatch[1]));
      notifyContactUpdated(user.id, result.otherId);
      writeJson(res, 200, { ok: true });
      return;
    }

    writeJson(res, 404, { error: { code: 'not-found', message: '接口不存在' } });
  }

  function handleRequest(req, res) {
    let url;
    try {
      url = new URL(req.url, 'http://localhost');
    } catch {
      writeText(res, 400, 'Bad Request');
      return;
    }

    if (url.pathname.startsWith('/api/')) {
      handleApiRequest(req, res, url).catch((err) => sendApiError(res, err));
      return;
    }

    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { Allow: 'GET, HEAD' });
      res.end('Method Not Allowed');
      return;
    }

    if (url.pathname === '/healthz') {
      const peers = [...rooms.values()].reduce((total, room) => total + room.size, 0);
      let stats = { users: 0, contacts: 0 };
      try {
        stats = store.stats();
      } catch {
        // keep health endpoint available even if the database is closing
      }
      writeJson(res, 200, {
        ok: true,
        rooms: rooms.size,
        peers,
        users: stats.users,
        contacts: stats.contacts,
        uptimeSeconds: Math.round(process.uptime()),
      });
      return;
    }

    const normalizedPath = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, '') : url.pathname;
    const pageRoutes = {
      '/': path.join(PUBLIC_DIR, 'landing.html'),
      '/phone': path.join(PUBLIC_DIR, 'index.html'),
      '/device': path.join(PUBLIC_DIR, 'index.html'),
    };
    const file = pageRoutes[normalizedPath] || resolvePublicFile(url.pathname);
    if (!file) {
      writeText(res, 404, 'Not Found');
      return;
    }

    fs.readFile(file, (err, data) => {
      if (err) {
        writeText(res, 404, 'Not Found');
        return;
      }
      const ext = path.extname(file).toLowerCase();
      const isCode = ext === '.html' || ext === '.js' || ext === '.css';
      res.writeHead(200, {
        'Content-Type': MIME_TYPES[ext] || 'application/octet-stream',
        'Content-Length': data.length,
        'Cache-Control': isCode ? 'no-cache' : 'public, max-age=3600',
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
        'X-Frame-Options': 'DENY',
        'Content-Security-Policy':
          "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
          "connect-src 'self' ws: wss:; media-src 'self' blob:; object-src 'none'; " +
          "base-uri 'none'; frame-ancestors 'none'",
      });
      if (req.method === 'HEAD') {
        res.end();
      } else {
        res.end(data);
      }
    });
  }

  const tlsKeyFile = options.keyFile || env.TLS_KEY || path.join(__dirname, 'certs', 'key.pem');
  const tlsCertFile = options.certFile || env.TLS_CERT || path.join(__dirname, 'certs', 'cert.pem');
  const hasTls = fs.existsSync(tlsKeyFile) && fs.existsSync(tlsCertFile);
  const secure = options.tls === true || (options.tls !== false && hasTls);

  let server;
  if (secure) {
    server = https.createServer(
      {
        key: fs.readFileSync(tlsKeyFile),
        cert: fs.readFileSync(tlsCertFile),
      },
      handleRequest,
    );
  } else {
    server = http.createServer(handleRequest);
  }

  const wss = new WebSocketServer({ server, path: '/ws', maxPayload: MAX_MESSAGE_BYTES });

  wss.on('connection', (ws, req) => {
    ws.isAlive = true;
    ws.peer = null;
    ws.user = null;
    ws.remote = req.socket.remoteAddress;

    ws.on('pong', () => {
      ws.isAlive = true;
    });

    ws.on('message', (raw, isBinary) => {
      if (isBinary) return;
      let msg;
      try {
        msg = JSON.parse(raw.toString());
      } catch {
        return;
      }
      try {
        handleMessage(ws, msg);
      } catch (err) {
        log.error?.('websocket message failed:', err);
      }
    });

    ws.on('close', () => {
      handleLeave(ws);
      removePresence(ws);
    });
    ws.on('error', () => {
      handleLeave(ws);
      removePresence(ws);
    });
  });

  const heartbeat = setInterval(() => {
    for (const ws of wss.clients) {
      if (ws.isAlive === false) {
        ws.terminate();
        continue;
      }
      ws.isAlive = false;
      try {
        ws.ping();
      } catch {
        // ignore
      }
    }
  }, HEARTBEAT_INTERVAL_MS);
  heartbeat.unref();

  let closed = false;
  function close() {
    if (closed) return Promise.resolve();
    closed = true;
    clearInterval(heartbeat);
    for (const ws of wss.clients) {
      try {
        ws.close(1001, 'server shutting down');
      } catch {
        // ignore
      }
    }
    return new Promise((resolve) => {
      wss.close(() => {
        server.close(() => {
          presence.clear();
          try {
            store.close();
          } catch {
            // already closed
          }
          resolve();
        });
      });
    });
  }

  return { server, wss, rooms, iceServers, secure, store, close };
}

/**
 * Start the signaling server.
 *
 * options.port        HTTPS (or HTTP) port, default 8443
 * options.httpPort    when TLS is active, also listen here and redirect to HTTPS
 * options.host        bind address, default 0.0.0.0
 * options.tls         false to force HTTP (tests / local dev)
 */
function start(options = {}) {
  const app = createApp(options);
  const port = options.port === undefined ? DEFAULT_PORT : Number(options.port);
  const host = options.host || '0.0.0.0';

  return new Promise((resolve, reject) => {
    function onError(err) {
      app.server.removeListener('listening', onListening);
      reject(err);
    }
    function onListening() {
      app.server.removeListener('error', onError);
      const address = app.server.address();
      const actualPort = typeof address === 'object' && address ? address.port : port;
      let redirectServer = null;

      const httpPort = options.httpPort === undefined ? undefined : Number(options.httpPort);
      if (app.secure && httpPort) {
        redirectServer = http.createServer((req, res) => {
          const hostHeader = String(req.headers.host || '').replace(/:\d+$/, '') || 'localhost';
          res.writeHead(301, { Location: `https://${hostHeader}:${actualPort}${req.url}` });
          res.end();
        });
        redirectServer.on('error', (err) => {
          (options.log || console).warn?.('HTTP redirect listener failed:', err.message);
        });
        redirectServer.listen(httpPort, host);
      }

      const close = async () => {
        if (redirectServer) await new Promise((r) => redirectServer.close(() => r()));
        await app.close();
      };

      resolve({
        port: actualPort,
        httpPort: redirectServer ? httpPort : null,
        secure: app.secure,
        iceServers: app.iceServers,
        close,
      });
    }
    app.server.once('error', onError);
    app.server.once('listening', onListening);
    app.server.listen(port, host);
  });
}

if (require.main === module) {
  const port = Number(process.env.PORT || DEFAULT_PORT);
  const httpPort = process.env.HTTP_PORT === '' ? undefined : Number(process.env.HTTP_PORT ?? DEFAULT_HTTP_PORT);

  start({ port, httpPort })
    .then((app) => {
      const scheme = app.secure ? 'https' : 'http';
      console.log(`[webrtc-chat] listening on ${scheme}://0.0.0.0:${app.port}`);
      if (!app.secure) {
        console.warn(
          '[webrtc-chat] TLS certificate not found; running over HTTP. Browsers only allow camera/microphone on HTTPS or localhost.',
        );
      }
      if (app.httpPort) console.log(`[webrtc-chat] HTTP redirect on port ${app.httpPort}`);
      const iceUrls = app.iceServers.map((server) => ({ urls: server.urls }));
      console.log(`[webrtc-chat] ICE servers: ${JSON.stringify(iceUrls)}`);

      const shutdown = (signal) => {
        console.log(`[webrtc-chat] received ${signal}, shutting down`);
        app.close().then(() => process.exit(0));
      };
      process.on('SIGINT', () => shutdown('SIGINT'));
      process.on('SIGTERM', () => shutdown('SIGTERM'));
    })
    .catch((err) => {
      console.error('[webrtc-chat] failed to start:', err);
      process.exit(1);
    });
}

module.exports = { createApp, start, readIceServers };
