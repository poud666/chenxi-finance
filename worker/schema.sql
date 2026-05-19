-- 股市预警 · D1 数据库 Schema
-- 在 wrangler 创建 D1 数据库后执行：
-- wrangler d1 execute chenxi-db --file=./schema.sql

-- 用户表
CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  is_paid       INTEGER DEFAULT 0,
  paid_until    TEXT,
  is_admin      INTEGER DEFAULT 0,
  created_at    TEXT NOT NULL,
  last_login_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Session 表
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- 订单表（支付记录）
CREATE TABLE IF NOT EXISTS orders (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL,
  email          TEXT NOT NULL,
  amount         REAL NOT NULL,
  duration_days  INTEGER NOT NULL DEFAULT 30,
  payment_method TEXT NOT NULL,           -- 'alipay' | 'wechat'
  status         TEXT NOT NULL,           -- 'pending' | 'approved' | 'rejected'
  user_note      TEXT,                    -- 用户备注（如支付订单号）
  admin_note     TEXT,                    -- 管理员备注
  created_at     TEXT NOT NULL,
  approved_at    TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_user    ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);

-- 邮件订阅表（被动同步：用户付费后自动加入）
CREATE TABLE IF NOT EXISTS subscribers (
  email      TEXT PRIMARY KEY,
  active     INTEGER DEFAULT 1,
  created_at TEXT NOT NULL,
  expires_at TEXT
);
