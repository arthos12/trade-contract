# 合约监控系统 · 使用说明

> 一句话：把熬鹰、TONY 这类交易员在交易所带单页的**开仓 / 平仓 / 加减仓**变化，推送到你的手机（Bark）。
> 它给你的是「他已经做了什么」，不是「你该不该做」。

---

## 1. 它能做什么，不能做什么

| 能做 | 不能做 |
|---|---|
| 每 3 分钟轮询一次他的公开持仓 | 给你他的止损价（平台不公开） |
| 识别新开仓 / 平仓 / 加减仓三类事件 | 提前告诉你「他准备开」 |
| 推送方向、均价、现价、杠杆、仓位、浮盈 | 判断这个信号值不值得跟 |
| 首次启动只记录不推送，避免刷屏 | 自动下单、自动跟单 |
| 状态去重，同一仓位不重复推 | 覆盖币安以外的所有平台 |

**核心认知**：信号天然滞后。你收到的是「他 3 分钟前开了」，不是「现在进场」。它只能当**验证输入和情绪锚**，不能当入场触发。

---

## 2. 数据从哪来

两个平台，都是公开接口，**不需要登录、不需要 API key**。

| 平台 | 接口 | 关键字段 |
|---|---|---|
| 币安 | `/bapi/futures/v1/friendly/future/copy-trade/lead-data/positions?portfolioId=xxx` | `entryPrice` `markPrice` `leverage` `positionAmount` `notionalValue` `unrealizedProfit` `positionSide` |
| OKX | `/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode=xxx` | `openAvgPx` `markPx` `lever` `subPos` `margin` `upl` `uplRatio` `posSide` |

三个必须知道的接口事实：

1. **都没有止损 / 止盈字段**。止损只能自己按结构位定，或把「平仓」事件当止损信号。
2. **方向可能缺失**。OKX 常返 `net`，币安可能返 `BOTH`，程序用「(现价 − 均价) × 浮盈 的符号」推断，推不出的显示「方向待定」。
3. **币安会返回大量空仓**。某账号 48 条记录里只有 4 条真在仓，程序按 `positionAmount ≠ 0` 过滤。

---

## 3. 怎么装

### 3.1 Windows（本机常驻）

```bat
copy config.example.json config.json
:: 编辑 config.json：填 bark_key / proxy / traders
python monitor.py --once     :: 先跑一轮调试
start_monitor.bat            :: 常驻后台（pythonw，无窗口）
```

开机自启：

```bat
schtasks /Create /TN TalksignalMonitor /TR "D:\project\合约\信号监控\start_monitor.bat" /SC ONLOGON /RL LIMITED /F
```

### 3.2 Linux 服务器（24 小时）

```bash
git clone https://github.com/arthos12/trade-contract.git
cd trade-contract
cp config.example.json config.json
vi config.json
sudo bash install.sh          # 装到 /opt/talksignal，systemd 开机自启
journalctl -u talksignal -f   # 看日志
```

### 3.3 部署前必须做的网络自检

```bash
curl -s -m 10 -o /dev/null -w "%{http_code}\n" \
  "https://www.okx.com/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode=9F25A5FBE6CFF912"
```

- 返回 `200` → 这台机器能跑
- 返回 `000` 或超时 → 出不去，先解决网络（换海外地域，或在服务器上装代理并把代理地址填进 `proxy`）

**国内机器访问 OKX / 币安必须走代理。** 本机开着 Clash 不代表服务器也能出去——`127.0.0.1` 只指向本机。

---

## 4. 配置怎么填

```json
{
  "bark_key": "你的 Bark key",
  "bark_server": "https://api.day.app",
  "proxy": "http://127.0.0.1:7897",
  "poll_seconds": 180,
  "sound": "alarm",
  "traders": [
    { "name": "熬鹰", "platform": "binance", "portfolio_id": "xxx", "unique_code": "", "link": "" },
    { "name": "TONY", "platform": "okx", "unique_code": "xxx", "link": "" }
  ]
}
```

