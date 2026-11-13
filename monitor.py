# -*- coding: utf-8 -*-
"""
跟单信号监控 -> Bark 推送

数据源：OKX 公开带单接口（无需登录）
  - 当前持仓：/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode=xxx
  - 只有 开仓均价 / 杠杆 / 仓位 / 保证金 / 现价 / 浮动盈亏，没有止损价（平台不公开）

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
UA = {"User-Agent": "Mozilla/5.0"}
OKX = "https://www.okx.com"

_CFG_REF = {}  # 运行时的配置，用于取 proxy


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
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
            req = urllib.request.Request(url, headers=UA)
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


def okx_positions(code):
    url = f"{OKX}/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode={code}"
    d = http_json(url)
    if d.get("code") != "0":
        raise RuntimeError(d.get("msg") or d.get("code"))
    return [p for p in (d.get("data") or []) if p]


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
        return f"{f:,.2f}" if abs(f) >= 1 else f"{f:.6f}"
    except Exception:
        return str(v)


def infer_side(p):
    """posSide=net 时接口不给方向，用 (现价-均价) 与 浮盈 的符号相乘推断。"""
    ps = (p.get("posSide") or "").lower()
    if ps in ("long", "short"):
        return ps
    try:
        o = float(p.get("openAvgPx")); m = float(p.get("markPx")); u = float(p.get("upl"))
    except Exception:
        return ""
    if u == 0 or o == 0:
        return ""
    return "long" if (m - o) * u >= 0 else "short"


def side_text(p):
    s = infer_side(p)
    return {"long": "多", "short": "空"}.get(s, "方向待定")


def bark_send(title, body, link):
    return bark(_CFG_REF["cfg"], title, body, link)


def alert_new(name, p, trader):
    title = f"【{name}】新开仓 {p.get('instId')}"
    body = " | ".join([
        f"{side_text(p)} {p.get('lever')}x",
        f"均价 {fmt_price(p.get('openAvgPx'))}",
        f"现价 {fmt_price(p.get('markPx'))}",
        f"仓位 {p.get('subPos')} 张",
        f"保证金 {fmt_price(p.get('margin'))}",
    ])
    return bark_send(title, body, trader.get("link")), title, body


def alert_close(name, p, trader, last_mark):
    title = f"【{name}】已平仓 {p.get('instId')}"
    body = " | ".join([
        f"{side_text(p)} {p.get('lever')}x",
        f"开仓均价 {fmt_price(p.get('openAvgPx'))}",
        f"最后价 {fmt_price(last_mark or p.get('markPx'))}",
        f"浮动盈亏 {p.get('upl')} ({p.get('uplRatio')})",
    ])
    return bark_send(title, body, trader.get("link")), title, body


def alert_size(name, p, prev, trader):
    title = f"【{name}】加减仓 {p.get('instId')}"
    body = " | ".join([
        f"{side_text(p)} {p.get('lever')}x",
        f"仓位 {prev.get('subPos')} → {p.get('subPos')}",
        f"均价 {fmt_price(p.get('openAvgPx'))}",
        f"现价 {fmt_price(p.get('markPx'))}",
    ])
    return bark_send(title, body, trader.get("link")), title, body


def run_once(cfg, state):
    _CFG_REF["cfg"] = cfg
    pushed = 0
    for trader in cfg.get("traders", []):
        name = trader.get("name") or "未命名"
        code = (trader.get("unique_code") or "").strip()
        if not code:
            log(f"{name} 未配置 unique_code，跳过")
            continue
        try:
            data = okx_positions(code)
        except Exception as e:
            log(f"{name} 拉取失败：{e}")
            continue

        tstate = state.setdefault("traders", {}).setdefault(name, {})
        prev = tstate.get("positions", {})
        initialized = tstate.get("initialized", False)

        cur = {}
        for p in data:
            cur[f"{p.get('instId')}|{p.get('posSide')}"] = p

        if not initialized:
            tstate["positions"] = cur
            tstate["initialized"] = True
            log(f"{name} 首次运行，已记录现有 {len(cur)} 个持仓（不推送）")
            continue

        for k, p in cur.items():
            if k not in prev:
                _, ti, bo = alert_new(name, p, trader)
                pushed += 1
                log(ti + " " + bo)
        for k, p in prev.items():
            if k not in cur:
                last_mark = state.get("last_mark", {}).get(k)
                _, ti, bo = alert_close(name, p, trader, last_mark)
                pushed += 1
                log(ti + " " + bo)
        for k, p in cur.items():
            if k in prev and str(p.get("subPos")) != str(prev[k].get("subPos")):
                _, ti, bo = alert_size(name, p, prev[k], trader)
                pushed += 1
                log(ti + " " + bo)

        tstate["positions"] = cur
        state.setdefault("last_mark", {}).update({k: p.get("markPx") for k, p in cur.items()})

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
