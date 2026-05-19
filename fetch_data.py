#!/usr/bin/env python3
"""
股市预警 · 指标数据抓取
- yfinance: 股指 / ETF / 收益率 / 波动率
- FRED CSV: 信用利差 / 期限利差（无需 API Key）
- CNN: Fear & Greed 综合情绪指标

输出: data.json，前端读取渲染仪表盘
"""

import json
import sys
import io
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# Windows 终端编码
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUTPUT = Path(__file__).parent / "data.json"
TIMEOUT = 15
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ──────────────────────────────────────────────────────
# 指标定义（含警戒线）
# ──────────────────────────────────────────────────────

THRESHOLDS = {
    # 第一档 - 核心
    "VIX":     {"normal": (12, 20),   "warn": 25,    "alert": 30},
    "MOVE":    {"normal": (80, 100),  "warn": 120,   "alert": 150},
    "TNX":     {"normal": (4.0, 4.5), "warn": 4.8,   "alert": 5.0},   # 10Y
    "DXY":     {"normal": (100, 105), "warn": 107,   "alert": 110},
    "T10Y2Y":  {"normal": (0, 2),     "warn_low": 0, "alert_low": -0.5},  # 倒挂
    # 第二档 - 信用
    "HY_OAS":  {"normal": (300, 400), "warn": 500,   "alert": 700},
    "IG_OAS":  {"normal": (100, 150), "warn": 200,   "alert": 300},
    # 情绪
    "FG":      {"normal": (25, 75),   "warn_high": 75, "warn_low": 25,
                "alert_high": 90, "alert_low": 10},
}


def classify(symbol: str, value: float) -> str:
    """根据警戒线返回 ok / warn / alert"""
    t = THRESHOLDS.get(symbol)
    if not t or value is None:
        return "ok"
    # 高位警戒
    if "alert" in t and value >= t["alert"]:
        return "alert"
    if "warn" in t and value >= t["warn"]:
        return "warn"
    # 低位警戒（倒挂等）
    if "alert_low" in t and value <= t["alert_low"]:
        return "alert"
    if "warn_low" in t and value <= t["warn_low"]:
        return "warn"
    # 情绪指标双向
    if "alert_high" in t and value >= t["alert_high"]:
        return "alert"
    if "alert_low" in t and value <= t["alert_low"]:
        return "alert"
    return "ok"


# ──────────────────────────────────────────────────────
# Yahoo Finance（无需 API Key）
# ──────────────────────────────────────────────────────

