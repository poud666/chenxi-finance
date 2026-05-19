# 收款码

请把以下两张图片放到这个目录：

- `alipay-qr.png` — 你的支付宝个人收款码截图
- `wechat-qr.png` — 你的微信个人收款码截图

**如何获取：**

## 支付宝
1. 打开支付宝 App
2. 首页 → 收钱 → 保存图片到相册
3. 把图片传到电脑，改名为 `alipay-qr.png`

## 微信
1. 打开微信 → 我 → 服务 → 收付款 → 二维码收款
2. 右上角 → 保存收款码
3. 把图片传到电脑，改名为 `wechat-qr.png`

放好后执行：
```
git add assets/
git commit -m "feat: 加入收款码"
git push
```
