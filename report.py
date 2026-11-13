# -*- coding: utf-8 -*-
"""
单标的分析报表生成器（固定格式）

用法：
  python report.py SPCXUSDT SPCX        # 指定合约代码与显示名
  python report.py SPCXUSDT SPCX --push # 顺带推一条摘要

输出：reports/<名称>-<日期>.md，章节固定八节，口径与 plan.py 完全一致。
"""

import os
import sys
from datetime import datetime

import plan

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE, "reports")
RISK_PCT = plan.RISK_PCT
MAX_STOP = plan.MAX_STOP_PCT
MIN_RR = plan.MIN_RR
fmt = plan.fmt


def stats(kl):
    c = kl[-1]["c"]
    a = plan.atr(kl)
    v5 = sum(x["v"] for x in kl[-5:]) / 5
    v20 = sum(x["v"] for x in kl[-20:]) / 20
    h20 = max(x["h"] for x in kl[-20:])
    l20 = min(x["l"] for x in kl[-20:])
    h60 = max(x["h"] for x in kl[-60:])
    l60 = min(x["l"] for x in kl[-60:])
    pos = (c - l20) / (h20 - l20) * 100 if h20 > l20 else 50
    worst = min(kl[-30:], key=lambda x: x["c"] / x["o"] - 1)
    return {
        "c": c, "atr": a, "atr_pct": a / c * 100,
        "chg5": (c / kl[-6]["c"] - 1) * 100, "chg10": (c / kl[-11]["c"] - 1) * 100,
        "chg20": (c / kl[-21]["c"] - 1) * 100,
        "vr": v5 / v20 if v20 else 0, "h20": h20, "l20": l20, "h60": h60, "l60": l60,
        "pos": pos, "worst_t": datetime.fromtimestamp(worst["t"] / 1000).strftime("%m-%d"),
        "worst_pct": (worst["c"] / worst["o"] - 1) * 100,
        "ma20": plan.ma(kl, 20), "ma50": plan.ma(kl, 50),
    }


def swing_points(kl, w=3, lookback=60):
    """最近 60 根的局部摆动高低点，给关键价位补充参照，避免只靠最后一组 swing。"""
    seg = kl[-lookback:]
    lows, highs = [], []
    for i in range(w, len(seg) - w):
        if seg[i]["l"] <= min(x["l"] for x in seg[i - w:i + w + 1]):
            lows.append(seg[i]["l"])
        if seg[i]["h"] >= max(x["h"] for x in seg[i - w:i + w + 1]):
            highs.append(seg[i]["h"])
    return lows, highs


def levels(p, s, kl):
    c = s["c"]
    sl, sh = swing_points(kl)
    sup = [x for x in [p.get("swing_low"), s["l20"], s["l60"], s["ma50"], s["ma20"]] + sl if x and x < c]
    res = [x for x in [p.get("swing_high"), s["h20"], s["h60"], s["ma50"], s["ma20"]] + sh if x and x > c]
    sup = sorted(set(round(x, 2) for x in sup), reverse=True)[:3]
    res = sorted(set(round(x, 2) for x in res))[:3]
    return sup, res


