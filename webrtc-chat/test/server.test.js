'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert');
const { WebSocket } = require('ws');
const { start, readIceServers } = require('../server');

let app;
let baseUrl;

before(async () => {
  app = await start({
    port: 0,
    host: '127.0.0.1',
    tls: false,
    dbPath: ':memory:',
    env: { MAX_PEERS: '2' },
    iceServers: [{ urls: ['stun:192.168.1.110:3478'] }],
    log: { info() {}, warn() {}, error() {} },
  });
  baseUrl = `http://127.0.0.1:${app.port}`;
});

after(async () => {
  await app.close();
});

function createClient() {
  const ws = new WebSocket(`ws://127.0.0.1:${app.port}/ws`);
  const queue = [];
  const waiters = [];

  ws.on('message', (raw) => {
    const message = JSON.parse(raw.toString());
    const index = waiters.findIndex((waiter) => waiter.predicate(message));
    if (index >= 0) {
      const [waiter] = waiters.splice(index, 1);
      waiter.resolve(message);
    } else {
      queue.push(message);
    }
  });

  return {
    ws,
    open: new Promise((resolve, reject) => {
      ws.once('open', resolve);
      ws.once('error', reject);
    }),
    send(payload) {
      ws.send(JSON.stringify(payload));
    },
    waitFor(predicate, timeoutMs = 2000) {
      const index = queue.findIndex(predicate);
      if (index >= 0) return Promise.resolve(queue.splice(index, 1)[0]);
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          const waiterIndex = waiters.findIndex((waiter) => waiter.timer === timer);
          if (waiterIndex >= 0) waiters.splice(waiterIndex, 1);
          reject(new Error('timed out waiting for a websocket message'));
        }, timeoutMs);
        waiters.push({
          predicate,
          resolve: (message) => {
            clearTimeout(timer);
            resolve(message);
          },
          timer,
        });
      });
    },
    close() {
      ws.close();
    },
  };
}

async function join(client, room, name) {
  await client.open;
  client.send({ type: 'join', room, name });
  return client.waitFor((msg) => msg.type === 'welcome' || msg.type === 'error');
}

test('serves the web client and ICE configuration', async () => {
  const page = await fetch(`${baseUrl}/`);
  assert.equal(page.status, 200);
  assert.match(page.headers.get('content-type'), /text\/html/);
  assert.match(await page.text(), /landing-cards/);

  const phonePage = await fetch(`${baseUrl}/phone`);
  assert.equal(phonePage.status, 200);
  assert.match(await phonePage.text(), /id="auth-view"/);

  const devicePage = await fetch(`${baseUrl}/device`);
  assert.equal(devicePage.status, 200);
  assert.match(await devicePage.text(), /id="device-panel"/);

  const ice = await fetch(`${baseUrl}/api/ice`);
  assert.equal(ice.status, 200);
  assert.deepEqual(await ice.json(), {
    iceServers: [{ urls: ['stun:192.168.1.110:3478'] }],
  });

  const health = await fetch(`${baseUrl}/healthz`);
  assert.equal(health.status, 200);
  const healthBody = await health.json();
  assert.equal(healthBody.ok, true);
  assert.equal(typeof healthBody.peers, 'number');

  const missing = await fetch(`${baseUrl}/does-not-exist.txt`);
  assert.equal(missing.status, 404);

  const traversal = await fetch(`${baseUrl}/..%2Fserver.js`);
  assert.equal(traversal.status, 404);
});

test('replaces {host} in ICE URLs with the request hostname', () => {
  const env = {
    STUN_URLS: 'stun:{host}:3478',
    TURN_URLS: 'turn:{host}:3478?transport=udp,turn:{host}:3478?transport=tcp',
    TURN_USER: 'webrtc',
    TURN_PASSWORD: 'secret',
  };

  assert.deepEqual(readIceServers(env, 'www.shzhihuan.cn'), [
    { urls: ['stun:www.shzhihuan.cn:3478'] },
    {
      urls: [
        'turn:www.shzhihuan.cn:3478?transport=udp',
        'turn:www.shzhihuan.cn:3478?transport=tcp',
      ],
      username: 'webrtc',
      credential: 'secret',
    },
  ]);
  assert.deepEqual(readIceServers(env, '192.168.1.110')[1].urls, [
    'turn:192.168.1.110:3478?transport=udp',
    'turn:192.168.1.110:3478?transport=tcp',
  ]);
  assert.deepEqual(readIceServers({ STUN_URLS: 'stun:example.test:3478' }), [
    { urls: ['stun:example.test:3478'] },
  ]);
});

