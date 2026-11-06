'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { createStore, StoreError } = require('../lib/store');

function expectError(fn, code) {
  try {
    fn();
  } catch (err) {
    assert.ok(err instanceof StoreError, `expected StoreError, got ${err}`);
    assert.equal(err.code, code);
    return err;
  }
  assert.fail(`expected error ${code}`);
}

test('registers a device with a unique 12-digit number and logs in', () => {
  const store = createStore({ dbPath: ':memory:' });
  const first = store.registerDevice({ name: '客厅平板', password: 'secret123' });
  const second = store.registerDevice({ name: '书房平板', password: 'secret123' });

  assert.equal(first.user.role, 'device');
  assert.match(first.user.deviceNumber, /^\d{12}$/);
  assert.match(second.user.deviceNumber, /^\d{12}$/);
  assert.notEqual(first.user.deviceNumber, second.user.deviceNumber);
  assert.equal(first.user.phone, null);
  assert.ok(first.token.length > 20);

  const login = store.login({ identifier: first.user.deviceNumber, password: 'secret123' });
  assert.equal(login.user.id, first.user.id);
  expectError(
    () => store.login({ identifier: first.user.deviceNumber, password: 'wrong' }),
    'invalid-credentials',
  );
  assert.equal(store.getUserByToken(login.token).id, first.user.id);
  store.close();
});

test('registers phone users and validates input', () => {
  const store = createStore({ dbPath: ':memory:' });
  const result = store.registerPhone({
    phone: '138-0013-8000',
    name: '小明',
    password: 'secret123',
  });
  assert.equal(result.user.role, 'phone');
  assert.equal(result.user.phone, '13800138000');
  assert.equal(result.user.deviceNumber, null);
  expectError(
    () => store.registerPhone({ phone: '13800138000', name: '重复', password: 'secret123' }),
    'phone-taken',
  );
  expectError(
    () => store.registerPhone({ phone: '12345', name: 'x', password: 'secret123' }),
    'invalid-phone',
  );
  expectError(
    () => store.registerPhone({ phone: '13900139000', name: 'x', password: '123' }),
    'invalid-password',
  );
  const login = store.login({ identifier: '138 0013 8000', password: 'secret123' });
  assert.equal(login.user.id, result.user.id);
  store.close();
});

test('device user adds a phone user and the phone user confirms', () => {
  const store = createStore({ dbPath: ':memory:' });
  const device = store.registerDevice({ name: 'Pad', password: 'secret123' });
  const phone = store.registerPhone({ phone: '13800138000', name: '手机', password: 'secret123' });

  const request = store.requestContact(device.user.id, { phone: '13800138000' });
  assert.equal(request.contact.status, 'pending');
  assert.equal(request.contact.direction, 'outgoing');
  assert.equal(request.contact.user.id, phone.user.id);

  const phoneView = store.listContacts(phone.user.id);
  assert.equal(phoneView.incoming.length, 1);
  assert.equal(phoneView.incoming[0].user.deviceNumber, device.user.deviceNumber);

  // A different user cannot confirm it.
  const stranger = store.registerPhone({ phone: '13900139000', name: '路人', password: 'secret123' });
  expectError(() => store.confirmContact(stranger.user.id, request.contact.id), 'forbidden');

  const confirmed = store.confirmContact(phone.user.id, request.contact.id);
  assert.equal(confirmed.contact.status, 'active');

  const deviceView = store.listContacts(device.user.id);
  assert.equal(deviceView.contacts.length, 1);
  assert.equal(deviceView.contacts[0].user.phone, '13800138000');
  assert.equal(store.getActiveContactIds(device.user.id).length, 1);
  assert.ok(store.getActiveContact(device.user.id, phone.user.id));

  expectError(
    () => store.requestContact(device.user.id, { phone: '13800138000' }),
    'already-contacts',
  );
  store.close();
});

