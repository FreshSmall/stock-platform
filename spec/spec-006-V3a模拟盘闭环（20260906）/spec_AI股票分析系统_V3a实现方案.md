# AI股票分析系统 V3a 实现方案（模拟盘闭环）

> 对应 PRD：spec-006 PRD V3a（BP-V3a-001~006）
> 实施日期：2026-09-06 起立项（ROADMAP 阶段三，~3 周）
> 维护约定：每完成一项勾选对应 checklist 并补"实际偏差"注记。

## 一、模块地图（新增/改动）

```text
backend/app/
├── models/
│   └── paper.py              [新] SaPaperAccount/Order/Trade/Position/Nav 五表
├── alembic/versions/         [新] 迁移：sa_paper_* 五表（含 UK/索引）
├── services/
│   ├── execution.py          [新] 共享撮合层：开盘价撮合、可成交性判定、整手、
│   │                              成本计算、除权股数调整（回测与模拟盘共用）
│   ├── portfolio_backtest_service.py [改] 撮合内部函数改调 execution（行为不变，
│   │                              既有测试锁定）
│   ├── paper_service.py      [新] 账户 CRUD / paper_tick 四段推进 /
│   │                              净值·归因 / drift 对比 / 告警检查
│   └── admin_service.py      [改] 注册 paper_tick 任务（含补跑入参）
├── api/
│   └── paper.py              [新] /paper/accounts CRUD、/nav、/orders、/drift、
│                                  /tick（手动补跑）
├── scheduler.py              [改] 注册 paper_tick（工作日 20:00，数据 settle 后）
└── factor/multi_factor.py    [改] 无（配置直读 PRESETS；不改打分管线）

frontend/src/
├── api/paper.ts              [新] 账户/净值/订单/drift/tick 接口
├── pages/Paper.tsx           [新] 模拟盘页（总览/持仓/调仓/drift/告警条）
├── components/Layout/AppLayout.tsx [改] 导航加「模拟盘」
└── pages/Admin.tsx           [改] 任务区加 paper_tick 手动触发（含补跑日期）
```

## 二、关键实现约定

1. **共享撮合层（本方案最重要的重构）**：`portfolio_backtest_service` 现有的
   `_opens_on/_sellable_on/_buyable_on` 及撮合循环内的成交价、整手、成本计算，
   抽为 `execution.py` 纯函数（输入：订单意图 + 当日 open/trade_status/成本参数；
   输出：成交/跳过/顺延 + 费用明细）。抽取 PR 单独提交，跑全量既有回测测试锁定
   行为不变——这是零漂移测试与 drift 可比性的前提。
2. **paper_tick 状态机（单事务边界按段划分）**：
   `撮合段(T-1 订单 × T 日开盘/状态) → 信号段(调仓日? 守卫 → 打分 → diff → 订单)
   → 估值段(收盘估值 → nav/position 快照 upsert) → 告警段(rule 阈值 → check 落库)`。
   每段独立事务：撮合段成功而信号段守卫拦截时，持仓与净值仍正确推进。
3. **幂等**：订单 UK(account, signal_date, stock, side)；tick 以"当日快照是否已
   存在"为重入探针，补跑按日期区间逐日重放，重复执行只补缺口不重复成交。
4. **除权处理**：估值前查持仓股 `sa_adjust_factor` 当日因子/前日因子比 ≠ 1 →
   shares ×= 比值（qfq 口径市值连续）；因子缺失 → 告警 `corporate_action` 并将
   该股标记禁交易。回测引擎不回改（短窗口容差已在 V2.2 注记）。
5. **数据完备性守卫**：复用 `app.data.backfill.settled_counts` 口径——当日行数
   < 近期基线 × 0.9 → 不生成信号、落 `data_incomplete` 告警；23:00 重试同步后
   次日 tick 自然补上（信号延迟一天，真实反映数据现实）。
6. **drift 口径**：模拟盘 nav 序列 vs `run_mf_backtest(同 config, 同区间)` 净值
   序列；对齐交易日；周频聚合收益差；持仓重合度按调仓日 top-N Jaccard；按股票
   分解 = Σ(模拟独有股收益贡献) − Σ(回测独有股收益贡献)。回测 run 照常落
   sa_backtest_run（strategy=mf_portfolio, params 记录 config 指纹），drift 请求
   即时计算不落库。
7. **归因口径**：超额 = Π(1+r_paper) − Π(1+r_bench)；成本拖累 = −Σ费用/初始资金；
   现金拖累 = Σ(现金权重_t × r_bench_t)；选股贡献 = 超额 − 成本 − 现金（残差）。
8. **告警编码**：`check_name='paper_health'`，`metric_name='<metric>:<account_id>'`，
   阈值规则首次运行播种进 `sa_data_quality_rule`（同 factor_health 模式，零新表）。
9. **调度**：`CronTrigger(hour=20, minute=0, day_of_week="mon-fri")`，走
   `_run_admin_task("paper_tick")`（`sa_admin_task_log` 记录）；节假日/周末由
   tick 内交易日历判断跳过。补跑区间 > 5 交易日 → `run_task_async` 长任务链路。

