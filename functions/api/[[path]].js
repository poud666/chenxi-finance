/**
 * 股市预警 · Pages Functions API
 * 整个 /api/* 走这个 catchall，避免被国内限制的 workers.dev
 *
 * 与原 worker/index.js 功能完全一致，仅适配 Pages Functions 入口
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin':  '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  'Access-Control-Allow-Credentials': 'true',
};
const JSON_HEADERS = { ...CORS_HEADERS, 'Content-Type': 'application/json; charset=utf-8' };

const json  = (data, status=200) => new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
const error = (msg, status=400)  => json({ error: msg }, status);
const uuid  = () => crypto.randomUUID();
const now   = () => new Date().toISOString();
const isValidEmail = (e) => typeof e === 'string' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e);
const genCode = () => String(Math.floor(100000 + Math.random() * 900000));

async function sendEmail(env, { to, subject, html }) {
  const r = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${env.RESEND_API_KEY}`,
      'Content-Type':  'application/json',
    },
    body: JSON.stringify({ from: env.FROM_EMAIL, to: [to], subject, html }),
  });
  if (!r.ok) throw new Error(`Resend ${r.status}: ${await r.text()}`);
  return r.json();
}

const loginCodeTemplate = (code) => `
<div style="font-family:-apple-system,'PingFang SC',sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#fff;color:#222">
  <h2 style="color:#c0392b;border-bottom:2px solid #c0392b;padding-bottom:8px">股市预警 · 登录验证码</h2>
  <p>你的登录验证码是：</p>
  <div style="font-size:32px;font-weight:700;letter-spacing:8px;text-align:center;background:#f8f8f8;padding:20px;border-radius:8px;color:#c0392b;margin:20px 0">${code}</div>
  <p style="color:#666;font-size:14px">10 分钟内有效。如果不是你本人操作，请忽略此邮件。</p>
  <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
  <p style="color:#999;font-size:12px">本邮件由系统自动发送，请勿回复。</p>
</div>`;

const newOrderTemplate = (order, env) => `
<div style="font-family:-apple-system,'PingFang SC',sans-serif;max-width:520px;margin:0 auto;padding:32px;background:#fff;color:#222">
  <h2 style="color:#c0392b">📬 收到新订单</h2>
  <table style="width:100%;border-collapse:collapse">
    <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>用户邮箱</b></td><td>${order.email}</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>金额</b></td><td>¥${order.amount}</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>方式</b></td><td>${order.payment_method === 'alipay' ? '支付宝' : '微信'}</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>用户备注</b></td><td>${order.user_note || '(无)'}</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>下单时间</b></td><td>${order.created_at}</td></tr>
  </table>
  <p style="margin-top:24px">
    <a href="${env.SITE_URL}/admin.html" style="background:#c0392b;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none">前往审核 →</a>
  </p>
</div>`;

const approvedTemplate = (order, env) => `
<div style="font-family:-apple-system,'PingFang SC',sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#fff;color:#222">
  <h2 style="color:#27ae60">✅ 订阅开通成功</h2>
  <p>你好！</p>
  <p>你的订单已审核通过，<b>每日 AI 财经播报订阅</b>已正式开通。</p>
  <p>从明天 08:00 开始，每天都会有一份精心准备的财经晨报送到你的邮箱。</p>
  <div style="background:#f8f8f8;padding:16px;border-radius:6px;margin:20px 0">
    <b>有效期至：</b>${order.expires_at || '30 天'}<br>
    <b>订单金额：</b>¥${order.amount}
  </div>
  <p>有任何问题随时联系。感谢你的支持！</p>
</div>`;

async function createSession(env, userId) {
  const token = uuid().replace(/-/g, '') + uuid().replace(/-/g, '');
  const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
  await env.DB.prepare('INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)')
    .bind(token, userId, expiresAt).run();
  return token;
}

async function getUserFromToken(env, token) {
  if (!token) return null;
  return await env.DB.prepare(`
    SELECT u.* FROM users u JOIN sessions s ON s.user_id = u.id
    WHERE s.token = ? AND s.expires_at > ?
  `).bind(token, now()).first();
}

const getCookieToken = (req) => (req.headers.get('Cookie') || '').match(/session=([^;]+)/)?.[1] || null;
const setCookieHeader   = (t) => `session=${t}; Path=/; Max-Age=${30*24*60*60}; Secure; SameSite=None; HttpOnly`;
const clearCookieHeader = ()  => 'session=; Path=/; Max-Age=0; Secure; SameSite=None; HttpOnly';

// ── handlers ──────────────────────────────────────

async function handleSendCode(request, env) {
  const { email } = await request.json();
  if (!isValidEmail(email)) return error('邮箱格式错误');
  const rlKey = `rl:${email}`;
  if (await env.CODES.get(rlKey)) return error('发送过于频繁，请稍后再试', 429);
  const code = genCode();
  await env.CODES.put(`code:${email}`, code, { expirationTtl: 600 });
  await env.CODES.put(rlKey, '1', { expirationTtl: 60 });
  try {
    await sendEmail(env, {
      to: email,
      subject: `【股市预警】登录验证码 ${code}`,
      html: loginCodeTemplate(code),
    });
  } catch (e) {
    return error('邮件发送失败：' + e.message, 500);
  }
  return json({ ok: true, message: '验证码已发送到邮箱' });
}

async function handleVerify(request, env) {
  const { email, code } = await request.json();
  if (!isValidEmail(email)) return error('邮箱格式错误');
  if (!/^\d{6}$/.test(code || '')) return error('验证码格式错误');
  const stored = await env.CODES.get(`code:${email}`);
  if (!stored || stored !== code) return error('验证码错误或已过期');
  await env.CODES.delete(`code:${email}`);
  let user = await env.DB.prepare('SELECT * FROM users WHERE email = ?').bind(email).first();
  if (!user) {
    const id = uuid();
    await env.DB.prepare('INSERT INTO users (id, email, created_at, last_login_at) VALUES (?, ?, ?, ?)')
      .bind(id, email, now(), now()).run();
    user = { id, email, is_paid: 0, is_admin: 0 };
  } else {
    await env.DB.prepare('UPDATE users SET last_login_at = ? WHERE id = ?').bind(now(), user.id).run();
  }
  if (env.ADMIN_EMAIL && email === env.ADMIN_EMAIL && !user.is_admin) {
    await env.DB.prepare('UPDATE users SET is_admin = 1 WHERE id = ?').bind(user.id).run();
    user.is_admin = 1;
  }
  const token = await createSession(env, user.id);
  return new Response(JSON.stringify({
    ok: true,
    user: { email: user.email, is_paid: !!user.is_paid, is_admin: !!user.is_admin, paid_until: user.paid_until },
  }), { status: 200, headers: { ...JSON_HEADERS, 'Set-Cookie': setCookieHeader(token) } });
}

async function handleMe(request, env) {
  const user = await getUserFromToken(env, getCookieToken(request));
  if (!user) return json({ user: null });
  return json({ user: { email: user.email, is_paid: !!user.is_paid, is_admin: !!user.is_admin, paid_until: user.paid_until } });
}

async function handleLogout(request, env) {
  const token = getCookieToken(request);
  if (token) await env.DB.prepare('DELETE FROM sessions WHERE token = ?').bind(token).run();
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { ...JSON_HEADERS, 'Set-Cookie': clearCookieHeader() } });
}

async function handleCreateOrder(request, env) {
  const user = await getUserFromToken(env, getCookieToken(request));
  if (!user) return error('请先登录', 401);
  const { payment_method, user_note, amount, duration_days } = await request.json();
  if (!['alipay', 'wechat'].includes(payment_method)) return error('支付方式无效');
  const amt = parseFloat(amount) || parseFloat(env.PRICE_CNY) || 10;
  const days = parseInt(duration_days) || 30;
  const orderId = uuid();
  const order = {
    id: orderId, user_id: user.id, email: user.email, amount: amt, duration_days: days,
    payment_method, status: 'pending', user_note: (user_note || '').slice(0, 500), created_at: now(),
  };
  await env.DB.prepare(`
    INSERT INTO orders (id, user_id, email, amount, duration_days, payment_method, status, user_note, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(order.id, order.user_id, order.email, order.amount, order.duration_days,
           order.payment_method, order.status, order.user_note, order.created_at).run();
  if (env.ADMIN_EMAIL) {
    try {
      await sendEmail(env, {
        to: env.ADMIN_EMAIL,
        subject: `📬 [股市预警] 新订单 ${order.email} ¥${order.amount}`,
        html: newOrderTemplate(order, env),
      });
    } catch (e) { console.error('通知管理员失败:', e); }
  }
  return json({ ok: true, order_id: orderId, message: '订单已提交，等待审核' });
}

async function handleMyOrders(request, env) {
  const user = await getUserFromToken(env, getCookieToken(request));
  if (!user) return error('请先登录', 401);
  const rows = await env.DB.prepare(
    'SELECT id, amount, payment_method, status, user_note, admin_note, created_at, approved_at FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 20'
  ).bind(user.id).all();
  return json({ orders: rows.results || [] });
}

async function requireAdmin(request, env) {
  const user = await getUserFromToken(env, getCookieToken(request));
  return (user && user.is_admin) ? user : null;
}

async function handleAdminOrders(request, env) {
  if (!await requireAdmin(request, env)) return error('无权限', 403);
  const url = new URL(request.url);
  const status = url.searchParams.get('status') || 'pending';
  const rows = await env.DB.prepare(
    'SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC LIMIT 100'
  ).bind(status).all();
  return json({ orders: rows.results || [] });
}

async function handleApproveOrder(request, env) {
  if (!await requireAdmin(request, env)) return error('无权限', 403);
  const { order_id, admin_note } = await request.json();
  const order = await env.DB.prepare('SELECT * FROM orders WHERE id = ?').bind(order_id).first();
  if (!order) return error('订单不存在', 404);
  if (order.status !== 'pending') return error('订单状态不正确');
  const days = order.duration_days || 30;
  const expiresAt = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
  const expiresAtCn = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toLocaleDateString('zh-CN');
  await env.DB.prepare('UPDATE orders SET status = ?, admin_note = ?, approved_at = ? WHERE id = ?')
    .bind('approved', admin_note || '', now(), order_id).run();
  await env.DB.prepare('UPDATE users SET is_paid = 1, paid_until = ? WHERE id = ?')
    .bind(expiresAt, order.user_id).run();
  await env.DB.prepare(`
    INSERT INTO subscribers (email, active, created_at, expires_at) VALUES (?, 1, ?, ?)
    ON CONFLICT(email) DO UPDATE SET active = 1, expires_at = excluded.expires_at
  `).bind(order.email, now(), expiresAt).run();
  try {
    await sendEmail(env, {
      to: order.email,
      subject: '✅ 股市预警 · 订阅开通成功',
      html: approvedTemplate({ ...order, expires_at: expiresAtCn }, env),
    });
  } catch (e) { console.error('通知用户失败:', e); }
  return json({ ok: true, message: '已审核通过，用户已激活' });
}

async function handleRejectOrder(request, env) {
  if (!await requireAdmin(request, env)) return error('无权限', 403);
  const { order_id, admin_note } = await request.json();
  await env.DB.prepare('UPDATE orders SET status = ?, admin_note = ? WHERE id = ?')
    .bind('rejected', admin_note || '未通过审核', order_id).run();
  return json({ ok: true });
}

const ROUTES = {
  'POST /api/auth/send-code': handleSendCode,
  'POST /api/auth/verify':    handleVerify,
  'GET /api/auth/me':         handleMe,
  'POST /api/auth/logout':    handleLogout,
  'POST /api/orders/create':  handleCreateOrder,
  'GET /api/orders/my':       handleMyOrders,
  'GET /api/admin/orders':    handleAdminOrders,
  'POST /api/admin/approve':  handleApproveOrder,
  'POST /api/admin/reject':   handleRejectOrder,
};

// Pages Functions 入口
export async function onRequest(context) {
  const { request, env } = context;
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }
  const url = new URL(request.url);
  const key = `${request.method} ${url.pathname}`;
  const handler = ROUTES[key];
  if (!handler) return error('Not Found: ' + key, 404);
  try {
    return await handler(request, env);
  } catch (e) {
    console.error('处理出错:', e);
    return error('服务器错误: ' + e.message, 500);
  }
}