| 字段 | 从哪拿 |
|---|---|
| `bark_key` | Bark App 首页，`https://api.day.app/XXXXXXXX` 里的 `XXXXXXXX` |
| `proxy` | 本机 Clash 一般是 `http://127.0.0.1:7897`；云主机填实际代理地址，没有就留空 |
| `portfolio_id`（币安） | 带单页链接 `.../copy-trading/lead-details/<这串数字>` |
| `unique_code`（OKX） | 带单页链接里的 ID，形如 `9F25A5FBE6CFF912` |

> 币安带单榜单**没有搜索框**（只有推荐 / 全部项目 / 信号 / 我的收藏），按名字查不到人，只能从带单页链接里取 ID。

---

## 5. 收到推送后怎么用

推送长这样：

```
【熬鹰】新开仓 BTCUSDT
空 20x | 均价 80,821.40 | 现价 68,765.95 | 持仓 3 | 名义 206,297.86 | 币安
```

**不要看到就下单。** 按你的两规则过两道闸：

1. **方向闸**：这个标在你自己的日线结构里是同向吗？他空 BTC，你看到的是上升结构——不跟。
2. **位置闸**：他的开仓价离最近结构支撑/压力多远？已经追出去 3% 以上——不跟。

过闸之后，还要回答一句：**如果这单亏了，我在哪里认错？** 答不出来就不做。

信号的三类用法，按价值排序：

| 用法 | 说明 |
|---|---|
| 校准判断 | 他和你同向开仓，说明你的结构判断可能对（只是可能） |
| 提醒风险 | 他突然大规模减仓，说明风向可能在变 |
| 发现自己漏掉的标的 | 你没看的币他重仓了，去看一眼结构 |

---

## 6. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 日志 `code=000` / 超时 | 网络出不去 | 填 `proxy` 或换海外机房 |
| `bark fail` | key 错 / 未填 | 检查 `bark_key`，先跑 `--once` 看是否 `[DRY-RUN]` |
| 首次启动没有推送 | 设计如此 | 首次只记录持仓，第二轮起才比对 |
| 币安拉不到数据 | `portfolio_id` 错 | 从带单页链接重新取 |
| `install.sh` 报 `$'\r': command not found` | 换行被转成 CRLF | 仓库已加 `.gitattributes` 强制 LF；用 git clone，别手动复制文件 |
| 推送重复 | `state.json` 被删/损坏 | 保留 `state.json`，它在 `.gitignore` 里不进仓库 |

---

## 7. 维护

```bash
# 更新
cd trade-contract && git pull
sudo systemctl restart talksignal

# 看状态
systemctl status talksignal
tail -n 50 /opt/talksignal/logs/monitor.log

# 清空去重状态（会重新只记录不推送一轮）
rm /opt/talksignal/state.json
```

**安全**：`config.json`、`state.json`、`logs/` 都在 `.gitignore` 里。仓库只有 `config.example.json` 占位模板，**任何 key 和 ID 都不入库**。

---

## 8. 与交易记忆体系的关系

这套工具不是独立存在的，它在「研究 → 决策 → 交易 → 复盘」闭环里只占一环：

| 环节 | 工具做什么 | 记忆体系在哪 |
|---|---|---|
| 研究 | 提供别人的持仓作为输入 | `07-市场研究.md` |
| 决策 | **什么都不做**，决策靠你自己过两闸 | `03-交易策略.md` |
| 交易 | 不参与，不自动下单 | `04-交易记录.md` |
| 复盘 | 提供「他跟对了/跟错了」的事实 | `05-复盘总结.md` 错题集 |

所以它的边界很明确：**只提供事实，不提供判断**。

---

## 9. 已知限制（不改）

- 不给止损价：平台不公开，别指望
- 信号滞后：轮询间隔决定了最少滞后 3 分钟
- 只接两家：币安、OKX
- 不做自动交易：这是故意的，不是没做

---

*本文档随代码一起维护。改动接口或配置项时同步更新。*
