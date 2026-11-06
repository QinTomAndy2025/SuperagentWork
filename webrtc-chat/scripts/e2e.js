'use strict';

/**
 * Browser end-to-end check for the WebRTC client.
 *
 * Opens two Chromium pages with fake camera/microphone devices, joins the same
 * room from both, then verifies:
 *   - peer connections reach "connected";
 *   - remote audio and video tracks arrive;
 *   - text chat reaches the other participant;
 *   - (with --relay) media uses TURN relay candidates, proving coturn works.
 *
 * Usage:
 *   BASE_URL=https://192.168.1.110:8443 node scripts/e2e.js
 *   BASE_URL=https://192.168.1.110:8443 node scripts/e2e.js --relay
 *
 * To test a domain before public DNS is ready, map it onto the server:
 *   HOST_RESOLVER_RULES="MAP www.shzhihuan.cn 192.168.1.110" \
 *   BASE_URL=https://www.shzhihuan.cn:8443 node scripts/e2e.js
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const BASE_URL = process.env.BASE_URL || process.argv[2] || 'http://127.0.0.1:8443';
const RELAY_ONLY = process.argv.includes('--relay');
const OUT_DIR = path.join(__dirname, '..', 'docs', 'screenshots');

function log(message) {
  console.log(`[e2e] ${message}`);
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const launchArgs = [
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    '--autoplay-policy=no-user-gesture-required',
    // WSL/containers often cannot resolve the mDNS host candidates Chromium
    // hides local IPs behind; use raw host candidates for deterministic tests.
    '--disable-features=WebRtcHideLocalIpsWithMdns',
  ];
  if (process.env.HOST_RESOLVER_RULES) {
    launchArgs.push(`--host-resolver-rules=${process.env.HOST_RESOLVER_RULES}`);
  }

  const browser = await chromium.launch({ args: launchArgs });

  const errors = [];
  const origin = new URL(BASE_URL).origin;

  async function openPage(label) {
    const context = await browser.newContext({
      ignoreHTTPSErrors: true,
      viewport: { width: 1440, height: 900 },
    });
    await context.grantPermissions(['camera', 'microphone'], { origin });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(`${label} pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`${label} console: ${message.text()}`);
    });
    return { context, page };
  }

  const room = `e2e-${Date.now()}`;
  const query = `room=${encodeURIComponent(room)}${RELAY_ONLY ? '&ice=relay' : ''}`;
  const url = `${BASE_URL}/?${query}`;

  const alice = await openPage('alice');
  const bob = await openPage('bob');

  await alice.page.goto(url);
  await alice.page.waitForSelector('#join-view:not([hidden])');
  await alice.page.fill('#name-input', 'Alice');
  await alice.page.screenshot({ path: path.join(OUT_DIR, '01-join-screen.png') });
  await alice.page.click('#join-btn');
  await alice.page.waitForSelector('#room-view:not([hidden])');

  await bob.page.goto(url);
  await bob.page.waitForSelector('#join-view:not([hidden])');
  await bob.page.fill('#name-input', 'Bob');
  await bob.page.click('#join-btn');
  await bob.page.waitForSelector('#room-view:not([hidden])');

  await alice.page.waitForFunction(
    () => {
      const state = window.__webrtcDebug.state();
      return state.peers.length === 1 && state.peers[0].connectionState === 'connected';
    },
    null,
    { timeout: 45000 },
  );

  await Promise.all([
    alice.page.waitForFunction(
      () => {
        const peer = window.__webrtcDebug.state().peers[0];
        return peer && peer.remote && peer.remote.videoWidth > 0 && peer.remote.videoTracks >= 1 && peer.remote.audioTracks >= 1;
      },
      null,
      { timeout: 45000 },
    ),
    bob.page.waitForFunction(
      () => {
        const peer = window.__webrtcDebug.state().peers[0];
        return peer && peer.remote && peer.remote.videoWidth > 0 && peer.remote.videoTracks >= 1 && peer.remote.audioTracks >= 1;
      },
      null,
      { timeout: 45000 },
    ),
  ]);

  const chatText = `你好，Bob！e2e-${Date.now()}`;
  await alice.page.fill('#chat-input', chatText);
  await alice.page.press('#chat-input', 'Enter');
  await bob.page.waitForFunction(
    (text) => [...document.querySelectorAll('.msg .bubble')].some((el) => el.textContent === text),
    chatText,
    { timeout: 10000 },
  );
  await alice.page.waitForFunction(
    (text) => [...document.querySelectorAll('.msg .bubble')].some((el) => el.textContent === text),
    chatText,
    { timeout: 10000 },
  );

  const replyText = '收到，音视频都正常！';
  await bob.page.fill('#chat-input', replyText);
  await bob.page.press('#chat-input', 'Enter');
  await alice.page.waitForFunction(
    (text) => [...document.querySelectorAll('.msg .bubble')].some((el) => el.textContent === text),
    replyText,
    { timeout: 10000 },
  );

  // Microphone and camera toggles should flip their pressed state without
  // tearing the media connection down.
  await alice.page.click('#mic-btn');
  await alice.page.waitForSelector('#mic-btn[aria-pressed="false"]');
  await alice.page.click('#cam-btn');
  await alice.page.waitForSelector('#cam-btn[aria-pressed="false"]');
  await bob.page.waitForFunction(
    () => {
      const state = window.__webrtcDebug.state();
      return state.peers[0] && state.peers[0].connectionState === 'connected' && state.peers[0].remote.videoTracks >= 1;
    },
    null,
    { timeout: 10000 },
  );
  await alice.page.click('#mic-btn');
  await alice.page.click('#cam-btn');
  await alice.page.waitForSelector('#cam-btn[aria-pressed="true"]');

  const aliceState = await alice.page.evaluate(() => window.__webrtcDebug.state());
  const bobState = await bob.page.evaluate(() => window.__webrtcDebug.state());

  let relayPairs = null;
  if (RELAY_ONLY) {
    relayPairs = await alice.page.evaluate(() => window.__webrtcDebug.selectedCandidatePairs());
    const nonRelay = relayPairs.filter((pair) => pair.localType !== 'relay');
    if (nonRelay.length > 0) {
      throw new Error(`expected relay candidates only, got ${JSON.stringify(relayPairs)}`);
    }
  }

  await alice.page.screenshot({ path: path.join(OUT_DIR, '02-call-and-chat.png') });
  await bob.page.screenshot({ path: path.join(OUT_DIR, '03-call-and-chat-other-side.png') });
  await bob.page.close();
  await alice.page.close();

  await browser.close();

  if (errors.length > 0) {
    throw new Error(`browser errors:\n${errors.join('\n')}`);
  }

  const summary = {
    room,
    relayOnly: RELAY_ONLY,
    alice: aliceState,
    bob: bobState,
    relayPairs,
    screenshots: [
      'docs/screenshots/01-join-screen.png',
      'docs/screenshots/02-call-and-chat.png',
      'docs/screenshots/03-call-and-chat-other-side.png',
    ],
  };
  console.log(JSON.stringify(summary, null, 2));
  log('PASS: two peers connected, media flowed and chat was delivered');
}

main().catch((error) => {
  console.error(`[e2e] FAIL: ${error.stack || error.message}`);
  process.exit(1);
});
