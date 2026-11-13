# -*- coding: utf-8 -*-
"""
合约计划生成器

读自选 -> 拉币安 K 线（日线定结构、4H 定时机）-> 输出可直接执行的计划：
方向 / 结构 / 进场区 / 止损 / 目标 / 盈亏比 / 仓位 / 失效条件

用法：
  python plan.py            # 打印计划并写 plans/ 下的 md
  python plan.py --push     # 额外推一条汇总到 Bark
"""

import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
PLAN_DIR = os.path.join(BASE, "plans")
FAPI = "https://fapi.binance.com"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
H = {"User-Agent": "Mozilla/5.0"}

DEFAULT_WATCH = [
    {"symbol": "SNDKUSDT", "name": "SNDK", "kind": "美股"},
    {"symbol": "SPCXUSDT", "name": "SPCX", "kind": "美股"},
    {"symbol": "MRNAUSDT", "name": "MRNA", "kind": "美股"},
    {"symbol": "BTCUSDT", "name": "BTC", "kind": "加密"},
]

RISK_PCT = 0.01        # 单笔风险：账户 1%
SWING_W = 3            # 分形左右各 3 根
MAX_STOP_PCT = 0.05    # 止损距离红线，对齐记忆体系「千五级」；超过 5% 视为位置不好
MIN_RR = 3.0           # 盈亏比下限（用户 2026-11-14 定：3:1 才做，6:1 为最优）


def log(msg):
    print(msg)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _opener(cfg):
    proxy = (cfg.get("proxy") or "").strip()
    handlers = [urllib.request.HTTPSHandler(context=CTX)]
    if proxy:
        handlers.insert(0, urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def get_json(url, cfg, retries=3):
    last = None
    for _ in range(retries):
        try:
            return json.loads(_opener(cfg).open(urllib.request.Request(url, headers=H), timeout=20).read().decode())
        except Exception as e:
            last = e
    raise last


def klines(symbol, interval, limit, cfg):
    u = f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    raw = get_json(u, cfg)
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]),
             "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in raw]


# ---------------------------------------------------------------- 指标

def ma(kl, n):
    seg = kl[-n:]
    return sum(x["c"] for x in seg) / len(seg)