test('welcomes members, broadcasts chat and relays signals', async () => {
  const alice = createClient();
  const bob = createClient();

  const aliceWelcome = await join(alice, 'test-room', 'Alice');
  assert.equal(aliceWelcome.type, 'welcome');
  assert.equal(aliceWelcome.room, 'test-room');
  assert.deepEqual(aliceWelcome.peers, []);
  const aliceId = aliceWelcome.self.id;

  const bobWelcome = await join(bob, 'test-room', 'Bob');
  assert.equal(bobWelcome.type, 'welcome');
  assert.deepEqual(bobWelcome.peers.map((peer) => peer.name), ['Alice']);
  const bobId = bobWelcome.self.id;

  const joinedNotice = await alice.waitFor((msg) => msg.type === 'peer-joined');
  assert.equal(joinedNotice.peer.name, 'Bob');
  assert.equal(joinedNotice.peer.id, bobId);

  bob.send({ type: 'chat', text: '  你好，Alice  ' });
  const aliceChat = await alice.waitFor((msg) => msg.type === 'chat');
  const bobChat = await bob.waitFor((msg) => msg.type === 'chat');
  assert.equal(aliceChat.text, '你好，Alice');
  assert.equal(aliceChat.from.name, 'Bob');
  assert.equal(aliceChat.from.id, bobId);
  assert.equal(typeof aliceChat.ts, 'number');
  assert.deepEqual(bobChat, aliceChat);

  const offer = { type: 'offer', sdp: 'v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\n' };
  bob.send({ type: 'signal', to: aliceId, data: { description: offer } });
  const relayed = await alice.waitFor((msg) => msg.type === 'signal');
  assert.equal(relayed.from, bobId);
  assert.deepEqual(relayed.data.description, offer);

  // Signals addressed to another room must not leak.
  bob.send({ type: 'signal', to: 'not-a-peer', data: { candidate: { candidate: 'candidate:1' } } });

  const leftNotice = alice.waitFor((msg) => msg.type === 'peer-left');
  bob.close();
  const left = await leftNotice;
  assert.equal(left.peerId, bobId);

  alice.close();
});

test('rejects an empty join and a full room', async () => {
  const alice = createClient();
  await alice.open;
  alice.send({ type: 'join', room: '', name: 'Nobody' });
  const invalid = await alice.waitFor((msg) => msg.type === 'error');
  assert.equal(invalid.code, 'invalid-join');
  alice.close();

  const first = createClient();
  const second = createClient();
  const third = createClient();
  await join(first, 'full-room', 'One');
  await join(second, 'full-room', 'Two');
  const thirdResult = await join(third, 'full-room', 'Three');
  assert.equal(thirdResult.type, 'error');
  assert.equal(thirdResult.code, 'room-full');

  first.close();
  second.close();
  third.close();
});

test('ignores chat from clients that did not join', async () => {
  const anonymous = createClient();
  await anonymous.open;
  anonymous.send({ type: 'chat', text: 'should not be delivered' });
  anonymous.send({ type: 'signal', to: 'x', data: { candidate: {} } });

  const alice = createClient();
  await join(alice, 'lobby', 'Alice');
  anonymous.send({ type: 'chat', text: 'still not delivered' });

  await new Promise((resolve) => setTimeout(resolve, 150));
  await assert.rejects(
    alice.waitFor((msg) => msg.type === 'chat', 300),
    /timed out/,
  );

  alice.close();
  anonymous.close();
});

