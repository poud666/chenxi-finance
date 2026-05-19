# 部署指南

完整流程：注册账号 → 部署后端 Worker → 配置前端 → 上线。

预计耗时：**30-45 分钟**。

---

## ✅ 第 1 步：注册必要账号

### 1.1 Cloudflare（后端 + 数据库 + 邮件验证码存储）

1. 注册：https://dash.cloudflare.com/sign-up
2. 邮箱验证后即可使用

### 1.2 Resend（发送邮件）

1. 注册：https://resend.com
2. 邮箱验证后，左侧 **API Keys** → **Create API Key**
3. 复制保存这个 Key（只显示一次）

### 1.3 验证发件域名（可选但推荐）

Resend 免费用户可用 `onboarding@resend.dev` 作为发件邮箱，但建议绑定自己的域名：

- 如果暂时没有域名，先跳过，用 `onboarding@resend.dev` 即可
- 有域名的话，在 Resend 添加 Domain → 按指引在你的 DNS 加几条记录

---

## ✅ 第 2 步：部署 Worker 后端

### 2.1 本地安装 Wrangler CLI

```powershell
npm install -g wrangler
wrangler login
```

会跳浏览器登录 Cloudflare。

### 2.2 创建 D1 数据库

```powershell
cd C:\Users\l\daily-briefing-web\worker
wrangler d1 create chenxi-db
```

会输出类似：
```
[[d1_databases]]
binding = "DB"
database_name = "chenxi-db"
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"  ← 把这一行 ID 抄下来
```

打开 `wrangler.toml`，把 `REPLACE_WITH_YOUR_D1_ID` 替换为这个 ID。

### 2.3 初始化数据库表

```powershell
wrangler d1 execute chenxi-db --remote --file=./schema.sql
```

### 2.4 创建 KV 命名空间（存验证码）

```powershell
wrangler kv:namespace create CODES
```

输出：
```
[[kv_namespaces]]
binding = "CODES"
id = "yyyyyyyy..."  ← 抄下来
```

把 `wrangler.toml` 里 `REPLACE_WITH_YOUR_KV_ID` 也替换掉。

### 2.5 设置敏感密钥

```powershell
wrangler secret put RESEND_API_KEY
# 粘贴 Resend 的 API Key

wrangler secret put ADMIN_EMAIL
# 输入你的邮箱（这个邮箱登录后自动是管理员）
```

### 2.6 修改 wrangler.toml 中的 FROM_EMAIL

把 `noreply@chenxi.example.com` 改成：
- 如果用 Resend 默认：`onboarding@resend.dev`
- 如果绑了自己域名：`noreply@你的域名.com`

### 2.7 部署 Worker

```powershell
wrangler deploy
```

部署成功后会输出 Worker URL，类似：
```
https://chenxi-api.poud666.workers.dev
```

**把这个 URL 抄下来**，下一步要用。

---

## ✅ 第 3 步：把 Worker URL 配置到前端

打开 `app.js`，找到：

```js
const API_URL = window.API_URL || 'https://chenxi-api.poud666.workers.dev';
```

把后面那个 URL 改成你 Worker 部署的实际 URL。

---

## ✅ 第 4 步：放收款码

按 `assets/README.md` 里的说明，把两张收款码图片放到 `assets/` 目录：

- `assets/alipay-qr.png`
- `assets/wechat-qr.png`

---

## ✅ 第 5 步：推送上线

```powershell
cd C:\Users\l\daily-briefing-web
git add .
git commit -m "feat: 部署登录 + 订阅系统"
git push
```

等 1-2 分钟，GitHub Pages 自动构建。

---

## 🧪 第 6 步：测试

1. 打开 https://poud666.github.io/chenxi-finance/
2. 右上角点 **登录**
3. 输入你的管理员邮箱（之前设的 `ADMIN_EMAIL`）→ 收验证码 → 登录
4. 访问 https://poud666.github.io/chenxi-finance/admin.html
5. 看到管理后台说明登录成功

测试完整流程：
1. 用另一个邮箱注册一个普通用户
2. 在订阅页面下个单
3. 用管理员邮箱进后台 → 看到待审核订单 → 点通过
4. 普通用户邮箱收到"开通成功"邮件

---

## ❓ 常见问题

### Q: 推送后网站还是旧版？
A: GitHub Pages 缓存，硬刷新 Ctrl+F5 或等几分钟。

### Q: 登录验证码收不到？
A: 检查 Resend 后台的 **Logs** 看是否发送成功；可能去了垃圾邮件。

### Q: 后台看不到订单？
A: 确认管理员邮箱设置正确（`wrangler secret put ADMIN_EMAIL`），可以重新设置然后再登录一次。

### Q: 想改价格 / 时长？
A: 改 `wrangler.toml` 里的 `PRICE_CNY`，然后 `wrangler deploy`。

---

## 💰 费用说明

| 服务 | 免费额度 | 你大概用多少 |
|------|---------|------------|
| Cloudflare Workers | 10 万次请求/天 | 早期 <100 |
| Cloudflare D1 | 5GB 存储，500万次读/天 | 远远用不完 |
| Cloudflare KV | 1000 次写/天 | 早期 <100 |
| Resend | 3000 封邮件/月 | 100 用户够用 |
| GitHub Pages | 无限制 | 静态托管 |

**总成本：¥0**（在用户少于几千时）