def scenario(p, s, sup, res):
    """两套可执行情景。止损优先放结构位；上限 3ATR、下限 1ATR。
    目标必须与进场至少相隔 1ATR，否则破位即到目标、等于没有目标，直接判「不做」。"""
    out = []
    atr = s["atr"]
    max_risk = plan.MAX_STOP_PCT
    min_stop = 1.0 * atr
    gap = 1.0 * atr

    if sup:
        trig = sup[0]
        stop = res[0] if res else trig + 1.5 * atr
        if (stop - trig) > 3 * atr:
            stop = trig + 1.5 * atr
        if (stop - trig) < min_stop:
            stop = trig + min_stop
        below = [x for x in sup if trig - x >= gap]
        tgt = max(below) if below else None
        risk = (stop - trig) / trig if trig else 0
        rr = (trig - tgt) / (stop - trig) if tgt else 0
        if tgt is None:
            ok, note = False, "破位后下方没有 ≥1ATR 的结构空间，等于没有目标，不做"
        elif risk > max_risk:
            ok = False
            note = (f"止损 {risk*100:.1f}% 超过 {max_risk*100:.0f}% 红线 —— ATR {s['atr_pct']:.1f}%，"
                    f"放足 1ATR 就已经越线，此标的在当前波动下不该做")
        elif rr < MIN_RR:
            ok, note = False, f"盈亏比 {rr:.1f}:1 低于 {MIN_RR:.0f}:1 下限"
        else:
            ok, note = True, ""
        out.append({
            "name": "情景 A · 破位做空", "cond": f"日线收盘跌破 {fmt(trig)}",
            "entry": trig, "stop": stop, "target": tgt, "rr": rr, "risk": risk,
            "pos": (plan.RISK_PCT * 10000 / risk) if risk > 0 else 0,
            "ok": ok, "note": note,
        })

    if res:
        trig = res[0]
        stop = sup[0] if sup else trig - 1.5 * atr
        if (trig - stop) > 3 * atr:
            stop = trig - 1.5 * atr
        if (trig - stop) < min_stop:
            stop = trig - min_stop
        above = [x for x in res if x - trig >= gap]
        tgt = min(above) if above else None
        risk = (trig - stop) / trig if trig else 0
        rr = (tgt - trig) / (trig - stop) if tgt else 0
        if tgt is None:
            ok, note = False, "站上后上方没有 ≥1ATR 的结构空间，等于没有目标，不做"
        elif risk > max_risk:
            ok = False
            note = (f"止损 {risk*100:.1f}% 超过 {max_risk*100:.0f}% 红线 —— ATR {s['atr_pct']:.1f}%，"
                    f"放足 1ATR 就已经越线，此标的在当前波动下不该做")
        elif rr < MIN_RR:
            ok, note = False, f"盈亏比 {rr:.1f}:1 低于 {MIN_RR:.0f}:1 下限"
        else:
            ok, note = True, ""
        out.append({
            "name": "情景 B · 站上做多", "cond": f"日线收盘站上 {fmt(trig)}",
            "entry": trig, "stop": stop, "target": tgt, "rr": rr, "risk": risk,
            "pos": (plan.RISK_PCT * 10000 / risk) if risk > 0 else 0,
            "ok": ok, "note": note,
        })
    return out


def build(sym, name, cfg, equity):
    p = plan.build_plan({"symbol": sym, "name": name, "kind": "美股"}, cfg, equity)
    kl = plan.klines(sym, "1d", 220, cfg)
    s = stats(kl)
    sup, res = levels(p, s, kl)
    sc = scenario(p, s, sup, res)
    return p, s, sup, res, sc


