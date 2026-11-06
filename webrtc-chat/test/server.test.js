'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert');
const { WebSocket } = require('ws');
const { start } = require('../server');

let app;
let baseUrl;

before(async () => {
  app = await start({
    port: 0,
    host: '127.0.0.1',
    tls: false,
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
  assert.match(await page.text(), /WebRTC/);

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
