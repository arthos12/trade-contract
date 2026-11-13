# -*- coding: utf-8 -*-
"""
跟单信号监控 -> Bark 推送（支持 OKX / 币安）

数据源（均为公开接口，无需登录、无需 API key）：
  OKX   当前持仓：/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode=xxx
  Binance 当前持仓：/bapi/futures/v1/friendly/future/copy-trade/lead-data/positions?portfolioId=xxx
         （返回带单员所有 symbol 的持仓状态，空仓需按 positionAmount 过滤）

两个平台都只公开：开仓均价 / 杠杆 / 仓位 / 现价 / 浮动盈亏。
**都不公开止损价**，止损需自己按结构位定。

推送事件：新开仓、平仓、加减仓

用法：
  python monitor.py --once     # 跑一轮（调试）
  python monitor.py            # 常驻轮询
"""

import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
LOG_DIR = os.path.join(BASE, "logs")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "clienttype": "web",
    "lang": "zh-CN",
    "accept": "*/*",
}
OKX = "https://www.okx.com"
BINANCE = "https://www.binance.com"

_CFG_REF = {}  # 运行时配置，用于取 proxy


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line)
    except Exception:
        pass
    with open(os.path.join(LOG_DIR, "monitor.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _opener():
    """有 proxy 配置就走代理，否则直连。"""
    cfg = _CFG_REF.get("cfg") or {}
    proxy = (cfg.get("proxy") or "").strip()
    handlers = [urllib.request.HTTPSHandler(context=CTX)]
    if proxy:
        handlers.insert(0, urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def http_json(url, timeout=20, retries=3):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            return json.loads(_opener().open(req, timeout=timeout).read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(2)
    raise last


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 取数（统一结构）

def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def fetch_okx(code):
    """返回统一结构列表。"""
    url = f"{OKX}/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode={code}"
    d = http_json(url)
    if d.get("code") != "0":
        raise RuntimeError(f"OKX: {d.get('msg') or d.get('code')}")
    out = []
    for p in (d.get("data") or []):
        if not p:
            continue
        symbol = p.get("instId") or ""
        side = (p.get("posSide") or "").lower()
        if side not in ("long", "short"):
            # net（单向持仓）不给方向，用 (现价-均价)×浮盈 的符号推断
            o, m, u = _num(p.get("openAvgPx")), _num(p.get("markPx")), _num(p.get("upl"))
            if o and u:
                side = "long" if (m - o) * u >= 0 else "short"
            else:
                side = ""
        out.append({
            "platform": "okx",
            "symbol": symbol,
            "side": side,
            "lever": p.get("lever") or "",
            "entry": _num(p.get("openAvgPx")),
            "mark": _num(p.get("markPx")),
            "qty": p.get("subPos") or "",
            "extra": f"保证金 {fmt_price(p.get('margin'))}",
            "upl": _num(p.get("upl")),
            "upl_ratio": p.get("uplRatio") or "",
            "raw_key": f"{symbol}|{side}",
        })
    return out


def fetch_binance(pid):
    """币安返回带单员所有 symbol 的持仓状态（含大量空仓），按 positionAmount 过滤。"""
    url = f"{BINANCE}/bapi/futures/v1/friendly/future/copy-trade/lead-data/positions?portfolioId={pid}"
    d = http_json(url)
    if d.get("success") is False or d.get("code") not in ("000000", None):
        raise RuntimeError(f"Binance: {d.get('message') or d.get('code')}")
    rows = d.get("data") or []
    out = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        amt = _num(p.get("positionAmount"))
        if abs(amt) < 1e-12:      # 空仓
            continue
        symbol = p.get("symbol") or ""
        ps = (p.get("positionSide") or "BOTH").upper()
        if ps == "LONG":
            side = "long"
        elif ps == "SHORT":
            side = "short"
        else:
            e, m, u = _num(p.get("entryPrice")), _num(p.get("markPrice")), _num(p.get("unrealizedProfit"))
            side = "long" if (m - e) * u >= 0 else "short" if e and u else ""
        out.append({
            "platform": "binance",
            "symbol": symbol,
            "side": side,
            "lever": p.get("leverage") or "",
            "entry": _num(p.get("entryPrice")),
            "mark": _num(p.get("markPrice")),
            "qty": f"{abs(amt):g}",
            "extra": f"名义 {fmt_price(abs(_num(p.get('notionalValue'))))}",
            "upl": _num(p.get("unrealizedProfit")),
            "upl_ratio": "",
            "raw_key": f"{symbol}|{side}",
        })
    return out


def fetch(trader):
    platform = (trader.get("platform") or "okx").lower()
    if platform == "binance":
        pid = (trader.get("portfolio_id") or trader.get("unique_code") or "").strip()
        if not pid:
            raise RuntimeError("未配置 portfolio_id")
        return fetch_binance(pid)
    code = (trader.get("unique_code") or "").strip()
    if not code:
        raise RuntimeError("未配置 unique_code")
    return fetch_okx(code)


# ---------------------------------------------------------------- 推送

def bark(cfg, title, body, link=None):
    key = (cfg.get("bark_key") or "").strip()
    if not key:
        log(f"[DRY-RUN] {title} | {body}")
        return True
    server = (cfg.get("bark_server") or "https://api.day.app").rstrip("/")
    q = urllib.parse.quote
    url = f"{server}/{key}/{q(title)}/{q(body)}"
    params = {"group": "交易信号", "sound": cfg.get("sound", "alarm"), "level": "timeSensitive"}
    if link:
        params["url"] = link
    url += "?" + urllib.parse.urlencode(params)
    try:
        resp = urllib.request.urlopen(url, timeout=15, context=CTX).read().decode("utf-8", "ignore")
        ok = '"code":200' in resp or '"code": 200' in resp
        log(f"bark {'ok' if ok else 'fail'} {title}")
        return ok
    except Exception as e:
        log(f"bark err {e}")
        return False


def fmt_price(v):
    try:
        f = float(v)
        if abs(f) >= 1000:
            return f"{f:,.2f}"
        if abs(f) >= 1:
            return f"{f:.2f}"
        return f"{f:.6f}"
    except Exception:
        return str(v)


def side_text(p):
    return {"long": "多", "short": "空"}.get(p.get("side"), "方向待定")


def plat_text(p):
    return "币安" if p.get("platform") == "binance" else "OKX"


def build(kind, name, p, prev=None, last_mark=None):
    if kind == "new":
        title = f"【{name}】新开仓 {p['symbol']}"
        body = " | ".join([
            f"{side_text(p)} {p['lever']}x",
            f"均价 {fmt_price(p['entry'])}",
            f"现价 {fmt_price(p['mark'])}",
            f"持仓 {p['qty']}" + (f" | {p['extra']}" if p.get("extra") else ""),
        ])
    elif kind == "close":
        title = f"【{name}】已平仓 {p['symbol']}"
        body = " | ".join([
            f"{side_text(p)} {p['lever']}x",
            f"开仓均价 {fmt_price(p['entry'])}",
            f"最后价 {fmt_price(last_mark if last_mark not in (None, '') else p['mark'])}",
            f"浮盈 {p['upl']:,.2f}" + (f" ({p['upl_ratio']})" if p.get("upl_ratio") else ""),
        ])
    else:
        title = f"【{name}】加减仓 {p['symbol']}"
        body = " | ".join([
            f"{side_text(p)} {p['lever']}x",
            f"仓位 {prev['qty']} → {p['qty']}",
            f"均价 {fmt_price(p['entry'])}",
            f"现价 {fmt_price(p['mark'])}",
        ])
    body += f" | {plat_text(p)}"
    return title, body


def run_once(cfg, state):
    _CFG_REF["cfg"] = cfg
    pushed = 0
    for trader in cfg.get("traders", []):
        name = trader.get("name") or "未命名"
        try:
            data = fetch(trader)
        except Exception as e:
            log(f"{name} 拉取失败：{e}")
            continue

        tstate = state.setdefault("traders", {}).setdefault(name, {})
        prev = tstate.get("positions", {})
        initialized = tstate.get("initialized", False)

        cur = {p["raw_key"]: p for p in data}

        if not initialized:
            tstate["positions"] = cur
            tstate["initialized"] = True
            log(f"{name} 首次运行，已记录现有 {len(cur)} 个持仓（不推送）")
            continue

        if not cur:
            log(f"{name} 当前无持仓（接口返回 0 条）")

        for k, p in cur.items():
            if k not in prev:
                ti, bo = build("new", name, p)
                bark(cfg, ti, bo, trader.get("link"))
                pushed += 1
                log(ti + " " + bo)

        for k, p in prev.items():
            if k not in cur:
                last_mark = state.get("last_mark", {}).get(k)
                ti, bo = build("close", name, p, last_mark=last_mark)
                bark(cfg, ti, bo, trader.get("link"))
                pushed += 1
                log(ti + " " + bo)

        for k, p in cur.items():
            if k in prev and str(p.get("qty")) != str(prev[k].get("qty")):
                ti, bo = build("size", name, p, prev=prev[k])
                bark(cfg, ti, bo, trader.get("link"))
                pushed += 1
                log(ti + " " + bo)

        tstate["positions"] = cur
        state.setdefault("last_mark", {}).update({k: p["mark"] for k, p in cur.items()})

    return pushed


def main():
    cfg = load_json(CONFIG_PATH, {})
    if not cfg:
        log("config.json 不存在或为空")
        return
    _CFG_REF["cfg"] = cfg
    state = load_json(STATE_PATH, {})

    once = "--once" in sys.argv
    poll = int(cfg.get("poll_seconds", 180))
    log(f"启动：{len(cfg.get('traders', []))} 个监控对象，间隔 {poll}s，{'单轮' if once else '常驻'}，proxy={cfg.get('proxy') or '直连'}")

    while True:
        try:
            run_once(cfg, state)
            save_json(STATE_PATH, state)
        except Exception as e:
            log(f"轮询异常：{e}")
        if once:
            break
        time.sleep(poll)


if __name__ == "__main__":
    main()