def render(sym, name, p, s, sup, res, sc, equity):
    now = datetime.now()
    L = []
    A = L.append
    A(f"# {name} 合约分析报表")
    A("")
    A(f"> 生成时间 {now:%Y-%m-%d %H:%M} ｜ 数据源 币安合约 `{sym}` 日线 220 根 / 4H 150 根 / 1H 200 根")
    A(f"> 账户权益 {equity:,.0f} U ｜ 单笔风险 {RISK_PCT*100:.0f}% ｜ 止损红线 {MAX_STOP*100:.0f}% ｜ 盈亏比下限 {MIN_RR}:1")
    A("")

    A("## 一、结论")
    A("")
    side_txt = {"long": "只做多（等位置）", "short": "只做空（等位置）", "wait": "观望"}[p["side"]]
    A(f"**{side_txt}**。{p['wait_reason'] if p['side'] == 'wait' else '方向已成立，等进场区到价。'}")
    A("")
    A(f"- 大趋势（日线）：{p['tfs'][0][1]['struct']}，价{'在' if s['c'] > s['ma20'] else '跌破'} MA20（{fmt(s['ma20'])}）")
    A(f"- 共振判断：{p['consensus']}")
    A(f"- 唯一可执行触发：{sc[0]['cond'] if sc else '无'}（详见第六节）")
    A("")

    A("## 二、快照")
    A("")
    A("| 项目 | 数值 | 解读 |")
    A("|---|---|---|")
    A(f"| 现价 | {fmt(s['c'])} | — |")
    A(f"| 5 / 10 / 20 日涨跌 | {s['chg5']:+.1f}% / {s['chg10']:+.1f}% / {s['chg20']:+.1f}% | "
      f"{'短期反弹、中期仍弱' if s['chg5'] > 0 and s['chg20'] < 0 else '短期与中期同向' if s['chg5'] * s['chg20'] > 0 else '短中期背离'} |")
    A(f"| 日 ATR | {fmt(s['atr'])}（{s['atr_pct']:.1f}% 价格） | "
      f"{'波动大，止损须按结构位收紧并缩仓' if s['atr_pct'] > 5 else '波动可控'} |")
    A(f"| 量能（5 日/20 日） | {s['vr']:.2f} | {'放量' if s['vr'] > 1.2 else '缩量' if s['vr'] < 0.8 else '持平'} |")
    A(f"| 20 日区间 | {fmt(s['l20'])} ~ {fmt(s['h20'])} ｜ 位置 {s['pos']:.0f}% | "
      f"{'贴区间底' if s['pos'] < 25 else '贴区间顶' if s['pos'] > 75 else '区间中段'} |")
    A(f"| 60 日区间 | {fmt(s['l60'])} ~ {fmt(s['h60'])} | 距高 {(s['c']/s['h60']-1)*100:+.1f}% ｜ 距低 {(s['c']/s['l60']-1)*100:+.1f}% |")
    A(f"| MA20 / MA50 | {fmt(s['ma20'])} / {fmt(s['ma50'])} | "
      f"价对 MA20 {(s['c']/s['ma20']-1)*100:+.1f}% ｜ 对 MA50 {(s['c']/s['ma50']-1)*100:+.1f}% |")
    A(f"| 近 30 日最大单日跌幅 | {s['worst_t']} {s['worst_pct']:.1f}% | 极端波动参考 |")
    A("")

    A("## 三、多周期结构")
    A("")
    A("| 周期 | 结构 | 价 vs MA20 | 偏向 |")
    A("|---|---|---|---|")
    for n, t in p["tfs"]:
        A(f"| {n} | {t['struct']} | {'上方' if t['close'] > t['ma20'] else '下方'}（{fmt(t['ma20'])}） | {t['bias']} |")
    A("")
    A(f"**共振**：{p['consensus']}")
    A("")

    A("## 四、MACD 与柱子（12,26,9）")
    A("")
    A("| 周期 | 交叉 / 零轴 | 柱值 | 走向 | 连续根数 | 同号强度 | 是否过峰 | 判断 |")
    A("|---|---|---|---|---|---|---|---|")
    for n, t in p["tfs"]:
        m, b = t["macd"], t["macd"]["bar"]
        A(f"| {n} | {m['cross']} / {m['zero']} | {m['hist']:+.2f} | {m['trend']} | {b['streak']} | "
          f"{b['ratio']*100:.0f}% | {'是' if b['fading'] else '否'} | {m['label']} |")
    A("")
    A("> 柱的强弱按**同号柱的绝对值**比较；强度 = 当前柱 / 10 根内同号峰值；"
      "过峰 = 同号峰值在 2 根之前且强度跌破 75%。")
    A("")

    A("## 五、关键价位")
    A("")
    A("| 类型 | 价位 | 来源 | 距现价 |")
    A("|---|---|---|---|")
    for x in res:
        tag = "MA20" if abs(x - s["ma20"]) < 0.01 else "MA50" if abs(x - s["ma50"]) < 0.01 else \
            "20日高" if abs(x - s["h20"]) < 0.01 else "60日高" if abs(x - s["h60"]) < 0.01 else "摆动高点"
        A(f"| 阻力 | {fmt(x)} | {tag} | {(x/s['c']-1)*100:+.1f}% |")
    for x in sup:
        tag = "MA20" if abs(x - s["ma20"]) < 0.01 else "MA50" if abs(x - s["ma50"]) < 0.01 else \
            "20日低" if abs(x - s["l20"]) < 0.01 else "60日低" if abs(x - s["l60"]) < 0.01 else "摆动低点"
        A(f"| 支撑 | {fmt(x)} | {tag} | {(x/s['c']-1)*100:+.1f}% |")
    A("")

    A("## 六、情景与计划")
    A("")
    A("| 情景 | 触发条件 | 进场 | 止损 | 目标 | 风险 | 盈亏比 | 名义仓位 | 判定 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for x in sc:
        tgt_txt = fmt(x["target"]) if x["target"] else "—"
        A(f"| {x['name']} | {x['cond']} | {fmt(x['entry'])} | {fmt(x['stop'])} | {tgt_txt} | "
          f"{x['risk']*100:.1f}% | {x['rr']:.1f}:1 | {fmt(x['pos'])}U | {'可执行' if x['ok'] else '不达标'} |")
    A("")
    for x in sc:
        if not x["ok"]:
            A(f"- {x['name']}：{x['note']}")
    A("")
    A("**现在做不做**：两套情景都是「等触发」，触发前不动。价格没到触发条件，计划无效。")
    A("")

    A("## 七、风险与失效")
    A("")
    A(f"- 波动风险：日 ATR {s['atr_pct']:.1f}%，"
      f"{'一根 K 线就能吃掉 5% 止损，必须放到 1.5ATR 以上并缩仓' if s['atr_pct'] > 5 else '止损可放进 5% 红线内'}")
    A(f"- 方向失效：日线收盘站上 {fmt(s['ma20'])} 且柱转正放大 → 空头逻辑作废")
    A(f"- 结构失效：跌破 {fmt(s['l20'])} 后不能收回 → 区间下沿失守，重新评估")
    A("- 数据风险：美股代币合约在美股休市时段 K 线不动，非交易时段的信号不作为触发依据")
    A("- 规则风险：本报表所有价位为规则推导，不含消息面；重大财报/事件前后不做新单")
    A("")

    A("## 八、口径说明")
    A("")
    A("- 结构：日线分形摆动（左右各 3 根），HH+HL 上升 / LH+LL 下降，其余为区间")
    A("- 方向：结构 + 价格与 MA20 同侧（0.3ATR 缓冲）")
    A("- 动能：MACD(12,26,9)，金叉放大禁做空、死叉放大禁做多；只过滤时机，不改方向")
    A("- 进场：不追。做多 max(MA20, 现价−2ATR)，做空 min(MA20, 现价+2ATR)")
    A("- 止损：结构外侧 0.5ATR，结构位超 3ATR 改用 2ATR；情景表用 1.5ATR 兜底")
    A("- 目标：最近 60 根极值，第二目标 3R")
    A(f"- 仓位：账户 {RISK_PCT*100:.0f}% 风险 ÷ 止损距离")
    A(f"- 质量闸：止损 > {MAX_STOP*100:.0f}% 或盈亏比 < {MIN_RR}:1 → 不做")
    A("")
    A("---")
    A(f"*本报表由 `report.py` 按固定模板生成，口径与 `plan.py` 一致，可复现。*")
    return "\n".join(L)


def main():
    if len(sys.argv) < 2:
        print("用法：python report.py <SYMBOL> [名称]")
        return
    sym = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else sym.replace("USDT", "")
    cfg = plan.load_json(plan.CONFIG_PATH, {})
    equity = float(cfg.get("account_equity", 10000))
    p, s, sup, res, sc = build(sym, name, cfg, equity)
    md = render(sym, name, p, s, sup, res, sc, equity)
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"{name}-{datetime.now():%Y-%m-%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"\n已写入 {path}")


if __name__ == "__main__":
    main()
