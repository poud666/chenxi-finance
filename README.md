# 美国非农第一时间抓取流程

这个流程用于每个月自动盯 BLS 官方 API：从北京时间/香港时间 20:28 开始，每 5 秒抓取一次，最多持续 20 分钟；一旦发现新报告，立即解析数据、判断降息预期方向，然后提前结束。

## 数据源

- 官方 API：https://api.bls.gov/publicAPI/v2/timeseries/data/
- 官方发布页：https://www.bls.gov/news.release/empsit.nr0.htm
- 官方日程页：https://www.bls.gov/schedule/news_release/empsit.htm

## 初始化

第一次使用前，可以先把当前 BLS API 的最新月份标记为“已处理”，避免自动任务把旧报告当成新报告：

```powershell
py -3 .\nfp_monitor.py --mark-current-seen
```

## 单次运行

如果你要手动盯某次报告：

```powershell
py -3 .\nfp_monitor.py --watch --only-new --interval-seconds 5 --timeout-seconds 1200 --expected-payrolls-k 110 --expected-unemployment 4.3 --expected-ahe-mom 0.3
```

如果已经知道目标报告月份，也可以加：

```powershell
--target-release "June 2026"
```

## 本地 Windows 自动运行

这是本地备用方案。因为非农发布日期会受美国节假日影响，不建议用“每月第几个星期五”硬编码。本地更稳的做法是每天 20:28 启动一次，只运行 20 分钟；脚本通过 `state/nfp_state.json` 判断是否真的出现了新报告。非发布日不会重复分析旧数据。首次运行如果没有 state，脚本会先记录当前报告为基线，然后继续等待新报告。

创建 Windows 计划任务：

```powershell
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"C:\Users\l\Documents\金融工具网站\run_nfp_watch.ps1`""
$Trigger = New-ScheduledTaskTrigger -Daily -At 20:28
Register-ScheduledTask -TaskName "NFP_BLS_Watch" -Action $Action -Trigger $Trigger -Description "Watch BLS nonfarm payrolls every 5 seconds for 20 minutes from 20:28 HK time."
```

## GitHub Actions 自动工作流

仓库里包含两个 GitHub Actions workflow：

- `.github/workflows/update-nfp-calendar.yml`：每月 1 号查阅 BLS 官方日程，更新 `.github/nfp_release_dates.json` 里的本月真实公布日。
- `.github/workflows/update-nfp-calendar.yml` 同时会从 Trading Economics 的非农页面抓取本月 consensus，写入 `.github/nfp_expectations.json`。脚本会解析来源页面里的预期月份，例如 `June 2026`，并要求它和 BLS 公布日目标月份完全一致，否则失败，不写入。
- `.github/workflows/nfp.yml`：香港时间 20:28 附近启动，但只有当天匹配 `.github/nfp_release_dates.json` 里的真实公布日时，才进入 5 秒一次、持续 20 分钟的 BLS API 轮询。判断降息预期时优先使用月初已抓取的 `.github/nfp_expectations.json`，也可以用 GitHub Variables 或手动触发参数覆盖。

非农通常是每月第一个星期五，但遇到美国节假日会提前或调整，所以 workflow 不直接写死第一个星期五。月初查阅 BLS 日程失败时，更新脚本会退回到“第一个星期五，若遇美国联邦假日则前移到前一工作日”的规则，并在 JSON 中标记 `verified_by`。

可选：在 GitHub 仓库 `Settings -> Secrets and variables -> Actions -> Variables` 里设置，用于覆盖自动抓取的 consensus：

- `NFP_EXPECTED_PAYROLLS_K`
- `NFP_EXPECTED_UNEMPLOYMENT`
- `NFP_EXPECTED_AHE_MOM`
- `NFP_EMAIL_TO`
- `NFP_EMAIL_FROM`，必须是 Resend 已验证的发件人，例如 `Nonfarm Alerts <alerts@yourdomain.com>`

在 `Settings -> Secrets and variables -> Actions -> Secrets` 里设置：

- `RESEND_API_KEY`

抓到目标月份数据后会先发一封原始数据邮件；完成降息预期判断后会再发一封分析邮件。邮件通过 Resend API 发送；如果没有设置 `RESEND_API_KEY`、`NFP_EMAIL_FROM` 或收件人，工作流仍会生成 artifact，但不会发邮件。工作流产物会上传为 GitHub Actions artifact，包含 `outputs/` 和 `state/`。

## 市场预期

降息预期判断需要三个预期值：

- `--expected-payrolls-k`：非农新增预期，单位 k，比如 `110` 表示 11 万人
- `--expected-unemployment`：失业率预期，比如 `4.3`
- `--expected-ahe-mom`：平均时薪环比预期，比如 `0.3`

如果不填预期，脚本仍会抓取官方数据，但只能做低置信度判断。

## 输出

脚本会在控制台打印中文解读，并在 `outputs/` 目录生成：

- `nfp-YYYYMMDD-HHMMSS.md`：中文快评
- `nfp-YYYYMMDD-HHMMSS.json`：结构化数据和打分结果

## 判断规则

- 非农明显高于预期：偏鹰，降息预期下降
- 失业率明显低于预期：偏鹰，降息预期下降
- 平均时薪高于预期：偏鹰，降息预期下降
- 前值合计上修：偏鹰，降息预期下降
- 反向则偏鸽，降息预期上升

默认阈值：

- 非农预期差超过 50k 才计入强信号
- 失业率/平均时薪预期差超过 0.05 个百分点才计入强信号
- 前值合计修正超过 50k 才计入强信号