def atr(kl, n=14):
    trs = []
    for i in range(1, len(kl)):
        h, l, pc = kl[i]["h"], kl[i]["l"], kl[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    seg = trs[-n:]
    return sum(seg) / len(seg) if seg else 0.0


def ema(vals, n):
    k = 2 / (n + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def macd_raw(kl, fast=12, slow=26, sig=9):
    closes = [x["c"] for x in kl]
    dif = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    dea = ema(dif, sig)
    hist = [(a - b) * 2 for a, b in zip(dif, dea)]
    return dif, dea, hist


def bar_detail(hist):
    """柱子的细化判断：连续同向根数、10 根内峰值、当前强度占比、是否已过峰。"""
    streak = 1
    for i in range(len(hist) - 1, 0, -1):
        if (hist[i] > 0) == (hist[i - 1] > 0):
            streak += 1
        else:
            break
    win = hist[-10:] if len(hist) >= 10 else hist
    peak_abs = max(abs(x) for x in win)
    peak_idx = max(range(len(win)), key=lambda i: abs(win[i]))
    cur = abs(hist[-1])
    ratio = (cur / peak_abs) if peak_abs else 0
    ago = len(win) - 1 - peak_idx
    # 已过峰：峰值出现在 2 根以上之前，且当前只剩不到 75% 的强度
    fading = ago >= 2 and ratio < 0.75
    return {"streak": streak, "peak": peak_abs, "ratio": ratio,
            "fading": fading, "ago": ago}


def macd_state(kl, fast=12, slow=26, sig=9):
    """MACD(12,26,9) + 动能变化：交叉、零轴、柱强弱（按绝对值）、柱是否过峰。"""
    dif, dea, hist = macd_raw(kl, fast, slow, sig)
    cross = "金叉" if dif[-1] > dea[-1] else "死叉"
    zero = "零轴上" if dif[-1] > 0 else "零轴下"
    d1 = hist[-1] - hist[-2] if len(hist) > 1 else 0
    d3 = hist[-1] - hist[-4] if len(hist) > 4 else 0
    mag, mag_prev = abs(hist[-1]), abs(hist[-2]) if len(hist) > 1 else abs(hist[-1])
    trend = "放大" if mag > mag_prev else ("缩小" if mag < mag_prev else "持平")
    b = bar_detail(hist)
    # 「已过峰」只在柱缩小时成立：柱还在放大那是刚起步，不是退潮
    b["fading"] = bool(b["fading"] and trend == "缩小")
    if hist[-1] > 0:
        label = "多头动能加强" if trend == "放大" else ("多头动能减弱" if trend == "缩小" else "多头动能为平")
    else:
        label = "空头动能加强" if trend == "放大" else ("空头动能减弱" if trend == "缩小" else "空头动能为平")
    if b["fading"]:
        label += "（已过峰）"
    text = f"{cross}·{zero}·柱{hist[-1]:+.2f}{trend}"
    return {"dif": dif[-1], "dea": dea[-1], "hist": hist[-1], "cross": cross,
            "zero": zero, "trend": trend, "d1": d1, "d3": d3, "label": label,
            "bar": b, "text": text}


def tf_state(kl):
    """单周期状态：结构 + 均线偏向 + MACD。"""
    st, _, _ = structure(kl)
    close = kl[-1]["c"]
    m20 = ma(kl, 20)
    a = atr(kl)
    bias = "多" if close > m20 + 0.3 * a else ("空" if close < m20 - 0.3 * a else "平")
    return {"struct": st, "bias": bias, "close": close, "ma20": m20,
            "atr": a, "macd": macd_state(kl)}


def consensus(d, h4, h1):
    """三层共振结论：(判断, 是否可做)"""
    bs = [d["bias"], h4["bias"], h1["bias"]]
    hs = [d["macd"]["hist"], h4["macd"]["hist"], h1["macd"]["hist"]]
    same_bias = len(set(bs)) == 1 and bs[0] in ("多", "空")
    same_hist = all(x > 0 for x in hs) or all(x < 0 for x in hs)
    if same_bias and same_hist:
        return ("三层同向偏空，空头共振" if bs[0] == "空" else "三层同向偏多，多头共振"), True
    if same_bias:
        dh, oh = hs[0], hs[1:]
        if all((dh > 0) != (o > 0) for o in oh):
            who = "多头" if dh > 0 else "空头"
            return f"三层均偏{bs[0]}，但日线柱在走{who}（反向），等柱同向再动", False
        return f"三层均偏{bs[0]}，柱子未齐，等齐再动", False
    if d["bias"] == "空" and h4["bias"] == "多":
        return "日线空、4H 多：下跌中的反弹，等 4H 转空", False
    if d["bias"] == "多" and h4["bias"] == "空":
        return "日线多、4H 空：上涨中的回调，等 4H 转多", False
    return "层级不一致，等对齐再动", False


def swings(kl, w=SWING_W):
    highs, lows = [], []
    for i in range(w, len(kl) - w):
        if kl[i]["h"] >= max(x["h"] for x in kl[i - w:i + w + 1]):
            highs.append((i, kl[i]["h"]))
        if kl[i]["l"] <= min(x["l"] for x in kl[i - w:i + w + 1]):
            lows.append((i, kl[i]["l"]))
    return highs, lows


def structure(kl):
    highs, lows = swings(kl)
    tag = "区间"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1][1] > highs[-2][1]
        hl = lows[-1][1] > lows[-2][1]
        if hh and hl:
            tag = "上升"
        elif not hh and not hl:
            tag = "下降"
    return tag, highs, lows


# ---------------------------------------------------------------- 计划

def fmt(v):
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 100:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:.3f}"
    return f"{v:.6f}"


