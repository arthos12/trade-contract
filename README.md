# trade-contract · 合约监控

把「熬鹰 / TONY」这类交易员在交易所带单页的开仓、平仓、加减仓变化，推送到 Bark（iOS）。

## 它做什么

- 轮询 OKX / 币安公开带单接口（无需登录、无需 API key）
- 与上一轮持仓做比对，识别三类事件：
  - **新开仓** → 推「方向 / 开仓均价 / 杠杆 / 张数 / 保证金」
  - **已平仓** → 推「开仓均价 / 最后价 / 浮动盈亏」
  - **加减仓** → 推「仓位变化 / 均价 / 现价」
- 通过 Bark 推送到手机，分组「交易信号」，带原文链接
- 首次启动只记录现有持仓，不推送（避免开服刷屏）

## 目录

```
monitor.py            主程序（纯标准库，零依赖）
config.example.json   配置模板（复制成 config.json 再填）
install.sh            Linux 一键部署（systemd）
start_monitor.bat     Windows 后台启动（pythonw，无窗口）
```

## 快速开始

### Windows

```bat
copy config.example.json config.json
:: 编辑 config.json：填 bark_key、proxy、traders
python monitor.py --once     :: 单轮调试
start_monitor.bat            :: 常驻后台
```

开机自启：

```bat
schtasks /Create /TN TalksignalMonitor /TR "D:\path\to\start_monitor.bat" /SC ONLOGON /RL LIMITED /F
```

### Linux

```bash
cp config.example.json config.json
# 编辑 config.json
sudo bash install.sh          # 装到 /opt/talksignal，systemd 开机自启
journalctl -u talksignal -f   # 看日志
```

部署前先自检网络：

```bash
curl -s -m 10 -o /dev/null -w "%{http_code}\n" \
  "https://www.okx.com/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode=9F25A5FBE6CFF912"
```

返回 `200` 才能跑；返回 `000`/超时说明这台机器出不去。

## 配置说明

| 字段 | 说明 |
|---|---|
| `bark_key` | Bark App 里的推送 key |
| `bark_server` | 默认 `https://api.day.app`，自建可改 |
| `proxy` | 形如 `http://127.0.0.1:7897`，留空直连。**国内机器访问 OKX 必须填** |
| `poll_seconds` | 轮询间隔，默认 180 秒 |
| `traders[].platform` | `okx` 或 `binance`，不填默认 `okx` |
| `traders[].unique_code` | OKX 用：带单员 ID，形如 `9F25A5FBE6CFF912` |
| `traders[].portfolio_id` | 币安用：带单页链接 `.../copy-trading/lead-details/{portfolio_id}` 末尾那串数字 |
| `traders[].link` | 带单页链接，会附在推送里 |

## 数据源

| 平台 | 接口 | 关键字段 |
|---|---|---|
| OKX | `/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode=xxx` | `openAvgPx` `markPx` `lever` `subPos` `margin` `upl` `posSide` |
| 币安 | `/bapi/futures/v1/friendly/future/copy-trade/lead-data/positions?portfolioId=xxx` | `entryPrice` `markPrice` `leverage` `positionAmount` `notionalValue` `unrealizedProfit` `positionSide` |

共同点：**都没有止损 / 止盈字段**。方向在两边都可能缺（OKX 常返 `net`，币安返 `BOTH`），程序用「(现价−均价)×浮盈 的符号」推断。

## 已知限制

1. **带单页没有止损价**。交易所只公开开仓均价、杠杆、仓位、保证金、浮盈，不公开带单者的止损。止损要自己按结构位定。
2. **信号天然滞后**。你收到的是"他已经开了"，不是"他准备开"，不能当入场触发用。
3. **方向靠推断**。接口的 `posSide` 多为 `net`（单向持仓），不直接给多空；程序用「价差符号 × 浮盈符号」推断，推不出的显示"方向待定"。
4. **只接 OKX 与币安**。两家都只公开合约带单的持仓，且都不提供止损。币安的持仓接口会把该带单员所有 symbol 都返回（含大量空仓），程序已按 `positionAmount ≠ 0` 过滤。

## 安全

`config.json`、`state.json`、`logs/` 已在 `.gitignore` 中，**不要提交**。仓库里只保留 `config.example.json` 占位模板。

## 免责

本工具只做公开数据的读取与提醒，不构成任何投资建议。跟单信号的盈亏由使用者自行承担。
