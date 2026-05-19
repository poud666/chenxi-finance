/**
 * 股市预警 · 前端公共 API 客户端
 * 部署 Worker 后，把 API_URL 改成你的 Worker 域名
 */

// 部署 Cloudflare Worker 后改这里：
// 示例: 'https://chenxi-api.your-account.workers.dev'
const API_URL = window.API_URL || 'https://chenxi-api.lbs20060607.workers.dev';

// ──────────────────────────────────────────────────────
// API 调用封装
// ──────────────────────────────────────────────────────

async function api(path, options = {}) {
  const opts = {
    credentials: 'include',     // 带 Cookie
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  };
  if (opts.body && typeof opts.body !== 'string') {
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch(API_URL + path, opts);
  let data = null;
  try { data = await r.json(); } catch {}
  if (!r.ok) throw new Error(data?.error || `HTTP ${r.status}`);
  return data;
}

const Auth = {
  sendCode: (email) => api('/api/auth/send-code', { method: 'POST', body: { email } }),
  verify:   (email, code) => api('/api/auth/verify',  { method: 'POST', body: { email, code } }),
  me:       () => api('/api/auth/me'),
  logout:   () => api('/api/auth/logout', { method: 'POST' }),
};

const Orders = {
  create: (payload) => api('/api/orders/create', { method: 'POST', body: payload }),
  my:     () => api('/api/orders/my'),
};

const Admin = {
  list:    (status='pending') => api(`/api/admin/orders?status=${status}`),
  approve: (order_id, admin_note='') => api('/api/admin/approve', { method: 'POST', body: { order_id, admin_note } }),
  reject:  (order_id, admin_note='') => api('/api/admin/reject',  { method: 'POST', body: { order_id, admin_note } }),
};

// ──────────────────────────────────────────────────────
// 全局用户状态
// ──────────────────────────────────────────────────────

let currentUser = null;

async function loadUser() {
  try {
    const { user } = await Auth.me();
    currentUser = user;
    updateNavUser(user);
    return user;
  } catch {
    currentUser = null;
    updateNavUser(null);
    return null;
  }
}

function updateNavUser(user) {
  // 在导航栏右侧渲染用户状态
  const navLinks = document.querySelector('.nav-links');
  if (!navLinks) return;

  // 移除旧的用户区
  const old = document.getElementById('nav-user');
  if (old) old.remove();

  const wrap = document.createElement('span');
  wrap.id = 'nav-user';
  wrap.style.cssText = 'display:flex;align-items:center;gap:12px;margin-left:12px';

  if (user) {
    // 已登录
    const badge = user.is_paid ? '<span class="user-badge paid">已订阅</span>' : '';
    wrap.innerHTML = `
      ${badge}
      <span class="user-email" title="${user.email}">${user.email.split('@')[0]}</span>
      <a href="account.html" class="btn-ghost btn-sm">账户</a>
      <button onclick="logoutUser()" class="btn-ghost btn-sm" style="border:none;background:transparent;cursor:pointer;color:var(--text-mute);font-size:.85rem">退出</button>
    `;
  } else {
    wrap.innerHTML = `
      <button onclick="openLoginModal()" class="btn-ghost btn-sm" style="border:1px solid var(--border-strong);background:transparent;cursor:pointer;font-family:inherit">登录</button>
    `;
  }
  navLinks.appendChild(wrap);
}

async function logoutUser() {
  await Auth.logout();
  currentUser = null;
  location.reload();
}

// ──────────────────────────────────────────────────────
// 登录弹窗（任何页面都可调）
// ──────────────────────────────────────────────────────

function openLoginModal(onSuccess) {
  // 移除旧的弹窗
  const old = document.getElementById('login-modal');
  if (old) old.remove();

  const modal = document.createElement('div');
  modal.id = 'login-modal';
  modal.className = 'modal-mask';
  modal.innerHTML = `
    <div class="modal">
      <button class="modal-close" onclick="closeLoginModal()">×</button>
      <div class="modal-head">
        <h3>登录 / 注册</h3>
        <p>输入邮箱接收验证码，新邮箱将自动创建账户</p>
      </div>
      <div class="modal-body">
        <div class="form-step" id="step-email">
          <label>邮箱地址</label>
          <input type="email" id="login-email" placeholder="your@email.com" autocomplete="email">
          <button class="btn-primary btn-full" onclick="sendLoginCode()">发送验证码</button>
        </div>
        <div class="form-step" id="step-code" style="display:none">
          <label>验证码</label>
          <p class="form-hint">验证码已发送至 <b id="login-email-display"></b></p>
          <input type="text" id="login-code" placeholder="6 位数字" maxlength="6" inputmode="numeric" pattern="\\d{6}">
          <button class="btn-primary btn-full" onclick="verifyLoginCode()">登录</button>
          <button class="btn-ghost btn-full" style="margin-top:8px" onclick="backToEmail()">← 换个邮箱</button>
        </div>
        <div id="login-msg" class="login-msg"></div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeLoginModal();
  });
  modal._onSuccess = onSuccess;
  setTimeout(() => document.getElementById('login-email')?.focus(), 50);
}

function closeLoginModal() {
  document.getElementById('login-modal')?.remove();
}

function backToEmail() {
  document.getElementById('step-email').style.display = '';
  document.getElementById('step-code').style.display = 'none';
  showMsg('', '');
}

async function sendLoginCode() {
  const email = document.getElementById('login-email').value.trim();
  if (!email) return showMsg('请输入邮箱', 'error');

  showMsg('发送中...', 'info');
  try {
    await Auth.sendCode(email);
    document.getElementById('step-email').style.display = 'none';
    document.getElementById('step-code').style.display = '';
    document.getElementById('login-email-display').textContent = email;
    document.getElementById('login-code').focus();
    showMsg('验证码已发送', 'success');
  } catch (e) {
    showMsg(e.message, 'error');
  }
}

async function verifyLoginCode() {
  const email = document.getElementById('login-email').value.trim();
  const code  = document.getElementById('login-code').value.trim();
  if (!/^\d{6}$/.test(code)) return showMsg('请输入 6 位验证码', 'error');

  showMsg('登录中...', 'info');
  try {
    const { user } = await Auth.verify(email, code);
    showMsg('登录成功！', 'success');
    setTimeout(() => {
      const modal = document.getElementById('login-modal');
      const cb = modal?._onSuccess;
      closeLoginModal();
      currentUser = user;
      updateNavUser(user);
      if (cb) cb(user);
    }, 500);
  } catch (e) {
    showMsg(e.message, 'error');
  }
}

function showMsg(text, type) {
  const el = document.getElementById('login-msg');
  if (!el) return;
  el.textContent = text;
  el.className = `login-msg login-msg-${type}`;
}

// 暴露到全局，供 HTML inline 调用
window.openLoginModal = openLoginModal;
window.closeLoginModal = closeLoginModal;
window.sendLoginCode = sendLoginCode;
window.verifyLoginCode = verifyLoginCode;
window.backToEmail = backToEmail;
window.logoutUser = logoutUser;

// 自动加载用户
document.addEventListener('DOMContentLoaded', loadUser);