## 三、API 契约（新增）

```text
POST /api/v1/paper/accounts              创建账户
  body: { name, config: { preset|factors, top_n=10, freq='W', initial_cash=1000000,
          cost: {...}, pool, exclude_st, exclude_suspended, neutralize,
          liquidity_top_k=1000, benchmark='sh000001' } }
  → { id }

GET  /api/v1/paper/accounts              → [{ id, name, status, config, latest_nav,
                                             cum_ret, drawdown, open_alerts }]
GET  /api/v1/paper/accounts/{id}         → 详情（含当前持仓与指标）
POST /api/v1/paper/accounts/{id}/status  body: { action: 'pause'|'resume'|'stop' }
GET  /api/v1/paper/accounts/{id}/nav?start&end
  → { series: [{ trade_date, nav, daily_ret, benchmark_nav, drawdown }],
      attribution: { excess, cost_drag, cash_drag, selection } }
GET  /api/v1/paper/accounts/{id}/orders?start&end&status
  → [{ signal_date, exec_date, stock_code, side, shares, status, fail_reason,
       trade?: { price, amount, fees } }]
GET  /api/v1/paper/accounts/{id}/drift?start&end&run
  → { weekly_diff: [...], cum_nav_paper[], cum_nav_backtest[], turnover_diff,
      holding_jaccard: [...], by_stock: [...] }
POST /api/v1/paper/accounts/{id}/tick   body: { dates?: [yyyy-mm-dd, ...] }
  → { run_id }   （>5 日补跑走异步，前端轮询 sa_admin_task_log）
```

## 四、实施顺序与 checklist

> 顺序原则：先地基（表 + 共享撮合层）再引擎（tick）再跟踪（归因/drift）再门面（前端/告警）。
> 每步以"零漂移"与"幂等"两条测试主线的推进为验收。

### T3.1 模拟账户模型（BP-V3a-001）
- [x] models/paper.py 五表 + models/__init__ 导出
- [x] Alembic 迁移（五表、UK、account_id 索引）；downgrade 可回滚（up/down/up 全周期验证）
- [x] 测试：UK 幂等 upsert；重复账户名拒绝（test_paper_models.py 4 passed）

### 共享撮合层抽取（T3.3 前置，独立交付）
- [x] execution.py：fetch_open_prices / fetch_tradability / fill_price / board_lot_shares / cost_breakdown / match_fill
- [x] portfolio_backtest_service 改调 execution，**既有组合回测测试全绿（行为不变）**
- [x] 测试：test_execution.py 7 用例（费用与 cost_model 总额同源、方向性、整手、三分支）

### T3.2 信号→订单生成器（BP-V3a-002）
- [x] 调仓日判定：`_qualifies_rebalance`（回放=回测口径；实盘因果守卫见偏差注记 ③）
- [x] 目标持仓：`portfolio_backtest_service.scope_and_score_targets`（回测 Pass1 同一函数，见偏差注记 ②）
- [x] diff 订单生成（paper_analytics.diff_orders；rebalance_tol 默认禁用，见偏差注记 ⑥）
- [x] 数据完备性守卫（settled_counts 口径，仅当日实盘生效）
- [x] 测试：test_paper_engine 回放断言（信号日 ⊆ N 日调仓日、T+1 执行）

### T3.3 模拟撮合引擎（BP-V3a-003）
- [x] paper_tick 撮合段：pending 订单 × 当日 open/status → execution 撮合（卖先买后、买单按目标排名序）
- [x] 卖单顺延每日重试（deferred），>14 天历法（≈10 交易日）告警 sell_stuck
- [x] 资金约束：先卖后买、整手、现金不足跳过（与回测逐字一致）
- [x] 除权处理：一致性跳闸（见偏差注记 ④，替代原"按因子调股"方案）
- [x] 估值段：position/nav 快照 upsert；停牌沿用最近收盘价
- [x] **零漂移测试：2 个月逐日回放 vs run_mf_backtest 同参数净值逐日一致（±5e-6）**
- [x] 幂等测试：同日重复 tick 不产生新行（already_done）
- [x] 资产守恒测试：total_asset = cash + Σ持仓市值（逐日断言）

### T3.4 净值归因与 drift（BP-V3a-004）
- [x] 归因分解（paper_analytics.attribution：excess/cost_drag/cash_drag/selection）
- [x] drift 计算（run_drift：调 run_mf_backtest + drift_compare + 持仓 Jaccard）
- [x] 测试：test_paper_analytics 14 用例（手算值锁定）；drift 端到端由零漂移测试隐式覆盖

### T3.6 监控告警与日报（BP-V3a-006）
- [x] 告警段检查（drawdown/turnover/sell_stuck/signal_empty/data_incomplete/corporate_action）
      + 规则播种（sa_data_quality_rule，check_name=paper_health）