def build_plan(item, cfg, equity):
    sym, name = item["symbol"], item["name"]
    kl = klines(sym, "1d", 220, cfg)
    k4 = klines(sym, "4h", 150, cfg)
    close = kl[-1]["c"]
    m20 = ma(kl, 20)
    m50 = ma(kl, 50)
    a = atr(kl)
    st, highs, lows = structure(kl)
    hi = highs[-1][1] if highs else max(x["h"] for x in kl[-60:])
    lo = lows[-1][1] if lows else min(x["l"] for x in kl[-60:])

    last4 = k4[-1]["c"]
    m20_4 = ma(k4, 20)

    # 方向闸：结构与均线同向才算有方向（加 0.3ATR 缓冲，避免压在均线上来回翻转）
    if st == "上升" and close > m20 + 0.3 * a:
        side = "long"
    elif st == "下降" and close < m20 - 0.3 * a:
        side = "short"
    else:
        side = "wait"

    plan = {
        "symbol": sym, "name": name, "kind": item.get("kind", ""),
        "price": close, "ma20": m20, "ma50": m50, "atr": a,
        "struct": st, "swing_high": hi, "swing_low": lo,
        "side": side, "entry": None, "stop": None, "t1": None, "t2": None,
        "rr": None, "risk_pct": None, "notional": None, "invalid": None,
        "macd": macd_state(kl), "macd4": macd_state(k4),
        "h4": {"close": last4, "ma20": m20_4, "above": last4 > m20_4},
    }
    # 三层周期：日线定大趋势、4H 定中周期、1H 定小周期
    try:
        k1 = klines(sym, "1h", 200, cfg)
    except Exception:
        k1 = k4
    plan["tfs"] = [("日线", tf_state(kl)), ("4H", tf_state(k4)), ("1H", tf_state(k1))]
    plan["consensus"], plan["aligned"] = consensus(*[t[1] for t in plan["tfs"]])
    plan["side"] = side

    # 动能闸：动能正在朝反方向加速时，不许逆着开（这是最容易亏钱的位置）
    dm = plan["macd"]
    if side == "short" and dm["cross"] == "金叉" and dm["hist"] > 0 and dm["trend"] == "放大":
        side = plan["side"] = "wait"
        plan["wait_reason"] = f"MACD {dm['text']}，动能向上，此时空是逆势，等柱重新缩小"
    elif side == "long" and dm["cross"] == "死叉" and dm["hist"] < 0 and dm["trend"] == "放大":
        side = plan["side"] = "wait"
        plan["wait_reason"] = f"MACD {dm['text']}，动能向下，此时多是接刀，等柱缩小"

    highs_above = [h[1] for h in highs if h[1] > close]
    lows_below = [l[1] for l in lows if l[1] < close]

    low60 = min(x["l"] for x in kl[-60:])
    high60 = max(x["h"] for x in kl[-60:])

    if side == "long":
        # 进场：不追高，等回踩到 MA20 或拉回 2ATR 的较高者
        entry = max(m20, close - 2 * a)
        base_stop = lo - 0.5 * a
        # 结构位太远（>3ATR）就用 ATR 止损，避免一笔赌太大
        stop = base_stop if (entry - base_stop) <= 3 * a else entry - 2 * a
        R = entry - stop
        if R > 0:
            t1 = high60 if high60 > entry else entry + 2 * R
            t2 = entry + 3 * R
            plan.update({
                "entry": (entry - 0.3 * a, entry + 0.15 * a),
                "stop": stop, "t1": t1, "t2": t2,
                "rr": (t1 - entry) / R,
                "risk_pct": R / entry,
                "invalid": f"日线收盘跌破 {fmt(stop)}",
            })
        else:
            side = "wait"

    elif side == "short":
        entry = min(m20, close + 2 * a)
        base_stop = hi + 0.5 * a
        stop = base_stop if (base_stop - entry) <= 3 * a else entry + 2 * a
        R = stop - entry
        if R > 0:
            t1 = low60 if low60 < entry else entry - 2 * R
            t2 = entry - 3 * R
            plan.update({
                "entry": (entry - 0.15 * a, entry + 0.3 * a),
                "stop": stop, "t1": t1, "t2": t2,
                "rr": (entry - t1) / R,
                "risk_pct": R / entry,
                "invalid": f"日线收盘站上 {fmt(stop)}",
            })
        else:
            side = "wait"

    plan["side"] = side

    # 两道质量闸：位置不好 / 盈亏比不够 -> 直接降级为观望
    if side in ("long", "short"):
        if plan["risk_pct"] and plan["risk_pct"] > MAX_STOP_PCT:
            plan["side"] = "wait"
            plan["wait_reason"] = f"止损距离 {plan['risk_pct']*100:.1f}% 超过 {MAX_STOP_PCT*100:.0f}%，位置不好，等回踩"
        elif plan["rr"] is not None and plan["rr"] < MIN_RR:
            plan["side"] = "wait"
            plan["wait_reason"] = f"盈亏比 {plan['rr']:.1f}:1 不足 {MIN_RR}:1，不值得做"

    if plan["side"] == "wait":
        if not plan.get("wait_reason"):
            if st == "下降":
                plan["wait_reason"] = f"结构下降但价在 MA20 上方（{fmt(m20)}），等重新跌破再做空"
            elif st == "上升":
                plan["wait_reason"] = f"结构上升但价跌破 MA20（{fmt(m20)}），等站回再做多"
            else:
                plan["wait_reason"] = (f"区间震荡 {fmt(min(x['l'] for x in kl[-20:]))}"
                                       f"~{fmt(max(x['h'] for x in kl[-20:]))}，等突破或等边缘")
        plan["invalid"] = f"站上 MA20（{fmt(m20)}）才谈做多；跌破 {fmt(low60)} 说明跌势延续"
    else:
        risk_amt = equity * RISK_PCT
        plan["notional"] = risk_amt / plan["risk_pct"]

    return plan


