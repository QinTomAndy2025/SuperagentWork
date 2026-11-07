'use strict';

/**
 * SMS verification codes.
 *
 * Providers:
 *   - console (default, development): prints the code to the server log.
 *   - aliyun: Alibaba Cloud SMS (Dysmsapi 2017-05-25) via env credentials.
 *
 * Production requires SMS_ENABLED=1, SMS_PROVIDER=aliyun and the Aliyun
 * AccessKey / sign name / template code environment variables.
 */

const crypto = require('crypto');

class SmsError extends Error {
  constructor(code, message, status = 502) {
    super(message);
    this.name = 'SmsError';
    this.code = code;
    this.status = status;
  }
}

function isTruthy(value) {
  return /^(1|true|yes|on)$/i.test(String(value == null ? '' : value).trim());
}

function aliyunEncode(value) {
  return encodeURIComponent(String(value)).replace(/[!'()*]/g, (char) =>
    `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

async function sendViaAliyun({ phone, code, env, log }) {
  const accessKeyId = env.ALIYUN_SMS_ACCESS_KEY_ID;
  const accessKeySecret = env.ALIYUN_SMS_ACCESS_KEY_SECRET;
  const signName = env.ALIYUN_SMS_SIGN_NAME;
  const templateCode = env.ALIYUN_SMS_TEMPLATE_CODE;
  if (!accessKeyId || !accessKeySecret || !signName || !templateCode) {
    throw new SmsError(
      'sms-not-configured',
      '短信服务未配置完整（缺少阿里云 AccessKey、签名或模板）',
      503,
    );
  }

  const params = {
    AccessKeyId: accessKeyId,
    Action: 'SendSms',
    Format: 'JSON',
    PhoneNumbers: phone,
    RegionId: env.ALIYUN_SMS_REGION_ID || 'cn-hangzhou',
    SignName: signName,
    SignatureMethod: 'HMAC-SHA1',
    SignatureNonce: crypto.randomUUID(),
    SignatureVersion: '1.0',
    TemplateCode: templateCode,
    TemplateParam: JSON.stringify({ code }),
    Timestamp: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
    Version: '2017-05-25',
  };

  const canonical = Object.keys(params)
    .sort()
    .map((key) => `${aliyunEncode(key)}=${aliyunEncode(params[key])}`)
    .join('&');
  const stringToSign = `GET&${aliyunEncode('/')}&${aliyunEncode(canonical)}`;
  const signature = crypto
    .createHmac('sha1', `${accessKeySecret}&`)
    .update(stringToSign)
    .digest('base64');
  const url = `https://dysmsapi.aliyuncs.com/?${canonical}&Signature=${aliyunEncode(signature)}`;

  let response;
  try {
    response = await fetch(url, {
      method: 'GET',
      signal: AbortSignal.timeout(10000),
      headers: { Accept: 'application/json' },
    });
  } catch (err) {
    throw new SmsError('sms-network', `短信服务网络错误：${err.message}`, 502);
  }

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok || !data || data.Code !== 'OK') {
    const detail = data && (data.Message || data.Code);
    log.warn?.('[sms] aliyun send failed:', detail || `HTTP ${response.status}`);
    throw new SmsError('sms-send-failed', `短信发送失败${detail ? `：${detail}` : ''}`, 502);
  }
  return { provider: 'aliyun', requestId: data.RequestId };
}

function createSmsService(options = {}) {
  const env = options.env || process.env;
  const log = options.log || console;
  const enabled = isTruthy(env.SMS_ENABLED);
  const provider = String(env.SMS_PROVIDER || 'console').trim().toLowerCase();
  const devShowCode = isTruthy(env.SMS_DEV_SHOW_CODE);
  const secret = options.secret || env.SMS_SECRET || crypto.randomBytes(32).toString('hex');
  const ttlSeconds = Math.max(
    60,
    Math.min(Number(env.SMS_TTL_SECONDS) || 300, 15 * 60),
  );

  function hashCode(phone, purpose, code) {
    return crypto
      .createHmac('sha256', secret)
      .update(`${phone}:${purpose}:${code}`)
      .digest('hex');
  }

  function generateCode() {
    return String(crypto.randomInt(0, 1000000)).padStart(6, '0');
  }

  async function send(phone, code, purpose) {
    if (!enabled) {
      throw new SmsError('sms-disabled', '短信验证码服务未启用', 503);
    }
    if (provider === 'console') {
      log.info?.(`[sms] ${purpose} code for ${phone}: ${code}`);
      return { provider: 'console' };
    }
    if (provider === 'aliyun') {
      return sendViaAliyun({ phone, code, env, log });
    }
    throw new SmsError('sms-provider-unknown', `不支持的短信服务商：${provider}`, 503);
  }

  return {
    enabled,
    provider,
    devShowCode: devShowCode && provider === 'console',
    ttlSeconds,
    hashCode,
    generateCode,
    send,
  };
}

module.exports = { createSmsService, SmsError };
