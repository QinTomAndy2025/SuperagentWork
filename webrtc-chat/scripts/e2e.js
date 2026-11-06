'use strict';

/**
 * Browser end-to-end check for accounts, confirmed contacts and calls.
 *
 * Flow:
 *   - register a phone user and a device user (device number is generated);
 *   - phone adds the device by 12-digit number, device confirms;
 *   - phone calls the device, device accepts;
 *   - verify signaling, remote audio/video, chat and responsive layouts.
 *
 * Usage:
 *   BASE_URL=https://192.168.1.110:8443 node scripts/e2e.js
 *   BASE_URL=https://192.168.1.110:8443 node scripts/e2e.js --relay
 *   HOST_RESOLVER_RULES="MAP www.shzhihuan.cn 192.168.1.110" \
 *     BASE_URL=https://www.shzhihuan.cn:8443 node scripts/e2e.js
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

function randomPhone() {
  const tail = String(Date.now()).slice(-9);
  return `13${tail}`;
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const origin = new URL(BASE_URL).origin;

  const launchArgs = [
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    '--autoplay-policy=no-user-gesture-required',
    '--disable-features=WebRtcHideLocalIpsWithMdns',
  ];
  if (process.env.HOST_RESOLVER_RULES) {
    launchArgs.push(`--host-resolver-rules=${process.env.HOST_RESOLVER_RULES}`);
  }
  const browser = await chromium.launch({ args: launchArgs });
  const errors = [];

  async function openPage(label, viewport) {
    const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport });
    await context.grantPermissions(['camera', 'microphone'], { origin });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(`${label} pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`${label} console: ${message.text()}`);
    });
    return { context, page };
  }

  const phone = await openPage('phone', { width: 1440, height: 900 });
  const device = await openPage('device', { width: 1024, height: 768 });
  const phoneNumber = randomPhone();
  const query = RELAY_ONLY ? '?ice=relay' : '';
  const pageUrl = (path) => `${BASE_URL.replace(/\/$/, '')}${path}${query}`;

  // Landing chooser.
  await phone.page.goto(pageUrl('/'));
  await phone.page.waitForSelector('.landing-cards');
  await phone.page.screenshot({ path: path.join(OUT_DIR, '00-landing.png') });

  // Auth screenshot from the phone page.
  await phone.page.goto(pageUrl('/phone'));
  await phone.page.waitForSelector('#auth-view:not([hidden])');
  await phone.page.waitForSelector('#phone-panel:not([hidden])');
  await phone.page.screenshot({ path: path.join(OUT_DIR, '01-auth-desktop.png') });

  // Register the phone user.
  await phone.page.click('#phone-mode-register');
  await phone.page.fill('#phone-register-phone', phoneNumber);
  await phone.page.fill('#phone-register-name', '手机用户');
  await phone.page.fill('#phone-register-password', 'secret123');
  await phone.page.click('#phone-register-form button[type="submit"]');
  await phone.page.waitForSelector('#app-view:not([hidden])');
  log(`registered phone ${phoneNumber}`);

  // Register the device user on its own page and capture the 12-digit number.
  await device.page.goto(pageUrl('/device'));
  await device.page.waitForSelector('#auth-view:not([hidden])');
  await device.page.waitForSelector('#device-panel:not([hidden])');
  await device.page.click('#device-mode-register');
  await device.page.fill('#device-register-name', '客厅平板');
  await device.page.fill('#device-register-password', 'secret123');
  await device.page.click('#device-register-form button[type="submit"]');
  await device.page.waitForSelector('#device-register-success:not([hidden])');
  const deviceNumber = (await device.page.textContent('#device-number-display')).trim();
  if (!/^\d{12}$/.test(deviceNumber)) throw new Error(`bad device number: ${deviceNumber}`);
  await device.page.screenshot({ path: path.join(OUT_DIR, '02-device-register.png') });
  await device.page.click('#device-enter-app');
  await device.page.waitForSelector('#app-view:not([hidden])');
  log(`registered device ${deviceNumber}`);

  // Phone adds the device by its 12-digit number.
  await phone.page.fill('#add-contact-input', deviceNumber);
  await phone.page.click('#add-contact-form button[type="submit"]');
  await phone.page.waitForFunction(
    () => document.querySelector('#outgoing-count').textContent === '1',
  );

  // Device confirms; both sides reach an active contact.
  await device.page.waitForSelector('#incoming-list .contact-item');
  await device.page.click('#incoming-list .contact-item .accept');
  await device.page.waitForFunction(
    () => document.querySelector('#contacts-count').textContent === '1',
  );
  await phone.page.waitForFunction(
    () => document.querySelector('#contacts-count').textContent === '1',
  );
  await phone.page.waitForSelector('#contacts-list .online-dot.online');

  // Phone starts the call; device accepts.
  await device.page.screenshot({ path: path.join(OUT_DIR, '03-contacts-tablet.png') });
  await phone.page.click('#contacts-list .contact-call');
  await phone.page.waitForSelector('#room-view:not([hidden])');
  await device.page.waitForSelector('#incoming-call:not([hidden])');
  await device.page.click('#accept-call');
  await device.page.waitForSelector('#room-view:not([hidden])');

  await Promise.all([
    phone.page.waitForFunction(
      () => {
        const state = window.__webrtcDebug.state();
        return state.peers.length === 1 && state.peers[0].connectionState === 'connected';
      },
      null,
      { timeout: 45000 },
    ),
    device.page.waitForFunction(
      () => {
        const state = window.__webrtcDebug.state();
        return state.peers.length === 1 && state.peers[0].connectionState === 'connected';
      },
      null,
      { timeout: 45000 },
    ),
  ]);

  await Promise.all([
    phone.page.waitForFunction(
      () => {
        const peer = window.__webrtcDebug.state().peers[0];
        return peer && peer.remote && peer.remote.videoWidth > 0 && peer.remote.videoTracks >= 1 && peer.remote.audioTracks >= 1;
      },
      null,
      { timeout: 45000 },
    ),
    device.page.waitForFunction(
      () => {
        const peer = window.__webrtcDebug.state().peers[0];
        return peer && peer.remote && peer.remote.videoWidth > 0 && peer.remote.videoTracks >= 1 && peer.remote.audioTracks >= 1;
      },
      null,
      { timeout: 45000 },
    ),
  ]);
  log('both peers connected with audio + video');

  const chatText = `你好，设备 ${Date.now()}`;
  await phone.page.fill('#chat-input', chatText);
  await phone.page.press('#chat-input', 'Enter');
  await device.page.waitForFunction(
    (text) => [...document.querySelectorAll('.bubble')].some((el) => el.textContent === text),
    chatText,
    { timeout: 10000 },
  );

  // Let transient toasts fade before capturing the call screenshots.
  await phone.page.waitForTimeout(3000);

  // Microphone / camera toggles must not drop the media connection.
  await phone.page.click('#mic-btn');
  await phone.page.waitForSelector('#mic-btn[aria-pressed="false"]');
  await phone.page.click('#mic-btn');
  await phone.page.waitForSelector('#mic-btn[aria-pressed="true"]');

  await phone.page.screenshot({ path: path.join(OUT_DIR, '04-call-phone.png') });
  await device.page.screenshot({ path: path.join(OUT_DIR, '05-call-device-tablet-landscape.png') });

  // Responsive evidence: phone landscape and pad portrait.
  await phone.page.setViewportSize({ width: 844, height: 390 });
  await phone.page.waitForTimeout(300);
  await phone.page.screenshot({ path: path.join(OUT_DIR, '06-call-phone-landscape.png') });
  const phoneOverflow = await phone.page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
    viewportWidth: innerWidth,
    viewportHeight: innerHeight,
  }));

  await device.page.setViewportSize({ width: 768, height: 1024 });
  await device.page.waitForTimeout(300);
  await device.page.screenshot({ path: path.join(OUT_DIR, '07-call-pad-portrait.png') });
  const deviceOverflow = await device.page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
    viewportWidth: innerWidth,
    viewportHeight: innerHeight,
  }));

  let relayPairs = null;
  if (RELAY_ONLY) {
    relayPairs = await phone.page.evaluate(() => window.__webrtcDebug.selectedCandidatePairs());
    if (relayPairs.some((pair) => pair.localType !== 'relay')) {
      throw new Error(`expected relay candidates only, got ${JSON.stringify(relayPairs)}`);
    }
  }

  const phoneState = await phone.page.evaluate(() => window.__webrtcDebug.state());
  const deviceState = await device.page.evaluate(() => window.__webrtcDebug.state());

  await phone.context.close();
  await device.context.close();
  await browser.close();

  if (errors.length > 0) throw new Error(`browser errors:\n${errors.join('\n')}`);
  if (phoneOverflow.width > phoneOverflow.viewportWidth) {
    throw new Error(`phone landscape overflows horizontally: ${JSON.stringify(phoneOverflow)}`);
  }
  if (deviceOverflow.width > deviceOverflow.viewportWidth) {
    throw new Error(`pad portrait overflows horizontally: ${JSON.stringify(deviceOverflow)}`);
  }

  console.log(
    JSON.stringify(
      {
        phoneNumber,
        deviceNumber,
        relayOnly: RELAY_ONLY,
        relayPairs,
        phone: phoneState,
        device: deviceState,
        phoneOverflow,
        deviceOverflow,
        screenshots: fs
          .readdirSync(OUT_DIR)
          .filter((name) => name.endsWith('.png'))
          .sort(),
      },
      null,
      2,
    ),
  );
  log('PASS: accounts, confirmed contact and two-way call verified');
}

main().catch((error) => {
  console.error(`[e2e] FAIL: ${error.stack || error.message}`);
  process.exit(1);
});