test('phone user adds a device by 12-digit number and device confirms', () => {
  const store = createStore({ dbPath: ':memory:' });
  const phone = store.registerPhone({ phone: '13800138000', name: '手机', password: 'secret123' });
  const device = store.registerDevice({ name: 'Pad', password: 'secret123' });

  const request = store.requestContact(phone.user.id, {
    deviceNumber: device.user.deviceNumber,
  });
  assert.equal(request.contact.status, 'pending');
  assert.equal(request.contact.user.id, device.user.id);

  const confirmed = store.confirmContact(device.user.id, request.contact.id);
  assert.equal(confirmed.contact.status, 'active');
  assert.equal(store.listContacts(phone.user.id).contacts[0].user.id, device.user.id);
  store.close();
});

test('mutual pending requests auto-activate and duplicate requests stay pending', () => {
  const store = createStore({ dbPath: ':memory:' });
  const device = store.registerDevice({ name: 'Pad', password: 'secret123' });
  const phone = store.registerPhone({ phone: '13800138000', name: '手机', password: 'secret123' });

  const first = store.requestContact(device.user.id, { phone: '13800138000' });
  expectError(
    () => store.requestContact(device.user.id, { phone: '13800138000' }),
    'request-pending',
  );

  const reverse = store.requestContact(phone.user.id, {
    deviceNumber: device.user.deviceNumber,
  });
  assert.equal(reverse.autoAccepted, true);
  assert.equal(reverse.contact.status, 'active');
  assert.equal(store.listContacts(device.user.id).contacts.length, 1);
  assert.equal(store.listContacts(phone.user.id).contacts.length, 1);
  expectError(
    () => store.confirmContact(phone.user.id, first.contact.id),
    'contact-not-pending',
  );
  store.close();
});

test('rejects invalid role/number combinations and unknown targets', () => {
  const store = createStore({ dbPath: ':memory:' });
  const phone = store.registerPhone({ phone: '13800138000', name: '手机', password: 'secret123' });
  const device = store.registerDevice({ name: 'Pad', password: 'secret123' });

  // A phone user adds by device number only; a phone number is not accepted.
  expectError(
    () => store.requestContact(phone.user.id, { deviceNumber: '123456789012' }),
    'user-not-found',
  );
  // A device user adds by phone only.
  expectError(
    () => store.requestContact(device.user.id, { phone: '13900139000' }),
    'user-not-found',
  );
  expectError(
    () => store.requestContact(phone.user.id, { deviceNumber: '123' }),
    'invalid-device-number',
  );
  store.close();
});

test('removes a contact and rejects pending requests', () => {
  const store = createStore({ dbPath: ':memory:' });
  const device = store.registerDevice({ name: 'Pad', password: 'secret123' });
  const phone = store.registerPhone({ phone: '13800138000', name: '手机', password: 'secret123' });

  const request = store.requestContact(device.user.id, { phone: '13800138000' });
  const rejected = store.rejectContact(phone.user.id, request.contact.id);
  assert.equal(rejected.contact.status, 'rejected');
  assert.equal(store.listContacts(phone.user.id).incoming.length, 0);

  const again = store.requestContact(device.user.id, { phone: '13800138000' });
  assert.equal(again.contact.status, 'pending');
  store.confirmContact(phone.user.id, again.contact.id);
  const removed = store.removeContact(device.user.id, again.contact.id);
  assert.equal(removed.otherId, phone.user.id);
  assert.equal(store.listContacts(device.user.id).contacts.length, 0);
  store.close();
});

test('persists users and contacts across store restarts', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'webrtc-store-'));
  const dbPath = path.join(dir, 'test.db');
  try {
    const store = createStore({ dbPath });
    const device = store.registerDevice({ name: 'Pad', password: 'secret123' });
    const phone = store.registerPhone({ phone: '13800138000', name: '手机', password: 'secret123' });
    const request = store.requestContact(device.user.id, { phone: '13800138000' });
    store.confirmContact(phone.user.id, request.contact.id);
    store.close();

    const reopened = createStore({ dbPath });
    const login = reopened.login({ identifier: device.user.deviceNumber, password: 'secret123' });
    assert.equal(login.user.id, device.user.id);
    assert.equal(reopened.listContacts(login.user.id).contacts.length, 1);
    reopened.close();
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
