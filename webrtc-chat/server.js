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

  /** @type {Map<string, Map<string, {id: string, name: string, roomId: string, ws: import('ws').WebSocket}>>} */
  const rooms = new Map();

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

  function handleJoin(ws, msg) {
    if (ws.peer) {
      sendError(ws, 'already-joined', '已经在房间中');
      return;
    }
    const roomId = typeof msg.room === 'string' ? msg.room.trim().slice(0, 48) : '';
    const name = typeof msg.name === 'string' ? msg.name.trim().slice(0, 32) : '';
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
      case 'join':
        handleJoin(ws, msg);
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

  function handleRequest(req, res) {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { Allow: 'GET, HEAD' });
      res.end('Method Not Allowed');
      return;
    }

    let url;
    try {
      url = new URL(req.url, 'http://localhost');
    } catch {
      writeText(res, 400, 'Bad Request');
      return;
    }
    if (url.pathname === '/api/ice') {
      writeJson(res, 200, { iceServers: iceServersForHost(requestHostname(req)) });
      return;
    }
    if (url.pathname === '/healthz') {
      const peers = [...rooms.values()].reduce((total, room) => total + room.size, 0);
      writeJson(res, 200, {
        ok: true,
        rooms: rooms.size,
        peers,
        uptimeSeconds: Math.round(process.uptime()),
      });
      return;
    }

    const file = resolvePublicFile(url.pathname);
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

    ws.on('close', () => handleLeave(ws));
    ws.on('error', () => handleLeave(ws));
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
        server.close(() => resolve());
      });
    });
  }

  return { server, wss, rooms, iceServers, secure, close };
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