def render_line(p):
    """终端输出：标题行 + 三层 MACD 明细 + 结论。"""
    arrow = {"long": "只做多", "short": "只做空", "wait": "观望"}[p["side"]]
    tf_brief = " ".join(f"{n}{t['bias']}" for n, t in p["tfs"])
    lines = [f"{p['name']:<5} {p['symbol']:<10} {fmt(p['price']):>10}  {p['struct']:<3} {arrow:<4}"
             f"  [{tf_brief}]"]
    for name, t in p["tfs"]:
        m, b = t["macd"], t["macd"]["bar"]
        flag = "·已过峰" if b["fading"] else ""
        lines.append(f"        {name:<4} {m['cross']}·{m['zero']}·柱{m['hist']:+.2f}{m['trend']}"
                     f"（{b['streak']}根，强度 {b['ratio']*100:.0f}%{flag}） — {m['label']}")
    if p["side"] == "wait":
        lines.append(f"        → 观望：{p['wait_reason']}")
    else:
        lines.append(f"        → 进 {fmt(p['entry'][0])}~{fmt(p['entry'][1])}"
                     f" 损 {fmt(p['stop'])} 标 {fmt(p['t1'])}/{fmt(p['t2'])}"
                     f" 比 {p['rr']:.1f}:1 仓 {fmt(p['notional'])}U")
    return "\n".join(lines)


def render_tfs(p):
    """单标的的多周期明细：大趋势 + 中周期 + 小周期 + 柱子细节。"""
    lines = [f"### {p['name']} · 现价 {fmt(p['price'])}", "",
             "| 周期 | 结构 | 价 vs MA20 | MACD | 柱细节 |",
             "|---|---|---|---|---|"]
    for name, t in p["tfs"]:
        m, b = t["macd"], t["macd"]["bar"]
        rel = "上方" if t["close"] > t["ma20"] else "下方"
        extra = "，已过峰" if b["fading"] else ""
        lines.append(f"| {name} | {t['struct']} | {rel}（{fmt(t['ma20'])}） | {m['text']} | "
                     f"{b['streak']} 根同向，强度 {b['ratio']*100:.0f}%{extra} |")
    lines += ["", f"**共振**：{p['consensus']}"]
    return lines


