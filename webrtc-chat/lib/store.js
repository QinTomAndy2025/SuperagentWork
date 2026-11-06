'use strict';

/**
 * SQLite-backed accounts, sessions and contact relationships.
 *
 * Uses Node's built-in `node:sqlite` (Node >= 22.5), so there is no native
 * dependency to compile on the deployment host.
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { DatabaseSync } = require('node:sqlite');

const TOKEN_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const PHONE_PATTERN = /^1[3-9]\d{9}$/;
const DEVICE_NUMBER_PATTERN = /^\d{12}$/;

class StoreError extends Error {
  constructor(code, message, status = 400) {
    super(message);
    this.name = 'StoreError';
    this.code = code;
    this.status = status;
  }
}

function normalizePhone(value) {
  const phone = String(value == null ? '' : value).replace(/[\s-]/g, '');
  if (!PHONE_PATTERN.test(phone)) {
    throw new StoreError('invalid-phone', '请输入有效的 11 位手机号');
  }
  return phone;
}

function normalizeDeviceNumber(value) {
  const number = String(value == null ? '' : value).replace(/[\s-]/g, '');
  if (!DEVICE_NUMBER_PATTERN.test(number)) {
    throw new StoreError('invalid-device-number', '请输入 12 位设备号');
  }
  return number;
}

function normalizeName(value) {
  const name = String(value == null ? '' : value).trim();
  if (name.length < 1 || name.length > 32) {
    throw new StoreError('invalid-name', '昵称长度需为 1–32 个字符');
  }
  return name;
}

function normalizePassword(value) {
  if (typeof value !== 'string' || value.length < 6 || value.length > 128) {
    throw new StoreError('invalid-password', '密码长度需为 6–128 位');
  }
  return value;
}

function secretHash(value) {
  return crypto.createHash('sha256').update(String(value)).digest('hex');
}

function hashPassword(password) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(password, salt, 64).toString('hex');
  return { salt, hash };
}

function verifyPassword(password, salt, expectedHash) {
  const computed = crypto.scryptSync(password, salt, 64);
  const expected = Buffer.from(String(expectedHash), 'hex');
  return computed.length === expected.length && crypto.timingSafeEqual(computed, expected);
}

function pairKeyFor(a, b) {
  return a < b ? `${a}:${b}` : `${b}:${a}`;
}

function publicUser(row) {
  if (!row) return null;
  return {
    id: row.id,
    role: row.role,
    name: row.name,
    phone: row.role === 'phone' ? row.phone : null,
    deviceNumber: row.role === 'device' ? row.device_number : null,
    createdAt: row.created_at,
  };
}

function publicContact(row, viewerId) {
  return {
    id: row.id,
    status: row.status,
    direction: row.requester_id === viewerId ? 'outgoing' : 'incoming',
    createdAt: row.created_at,
    respondedAt: row.responded_at,
    user: publicUser({
      id: row.other_id,
      role: row.other_role,
      name: row.other_name,
      phone: row.other_phone,
      device_number: row.other_device_number,
      created_at: row.other_created_at,
    }),
  };
}

function createStore(options = {}) {
  const dbPath =
    options.dbPath || process.env.DB_PATH || path.join(__dirname, '..', 'data', 'webrtc-chat.db');
  if (dbPath !== ':memory:') {
    fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  }

  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA foreign_keys = ON;');
  if (dbPath !== ':memory:') {
    try {
      db.exec('PRAGMA journal_mode = WAL;');
    } catch {
      // Not fatal; some filesystems do not support WAL.
    }
  }

  db.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id TEXT PRIMARY KEY,
      role TEXT NOT NULL CHECK (role IN ('phone', 'device')),
      phone TEXT UNIQUE,
      device_number TEXT UNIQUE,
      name TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      password_salt TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      last_login_at INTEGER,
      CHECK (
        (role = 'phone' AND phone IS NOT NULL AND device_number IS NULL) OR
        (role = 'device' AND device_number IS NOT NULL AND phone IS NULL)
      )
    );

    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY,
      user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at INTEGER NOT NULL,
      expires_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

    CREATE TABLE IF NOT EXISTS contacts (
      id TEXT PRIMARY KEY,
      pair_key TEXT NOT NULL UNIQUE,
      requester_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      addressee_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'rejected')),
      created_at INTEGER NOT NULL,
      responded_at INTEGER,
      CHECK (requester_id <> addressee_id)
    );
    CREATE INDEX IF NOT EXISTS idx_contacts_requester ON contacts(requester_id, status);
    CREATE INDEX IF NOT EXISTS idx_contacts_addressee ON contacts(addressee_id, status);
  `);

  const stmts = {
    insertUser: db.prepare(
      `INSERT INTO users (id, role, phone, device_number, name, password_hash, password_salt, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    ),
    userById: db.prepare('SELECT * FROM users WHERE id = ?'),
    userByPhone: db.prepare('SELECT * FROM users WHERE phone = ?'),
    userByDevice: db.prepare('SELECT * FROM users WHERE device_number = ?'),
    updateLogin: db.prepare('UPDATE users SET last_login_at = ? WHERE id = ?'),
    insertSession: db.prepare(
      'INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)',
    ),
    deleteSession: db.prepare('DELETE FROM sessions WHERE token_hash = ?'),
    sessionUser: db.prepare(
      `SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
       WHERE s.token_hash = ? AND s.expires_at > ?`,
    ),
    insertContact: db.prepare(
      `INSERT INTO contacts (id, pair_key, requester_id, addressee_id, status, created_at, responded_at)
       VALUES (?, ?, ?, ?, 'pending', ?, NULL)`,
    ),
    contactById: db.prepare('SELECT * FROM contacts WHERE id = ?'),
    contactByPair: db.prepare('SELECT * FROM contacts WHERE pair_key = ?'),
    contactsByUser: db.prepare(
      `SELECT c.*,
              u.id AS other_id, u.role AS other_role, u.name AS other_name,
              u.phone AS other_phone, u.device_number AS other_device_number,
              u.created_at AS other_created_at
       FROM contacts c
       JOIN users u ON u.id = CASE WHEN c.requester_id = ? THEN c.addressee_id ELSE c.requester_id END
       WHERE c.requester_id = ? OR c.addressee_id = ?
       ORDER BY c.created_at DESC`,
    ),
    contactWithOther: db.prepare(
      `SELECT c.*,
              u.id AS other_id, u.role AS other_role, u.name AS other_name,
              u.phone AS other_phone, u.device_number AS other_device_number,
              u.created_at AS other_created_at
       FROM contacts c
       JOIN users u ON u.id = CASE WHEN c.requester_id = ? THEN c.addressee_id ELSE c.requester_id END
       WHERE c.id = ?`,
    ),
    activateContact: db.prepare(
      "UPDATE contacts SET status = 'active', responded_at = ? WHERE id = ? AND status = 'pending'",
    ),
    rejectContact: db.prepare(
      "UPDATE contacts SET status = 'rejected', responded_at = ? WHERE id = ? AND status = 'pending'",
    ),
    resetContact: db.prepare(
      `UPDATE contacts SET requester_id = ?, addressee_id = ?, status = 'pending', created_at = ?, responded_at = NULL
       WHERE id = ?`,
    ),
    deleteContact: db.prepare('DELETE FROM contacts WHERE id = ?'),
    activeContactIds: db.prepare(
      `SELECT CASE WHEN requester_id = ? THEN addressee_id ELSE requester_id END AS other_id
       FROM contacts
       WHERE status = 'active' AND (requester_id = ? OR addressee_id = ?)`,
    ),
    activeContact: db.prepare(
      "SELECT * FROM contacts WHERE pair_key = ? AND status = 'active'",
    ),
    countUsers: db.prepare('SELECT COUNT(*) AS count FROM users'),
    countContacts: db.prepare("SELECT COUNT(*) AS count FROM contacts WHERE status = 'active'"),
  };

  function transaction(fn) {
    db.exec('BEGIN');
    try {
      const result = fn();
      db.exec('COMMIT');
      return result;
    } catch (err) {
      try {
        db.exec('ROLLBACK');
      } catch {
        // ignore rollback errors
      }
      throw err;
    }
  }

  function createSession(userId, now = Date.now()) {
    const token = crypto.randomBytes(32).toString('base64url');
    stmts.insertSession.run(secretHash(token), userId, now, now + TOKEN_TTL_MS);
    return token;
  }

  function generateDeviceNumber() {
    for (let attempt = 0; attempt < 25; attempt += 1) {
      const candidate = String(crypto.randomInt(100000000000, 1000000000000));
      if (!stmts.userByDevice.get(candidate)) return candidate;
    }
    throw new StoreError('device-number-exhausted', '设备号生成失败，请重试', 500);
  }

  function registerPhone(input = {}) {
    const phone = normalizePhone(input.phone);
    const name = normalizeName(input.name);
    const password = normalizePassword(input.password);
    if (stmts.userByPhone.get(phone)) {
      throw new StoreError('phone-taken', '该手机号已注册', 409);
    }
    const { salt, hash } = hashPassword(password);
    const id = crypto.randomUUID();
    const now = Date.now();
    const token = transaction(() => {
      stmts.insertUser.run(id, 'phone', phone, null, name, hash, salt, now);
      return createSession(id, now);
    });
    return {
      user: publicUser(stmts.userById.get(id)),
      token,
    };
  }

  function registerDevice(input = {}) {
    const name = normalizeName(input.name);
    const password = normalizePassword(input.password);
    const { salt, hash } = hashPassword(password);
    const id = crypto.randomUUID();
    const now = Date.now();
    const deviceNumber = generateDeviceNumber();
    const token = transaction(() => {
      stmts.insertUser.run(id, 'device', null, deviceNumber, name, hash, salt, now);
      return createSession(id, now);
    });
    return {
      user: publicUser(stmts.userById.get(id)),
      token,
    };
  }

  function login(input = {}) {
    const identifier = String(input.identifier == null ? '' : input.identifier)
      .trim()
      .replace(/[\s-]/g, '');
    const password = typeof input.password === 'string' ? input.password : '';
    let row = null;
    if (PHONE_PATTERN.test(identifier)) {
      row = stmts.userByPhone.get(identifier);
    } else if (DEVICE_NUMBER_PATTERN.test(identifier)) {
      row = stmts.userByDevice.get(identifier);
    }
    if (!row || !verifyPassword(password, row.password_salt, row.password_hash)) {
      throw new StoreError('invalid-credentials', '账号或密码错误', 401);
    }
    const now = Date.now();
    stmts.updateLogin.run(now, row.id);
    return {
      user: publicUser(row),
      token: createSession(row.id, now),
    };
  }

  function logout(token) {
    if (typeof token === 'string' && token) {
      stmts.deleteSession.run(secretHash(token));
    }
  }

  function getUserByToken(token) {
    if (typeof token !== 'string' || token.length < 20) return null;
    const tokenHash = secretHash(token);
    const row = stmts.sessionUser.get(tokenHash, Date.now());
    if (!row) {
      stmts.deleteSession.run(tokenHash);
      return null;
    }
    return publicUser(row);
  }

  function getUserById(userId) {
    return publicUser(stmts.userById.get(userId));
  }

  function listContacts(userId) {
    const rows = stmts.contactsByUser.all(userId, userId, userId);
    const items = rows.map((row) => publicContact(row, userId));
    return {
      contacts: items.filter((item) => item.status === 'active'),
      incoming: items.filter((item) => item.status === 'pending' && item.direction === 'incoming'),
      outgoing: items.filter((item) => item.status === 'pending' && item.direction === 'outgoing'),
    };
  }

  function requestContact(userId, input = {}) {
    const me = stmts.userById.get(userId);
    if (!me) throw new StoreError('unauthorized', '登录状态已失效', 401);

    let target = null;
    if (me.role === 'phone') {
      const deviceNumber = normalizeDeviceNumber(input.deviceNumber);
      target = stmts.userByDevice.get(deviceNumber);
      if (!target || target.role !== 'device') {
        throw new StoreError('user-not-found', '未找到该设备号', 404);
      }
    } else {
      const phone = normalizePhone(input.phone);
      target = stmts.userByPhone.get(phone);
      if (!target) {
        throw new StoreError('user-not-found', '未找到该手机号', 404);
      }
    }

    if (target.id === userId) {
      throw new StoreError('self-contact', '不能添加自己为联系人');
    }

    const key = pairKeyFor(userId, target.id);
    const existing = stmts.contactByPair.get(key);
    const now = Date.now();
    let autoAccepted = false;
    let contactId = existing ? existing.id : null;

    if (existing) {
      if (existing.status === 'active') {
        throw new StoreError('already-contacts', '你们已经是联系人', 409);
      }
      if (existing.status === 'pending') {
        if (existing.addressee_id === userId) {
          // The other side already asked: both intended to connect, so confirm.
          stmts.activateContact.run(now, existing.id);
          autoAccepted = true;
        } else {
          throw new StoreError('request-pending', '请求已发送，等待对方确认', 409);
        }
      } else {
        stmts.resetContact.run(userId, target.id, now, existing.id);
      }
    } else {
      contactId = crypto.randomUUID();
      try {
        stmts.insertContact.run(contactId, key, userId, target.id, now);
      } catch (err) {
        // A concurrent request won the race; treat it as pending.
        const raced = stmts.contactByPair.get(key);
        if (!raced) throw err;
        contactId = raced.id;
        if (raced.status === 'active') {
          throw new StoreError('already-contacts', '你们已经是联系人', 409);
        }
        if (raced.status === 'pending' && raced.addressee_id === userId) {
          stmts.activateContact.run(now, raced.id);
          autoAccepted = true;
        } else {
          throw new StoreError('request-pending', '请求已发送，等待对方确认', 409);
        }
      }
    }

    const row = stmts.contactWithOther.get(userId, contactId);
    const contact = row ? publicContact(row, userId) : null;
    return {
      autoAccepted,
      contact,
      otherId: target.id,
      requester: publicUser(me),
      target: publicUser(target),
    };
  }

  function respondContact(userId, contactId, action) {
    const row = stmts.contactById.get(contactId);
    if (!row) throw new StoreError('contact-not-found', '请求不存在', 404);
    if (row.addressee_id !== userId) {
      throw new StoreError('forbidden', '只能处理发给自己的请求', 403);
    }
    if (row.status !== 'pending') {
      throw new StoreError('contact-not-pending', '该请求已处理', 409);
    }
    const now = Date.now();
    if (action === 'confirm') {
      stmts.activateContact.run(now, contactId);
    } else {
      stmts.rejectContact.run(now, contactId);
    }
    const updated = stmts.contactWithOther.get(userId, contactId);
    return {
      contact: updated ? publicContact(updated, userId) : null,
      otherId: row.requester_id,
    };
  }

  function confirmContact(userId, contactId) {
    return respondContact(userId, contactId, 'confirm');
  }

  function rejectContact(userId, contactId) {
    return respondContact(userId, contactId, 'reject');
  }

  function removeContact(userId, contactId) {
    const row = stmts.contactById.get(contactId);
    if (!row) throw new StoreError('contact-not-found', '联系人不存在', 404);
    if (row.requester_id !== userId && row.addressee_id !== userId) {
      throw new StoreError('forbidden', '无权操作该联系人', 403);
    }
    stmts.deleteContact.run(contactId);
    return { otherId: row.requester_id === userId ? row.addressee_id : row.requester_id };
  }

  function getActiveContactIds(userId) {
    return stmts.activeContactIds.all(userId, userId, userId).map((row) => row.other_id);
  }

  function getActiveContact(userId, otherId) {
    return stmts.activeContact.get(pairKeyFor(userId, otherId)) || null;
  }

  function stats() {
    return {
      users: stmts.countUsers.get().count,
      contacts: stmts.countContacts.get().count,
    };
  }

  function close() {
    db.close();
  }

  return {
    registerPhone,
    registerDevice,
    login,
    logout,
    getUserByToken,
    getUserById,
    listContacts,
    requestContact,
    confirmContact,
    rejectContact,
    removeContact,
    getActiveContactIds,
    getActiveContact,
    stats,
    close,
  };
}

module.exports = { createStore, StoreError };