async function apiPost(path, body, token) {
  return fetch(`${baseUrl}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
}

async function apiGet(path, token) {
  return fetch(`${baseUrl}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
}

async function registerPhoneAccount(phone, name = '手机用户') {
  const response = await apiPost('/api/auth/register/phone', {
    phone,
    name,
    password: 'secret123',
  });
  assert.equal(response.status, 201);
  return response.json();
}

async function registerDeviceAccount(name = 'Pad') {
  const response = await apiPost('/api/auth/register/device', {
    name,
    password: 'secret123',
  });
  assert.equal(response.status, 201);
  return response.json();
}

async function makeActiveContact(device, phone) {
  const request = await apiPost(
    '/api/contacts/request',
    { phone: phone.user.phone },
    device.token,
  );
  assert.equal(request.status, 201);
  const body = await request.json();
  const confirm = await apiPost(
    `/api/contacts/${body.contact.id}/confirm`,
    {},
    phone.token,
  );
  assert.equal(confirm.status, 200);
  return body.contact;
}

test('auth API persists accounts and enforces contact rules', async () => {
  const phone = await registerPhoneAccount('13600136000', '手机甲');
  const device = await registerDeviceAccount('Pad甲');
  assert.match(device.user.deviceNumber, /^\d{12}$/);

  const me = await apiGet('/api/me', phone.token);
  assert.equal(me.status, 200);
  assert.equal((await me.json()).user.id, phone.user.id);

  const unauthorized = await apiGet('/api/contacts');
  assert.equal(unauthorized.status, 401);

  // A phone user must add by 12-digit device number.
  const wrong = await apiPost(
    '/api/contacts/request',
    { phone: '13600136000' },
    phone.token,
  );
  assert.equal(wrong.status, 400);

  const request = await apiPost(
    '/api/contacts/request',
    { phone: phone.user.phone },
    device.token,
  );
  assert.equal(request.status, 201);
  const requestBody = await request.json();
  assert.equal(requestBody.contact.status, 'pending');
  assert.equal(requestBody.contact.user.id, phone.user.id);

  const phoneView = await (await apiGet('/api/contacts', phone.token)).json();
  assert.equal(phoneView.incoming.length, 1);
  assert.equal(phoneView.incoming[0].user.deviceNumber, device.user.deviceNumber);
  assert.equal(phoneView.incoming[0].user.online, false);

  const confirmed = await apiPost(
    `/api/contacts/${requestBody.contact.id}/confirm`,
    {},
    phone.token,
  );
  assert.equal(confirmed.status, 200);

  const deviceView = await (await apiGet('/api/contacts', device.token)).json();
  assert.equal(deviceView.contacts.length, 1);
  assert.equal(deviceView.contacts[0].user.phone, phone.user.phone);
  assert.equal(deviceView.contacts[0].status, 'active');
});

test('authenticated websockets, presence, contact events and call invites', async () => {
  const phone = await registerPhoneAccount('13500135000', '手机乙');
  const device = await registerDeviceAccount('Pad乙');
  await makeActiveContact(device, phone);

  const phoneWs = createClient();
  await phoneWs.open;
  phoneWs.send({ type: 'auth', token: phone.token });
  const phoneAuth = await phoneWs.waitFor((msg) => msg.type === 'auth-ok' || msg.type === 'auth-error');
  assert.equal(phoneAuth.type, 'auth-ok');
  assert.equal(phoneAuth.user.id, phone.user.id);

  const deviceWs = createClient();
  await deviceWs.open;
  deviceWs.send({ type: 'auth', token: device.token });
  const deviceAuth = await deviceWs.waitFor((msg) => msg.type === 'auth-ok' || msg.type === 'auth-error');
  assert.equal(deviceAuth.type, 'auth-ok');

  const presence = await phoneWs.waitFor(
    (msg) => msg.type === 'presence' && msg.userId === device.user.id,
  );
  assert.equal(presence.online, true);

  const contacts = await (await apiGet('/api/contacts', phone.token)).json();
  assert.equal(contacts.contacts[0].user.online, true);

  deviceWs.send({ type: 'call-invite', to: phone.user.id, room: 'call-websocket-test' });
  const invite = await phoneWs.waitFor((msg) => msg.type === 'call-invite');
  assert.equal(invite.from.id, device.user.id);
  assert.equal(invite.room, 'call-websocket-test');

  phoneWs.send({
    type: 'call-response',
    to: device.user.id,
    room: 'call-websocket-test',
    accepted: true,
  });
  const response = await deviceWs.waitFor((msg) => msg.type === 'call-response');
  assert.equal(response.accepted, true);
  assert.equal(response.from.id, phone.user.id);

  phoneWs.close();
  deviceWs.close();
});

test('SMS verification registration and login with a console provider', async () => {
  const smsApp = await start({
    port: 0,
    host: '127.0.0.1',
    tls: false,
    dbPath: ':memory:',
    env: {
      MAX_PEERS: '2',
      SMS_ENABLED: '1',
      SMS_PROVIDER: 'console',
      SMS_DEV_SHOW_CODE: '1',
      SMS_SECRET: 'test-secret',
    },
    log: { info() {}, warn() {}, error() {} },
  });
  const smsBase = `http://127.0.0.1:${smsApp.port}`;
  const phone = '13300133000';

  async function post(path, body) {
    return fetch(`${smsBase}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  try {
    const config = await (await fetch(`${smsBase}/api/config`)).json();
    assert.equal(config.smsEnabled, true);
    assert.equal(config.smsDevShowCode, true);

    const send = await post('/api/auth/sms/send', { phone, purpose: 'register' });
    assert.equal(send.status, 200);
    const sendBody = await send.json();
    assert.match(sendBody.devCode, /^\d{6}$/);

    const wrong = await post('/api/auth/register/phone', {
      phone,
      name: '短信用户',
      password: 'secret123',
      code: '000000',
    });
    assert.equal(wrong.status, 400);

    const register = await post('/api/auth/register/phone', {
      phone,
      name: '短信用户',
      password: 'secret123',
      code: sendBody.devCode,
    });
    assert.equal(register.status, 201);
    const registered = await register.json();
    assert.equal(registered.user.phone, phone);

    const loginSend = await post('/api/auth/sms/send', { phone, purpose: 'login' });
    assert.equal(loginSend.status, 200);
    const loginCode = (await loginSend.json()).devCode;

    const login = await post('/api/auth/login/sms', { phone, code: loginCode });
    assert.equal(login.status, 200);
    const loginBody = await login.json();
    assert.ok(loginBody.token.length > 20);
    const me = await fetch(`${smsBase}/api/me`, {
      headers: { Authorization: `Bearer ${loginBody.token}` },
    });
    assert.equal((await me.json()).user.id, registered.user.id);

    const directRegister = await post('/api/auth/register/phone', {
      phone: '13300133001',
      name: '无验证码',
      password: 'secret123',
    });
    assert.equal(directRegister.status, 400);
  } finally {
    await smsApp.close();
  }
});