def render_md(plans, equity, date_str):
    lines = [f"# 合约计划 · {date_str}", "",
                 f"账户权益假设 {equity:,.0f} U | 单笔风险 {RISK_PCT*100:.0f}% | 结构用日线，时机看 4H + MACD", "",
                 "| 标的 | 现价 | 结构 | 日线动能 | 结论 | 进场区 | 止损 | 目标1 | 目标2 | 盈亏比 | 名义仓位 |",
                 "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in plans:
        if p["side"] == "wait":
            lines.append(f"| {p['name']} | {fmt(p['price'])} | {p['struct']} | {p['macd']['text']} | 观望 | — | — | — | — | — | — |")
        else:
            lines.append("| {n} | {px} | {st} | {mc} | {sd} | {e0}~{e1} | {sl} | {t1} | {t2} | {rr:.1f}:1 | {nt}U |".format(
                n=p["name"], px=fmt(p["price"]), st=p["struct"], mc=p["macd"]["text"],
                sd={"long": "只做多", "short": "只做空"}[p["side"]],
                e0=fmt(p["entry"][0]), e1=fmt(p["entry"][1]), sl=fmt(p["stop"]),
                t1=fmt(p["t1"]), t2=fmt(p["t2"]), rr=p["rr"], nt=fmt(p["notional"])))
    lines += ["", "## 执行条件", ""]
    for p in plans:
        if p["side"] == "wait":
            lines.append(f"- **{p['name']}**：不做。{p['wait_reason']}；{p['invalid']} 再重新评估。")
        else:
            d = "回踩" if p["side"] == "long" else "反抽"
            lines.append(f"- **{p['name']}**：等{d}进 {fmt(p['entry'][0])}~{fmt(p['entry'][1])} 才动手，"
                         f"止损 {fmt(p['stop'])}，先看 {fmt(p['t1'])}，破了看 {fmt(p['t2'])}。"
                         f"{p['invalid']} 即失效，无条件走。")
    lines += ["", "## 多周期与柱子（日线 / 4H / 1H）", "",
              "| 标的 | 日线 | 4H | 1H | 共振 |",
              "|---|---|---|---|---|"]
    for p in plans:
        t = p["tfs"]
        lines.append(f"| {p['name']} | {t[0][1]['bias']}·{t[0][1]['struct']} "
                     f"| {t[1][1]['bias']}·{t[1][1]['struct']} "
                     f"| {t[2][1]['bias']}·{t[2][1]['struct']} | {p['consensus']} |")
    lines += ["", "**柱子判断口径**：柱的放大/缩小按绝对值算（柱越粗动能越强，与正负无关）；"
              "「已过峰」= 10 根内最粗的柱出现在 2 根以上之前，且当前强度不足峰值的 75%，说明这段动能正在退潮。", ""]
    for p in plans:
        lines += render_tfs(p) + [""]
    lines += ["> 动能只做时机过滤，不改结构方向：动能向下加速时不做多，向上加速时不做空。",
              "> 计划不是预测。价格没到进场区就不做，这是纪律。", ""]
    return "\n".join(lines)


def bark_summary(plans, cfg):
    key = (cfg.get("bark_key") or "").strip()
    if not key:
        log("[DRY-RUN] 未配置 bark_key，跳过推送")
        return
    rows = []
    for p in plans:
        if p["side"] == "wait":
            rows.append(f"{p['name']} 观望")
        else:
            sd = "多" if p["side"] == "long" else "空"
            rows.append(f"{p['name']} {sd} {fmt(p['entry'][0])}-{fmt(p['entry'][1])} 损{fmt(p['stop'])}")
    body = " | ".join(rows)
    server = (cfg.get("bark_server") or "https://api.day.app").rstrip("/")
    q = urllib.parse.quote
    url = f"{server}/{key}/{q('今日合约计划')}/{q(body)}?" + urllib.parse.urlencode(
        {"group": "交易计划", "sound": cfg.get("sound", "alarm"), "level": "timeSensitive"})
    try:
        r = urllib.request.urlopen(url, timeout=15, context=CTX).read().decode("utf-8", "ignore")
        log("bark " + ("ok" if '"code":200' in r else "fail"))
    except Exception as e:
        log(f"bark err {e}")


def main():
    cfg = load_json(CONFIG_PATH, {})
    watch = cfg.get("watchlist") or DEFAULT_WATCH
    equity = float(cfg.get("account_equity", 10000))

    plans = []
    for item in watch:
        try:
            plans.append(build_plan(item, cfg, equity))
        except Exception as e:
            log(f"{item.get('name')} 失败：{e}")

    date_str = f"{datetime.now():%Y-%m-%d}"
    print(f"=== 合约计划 {date_str}  权益 {equity:,.0f}U  风险 {RISK_PCT*100:.0f}%/笔 ===")
    for p in plans:
        print(render_line(p))

    os.makedirs(PLAN_DIR, exist_ok=True)
    path = os.path.join(PLAN_DIR, f"{date_str}-计划.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_md(plans, equity, date_str))
    print(f"\n已写入 {path}")

    if "--push" in sys.argv:
        bark_summary(plans, cfg)


if __name__ == "__main__":
    main()