- [x] 日报摘要：nav 快照行即当日摘要（净值/收益/现金/市值/回撤），页面可查
- [x] 测试：alert_eval 纯函数 9 分支；回放路径告警写入随引擎测试验证

### 调度与 API
- [x] api/paper.py 8 端点 + main.py 路由注册
- [x] scheduler 注册（工作日 20:00）
- [x] admin_service 任务注册（paper_tick 进 _LONG_TASKS）+ POST /admin/paper/run 长任务触发
- [ ] API 层独立测试（端点薄、由 service 层测试覆盖，留待下批补）

### T3.5 前端（BP-V3a-005）
- [x] api/paper.ts + 类型
- [x] Paper.tsx：账户选择/创建、总览卡、净值 vs 基准、持仓表、调仓表（五色状态徽标）、drift 卡、手动补跑
- [x] AppLayout 导航 + App.tsx 路由；Admin 任务区 paper_tick 触发（复用 FactorHealthPanel 轮询模式）
- [x] npm run build（tsc）通过

### 上线演练与收尾
- [x] ruff 全部新文件通过（main.py E402 / admin_service:358 为存量未触碰）
- [x] 全量 pytest（交付记录见下）
- [x] 真实数据演练：首个账户 v2_reversal/W/top10/100 万，历史回放建仓 + 成交抽查
- [ ] 开启每日调度，进入 M3 观察期（≥4 周），每周核对 drift 卡——**运营进行中**
- [x] Backlog BP-V3a-001~006 状态更新；分逻辑 commit

### 实际偏差注记（2026-09-06 交付时）

1. **除权处理实现为"一致性跳闸"而非"按因子调股"**：估值沿用 qfq 口径（与回测一致），
   价格序列本身跨除权日连续，持股数若再按因子比调整会双计收益。改为：sa_adjust_factor
   跳变日核对 qfq 收益与 raw 收益（sa_kline_daily）一致性，偏差 >3% 判 qfq 数据断裂 →
   corporate_action 告警人工介入。PRD 原文"调整持股数"表述由本注记修正。
2. **信号段直接复用回测 Pass1 函数**：抽出 `scope_and_score_targets`（回测逐调仓日
   scope+打分的同一函数）供两路共用——零漂移因此 by construction（同面板行、同候选集、
   同 z-score、同撮合代码）。
3. **周频/月频的实盘因果守卫**：回放/回测口径为"周期内最后一个交易日"；实盘当天无法
   预知未来，故周信号仅在周五已到（today ≥ 周五）、月信号仅在月末已过时触发。节假日
   缩短的周/月末实盘可能漏一期（回测按真实最后交易日）——已知且可解释的 drift 来源。
4. **零漂移测试口径**：liquidity_top_k=None 关闭股票池截断（全市场 >5000 只有数据，任何
   截断的边缘成员依赖各路径的成交额排名窗口）。生产默认 1000 保留——窗口边缘成员差
   是真实"数据差异"，正是 drift 页要度量的东西。比对截止于回测丢弃的最后一个调仓
   （reb_dates[:-1]）执行日前：模拟盘照常执行该期（实盘无法预知其为末期）。
5. **tick 执行模式**：每日 tick 挂 admin 长任务链路（_LONG_TASKS + /admin/paper/run，
   因信号段全市场面板构建 >30s）；手动补跑 /paper/accounts/{id}/tick 同步执行、
   限 ≤20 日/次（简化原">5 日走异步"设计，20 日实测 ~3 分钟内）。
6. **权重再平衡默认禁用**（rebalance_tol=None）：回测语义为"仅成员变动调仓"，启用
   再平衡会引入真实 drift（drift 页如实展示），默认关闭以保持零漂移基线。

## 五、风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 撮合层抽取改变回测行为 | 独立 PR + 既有回测测试全绿门槛；不一致即回滚抽取 |
| 每日数据晚到/不完备 | 完备性守卫拦信号不拦估值；23:00 重试 + 次日自然补上；告警可见 |
| 停机多日漏跑 | tick 支持区间补跑（订单状态机 + UK 幂等）；>5 日走异步长任务 |
| 除权事件误调持股数 | 因子跳变仅按 sa_adjust_factor 比值调整；缺因子告警+禁交易而非猜测 |
| 长期净值与真实分红口径差异 | qfq 口径=分红再投资近似，PRD 已声明；drift 对比不受影响（同口径） |
| drift 误解（把回测当真值） | drift 页明示"两侧都是模型，差异=假设距离"；因子健康度告警联动展示 |
| 回测引擎后续演进导致 drift 失义 | 回测 run params 存 config 指纹，drift 请求校验引擎口径一致性 |

## 六、M3 观察期运营项（非代码）

- 首账户上线后前两周每日检查 tick 日志与告警；四周后出《模拟盘首月运行纪要》
  （净值、drift 分解、告警清单、与因子健康度巡检的交叉印证）；
- 观察期发现的撮合规则缺口（如连续跌停卖不出 >10 日的处置）回填本方案"实际偏差注记"，
  作为阶段四 T4.1 任务链状态化的输入。