def fetch_yahoo(symbol: str) -> dict | None:
    """获取单个 Yahoo Finance 标的的最新价 + 变化"""
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
               "?interval=1d&range=5d")
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
        d = r.json()
        meta = d["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev  = meta.get("chartPreviousClose") or price
        if price is None:
            return None
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0
        return {
            "value": round(price, 4),
            "change": round(change, 4),
            "change_pct": round(change_pct, 2),
            "prev_close": round(prev, 4),
        }
    except Exception as e:
        print(f"  [yahoo] {symbol} 获取失败: {e}")
        return None


# ──────────────────────────────────────────────────────
# FRED（CSV 下载，无需 API Key）
# ──────────────────────────────────────────────────────

def fetch_fred(series_id: str) -> dict | None:
    """从 FRED CSV 下载最新值"""
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
        reader = csv.reader(io.StringIO(r.text))
        rows = [row for row in reader if len(row) == 2]
        # 跳过标题
        rows = rows[1:]
        # 过滤无效数据（FRED 用 . 表示 N/A）
        valid = [(d, float(v)) for d, v in rows if v not in (".", "")]
        if len(valid) < 2:
            return None
        date_now, val_now = valid[-1]
        date_prev, val_prev = valid[-2]
        change = val_now - val_prev
        change_pct = (change / val_prev * 100) if val_prev else 0
        return {
            "value": round(val_now, 4),
            "change": round(change, 4),
            "change_pct": round(change_pct, 2),
            "date": date_now,
        }
    except Exception as e:
        print(f"  [fred] {series_id} 获取失败: {e}")
        return None


# ──────────────────────────────────────────────────────
# CNN Fear & Greed
# ──────────────────────────────────────────────────────

def fetch_fear_greed() -> dict | None:
    """CNN Fear & Greed 综合情绪指标"""
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
        d = r.json()
        now = d.get("fear_and_greed", {})
        val = now.get("score")
        if val is None:
            return None
        return {
            "value": round(val, 1),
            "rating": now.get("rating", "neutral"),
            "prev_close":     round(now.get("previous_close",     val), 1),
            "prev_1_week":    round(now.get("previous_1_week",    val), 1),
            "prev_1_month":   round(now.get("previous_1_month",   val), 1),
            "prev_1_year":    round(now.get("previous_1_year",    val), 1),
        }
    except Exception as e:
        print(f"  [cnn] Fear & Greed 获取失败: {e}")
        return None


# ──────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────

def make_indicator(name, name_cn, sub, value_data, threshold_key,
                   unit="", desc="", category="tier1"):
    """统一构造指标对象"""
    if not value_data:
        return {"name": name, "name_cn": name_cn, "sub": sub,
                "value": None, "status": "unknown", "desc": desc,
                "category": category}
    status = classify(threshold_key, value_data["value"])
    return {
        "name": name,
        "name_cn": name_cn,
        "sub": sub,
        "value": value_data["value"],
        "change": value_data.get("change"),
        "change_pct": value_data.get("change_pct"),
        "unit": unit,
        "status": status,
        "desc": desc,
        "category": category,
        "threshold": THRESHOLDS.get(threshold_key, {}),
    }


def main():
    print("📡 开始抓取指标数据...")

    indicators = []

    # ─── 第一档：核心 ───
    print("\n[第一档] 核心风险指标")
    indicators.append(make_indicator(
        "VIX", "恐慌指数", "标普500隐含波动率",
        fetch_yahoo("^VIX"), "VIX", unit="",
        desc="标普 500 未来 30 天的隐含波动率。数值越高，市场越恐慌。<b>>25 紧张 · >30 恐慌</b>。",
        category="tier1"))
    print("  ✓ VIX")

    indicators.append(make_indicator(
        "MOVE", "债券波动率", "美债隐含波动率",
        fetch_yahoo("^MOVE"), "MOVE",
        desc="\"债券版 VIX\"。美债利率的预期波动率，常先于股市预警系统性风险。<b>>120 紧张 · >150 危险</b>。",
        category="tier1"))
    print("  ✓ MOVE")

    # ^TNX 在 Yahoo 是 10Y 收益率 *10，比如 4.32% 显示为 43.20
    tnx = fetch_yahoo("^TNX")
    if tnx:
        tnx["value"] = round(tnx["value"] / 10, 3)
        if tnx.get("change") is not None:
            tnx["change"] = round(tnx["change"] / 10, 3)
            tnx["change_pct"] = round(tnx["change_pct"], 2)
    indicators.append(make_indicator(
        "10Y", "美债收益率", "10年期",
        tnx, "TNX", unit="%",
        desc="全球资产定价的\"无风险锚\"。上升压制股市估值，尤其是科技股。<b>>4.8% 关键 · >5% 警报</b>。",
        category="tier1"))
    print("  ✓ 10Y TNX")

    indicators.append(make_indicator(
        "DXY", "美元指数", "美元强弱",
        fetch_yahoo("DX-Y.NYB"), "DXY",
        desc="美元对一篮子主要货币的强弱。走强意味着全球流动性收紧、新兴市场承压。<b>>107 流动性紧张</b>。",
        category="tier1"))
    print("  ✓ DXY")

    # 10Y-2Y 期限利差（直接用 FRED）
    indicators.append(make_indicator(
        "10Y-2Y", "期限利差", "10年-2年美债",
        fetch_fred("T10Y2Y"), "T10Y2Y", unit="%",
        desc="长短期美债利差。<b>负值 = 倒挂</b>，是历史上几乎所有美国衰退的提前信号；由负转正后 6-18 月易触发衰退。",
        category="tier1"))
    print("  ✓ T10Y2Y")

    indicators.append(make_indicator(
        "S&P 500", "标普500", "美股大盘",
        fetch_yahoo("^GSPC"), None,
        desc="美股最具代表性的指数，由 500 家大公司组成。<b>跌破 200 日均线</b>通常预示中期趋势转弱。",
        category="tier1"))
    print("  ✓ S&P 500")

    indicators.append(make_indicator(
        "NDX", "纳指100", "科技大盘",
        fetch_yahoo("^NDX"), None,
        desc="纳斯达克 100 指数，科技股权重高。对利率最敏感，是风险偏好的晴雨表。",
        category="tier1"))
    print("  ✓ NDX")

    # ─── 第二档：信用与银行 ───
    print("\n[第二档] 信用与银行")

    # HY OAS（FRED）
    hy = fetch_fred("BAMLH0A0HYM2")
    if hy:
        # FRED 返回的单位是 %，转成 bp
        hy["value"] = round(hy["value"] * 100, 0)
        if hy.get("change") is not None:
            hy["change"] = round(hy["change"] * 100, 0)
    indicators.append(make_indicator(
        "HY OAS", "高收益债利差", "ICE BofA",
        hy, "HY_OAS", unit="bp",
        desc="\"垃圾债\"与国债的利差。<b>快速走阔</b>意味着市场担心企业违约，是信用危机的最敏感信号。<b>>500bp 警报</b>。",
        category="tier2"))
    print("  ✓ HY OAS")

    ig = fetch_fred("BAMLC0A0CM")
    if ig:
        ig["value"] = round(ig["value"] * 100, 0)
        if ig.get("change") is not None:
            ig["change"] = round(ig["change"] * 100, 0)
    indicators.append(make_indicator(
        "IG OAS", "投资级利差", "ICE BofA",
        ig, "IG_OAS", unit="bp",
        desc="优质公司债与国债的利差。<b>同时走阔</b>意味着系统性而非局部风险。<b>>200bp 警报</b>。",
        category="tier2"))
    print("  ✓ IG OAS")

    indicators.append(make_indicator(
        "KBW", "银行指数", "大型银行",
        fetch_yahoo("^BKX"), None,
        desc="美国 24 家大型银行股的加权指数。<b>大幅跑输标普</b>常预示银行业承压（如 2023 年硅谷银行事件）。",
        category="tier2"))
    print("  ✓ KBW")

    indicators.append(make_indicator(
        "KRE", "区域银行", "ETF",
        fetch_yahoo("KRE"), None,
        desc="中小银行 ETF。<b>对利率和商业地产暴露最大</b>，是地方性银行危机的先行指标。",
        category="tier2"))
    print("  ✓ KRE")

    indicators.append(make_indicator(
        "XLF", "金融业 ETF", "大型金融股",
        fetch_yahoo("XLF"), None,
        desc="标普金融板块 ETF，覆盖大行、保险、券商。<b>持续跑输大盘</b>反映金融压力。",
        category="tier2"))
    print("  ✓ XLF")

    # ─── 第四档：危机模式触发器（ETF） ───
    print("\n[第四档] 危机模式触发器（监控用）")

    indicators.append(make_indicator(
        "VNQ", "REIT 综合 ETF", "商业地产",
        fetch_yahoo("VNQ"), None,
        desc="美国 REITs 综合 ETF。<b>商业地产暴跌</b>会冲击区域银行资产质量，是系统性风险传导链关键一环。",
        category="tier4"))
    print("  ✓ VNQ")

    indicators.append(make_indicator(
        "IYR", "REIT ETF", "iShares 房地产",
        fetch_yahoo("IYR"), None,
        desc="iShares 美国房地产 ETF，与 VNQ 互相印证。同步暴跌时警惕地产链系统性风险。",
        category="tier4"))
    print("  ✓ IYR")

    indicators.append(make_indicator(
        "ARCC", "BDC 龙头", "私募信贷",
        fetch_yahoo("ARCC"), None,
        desc="最大的 BDC（商业发展公司），是私募信贷市场的标杆。<b>影子银行</b>风险的先行指标。",
        category="tier4"))
    print("  ✓ ARCC")

    # ─── 情绪 ───
    print("\n[情绪]")
    fg_data = fetch_fear_greed()
    if fg_data:
        fg_indicator = {
            "name": "Fear & Greed",
            "name_cn": "恐慌贪婪指数",
            "sub": "CNN 综合情绪",
            "value": fg_data["value"],
            "rating": fg_data["rating"],
            "prev_close":   fg_data["prev_close"],
            "prev_1_week":  fg_data["prev_1_week"],
            "prev_1_month": fg_data["prev_1_month"],
            "prev_1_year":  fg_data["prev_1_year"],
            "status": classify("FG", fg_data["value"]),
            "desc": "综合 7 个子指标（动量、波动率、安全资产需求、垃圾债需求、Put/Call 比率、广度、看涨期权）。<b>极度恐慌时往往是底部</b>，<b>极度贪婪时往往临近顶部</b>。",
            "category": "sentiment",
        }
        print(f"  ✓ Fear & Greed = {fg_data['value']} ({fg_data['rating']})")
    else:
        fg_indicator = None

    # ─── 输出 ───
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_at_cn": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "indicators": indicators,
        "sentiment": fg_indicator,
    }

    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 已写入 {OUTPUT.name}（{len(indicators)} 个指标 + 情绪指数）")


if __name__ == "__main__":
    main()
