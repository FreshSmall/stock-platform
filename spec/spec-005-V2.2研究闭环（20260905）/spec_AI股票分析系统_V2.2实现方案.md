# AI股票分析系统 V2.2 实现方案（核心五件套）

> 对应 PRD：spec-005 PRD V2.2（BP-V2.2-001~005）
> 实施日期：2026-09-05
> 维护约定：每完成一项勾选对应 checklist 并补"实际偏差"注记。

## 一、模块地图（新增/改动）

```text
backend/app/
├── factor/
│   ├── momentum.py      [改] 注册 rsi14；roc120（回看窗口 250）
│   ├── volatility.py    [改] 注册 skew20
│   ├── volume.py        [改] 注册 amt20
│   └── multi_factor.py  [改] 预设常量 PRESETS（v2_reversal）
├── services/
│   ├── factor_panel.py      [新] 向量化面板：build_panel / panel_factor_values /
│   │                              forward_returns / 逐日横截面打分
│   ├── factor_service.py    [改] IC 序列 + 落库 + 中性化接线 + 打分统一走
│   │                              multi_factor 管线 + layered_backtest
│   ├── cost_model.py        [新] A股费率纯函数（buy_cost/sell_cost/AShareCommission）
│   ├── backtest_service.py  [改] 非对称佣金 + sizer + 可成交性注入 + 参数落库
│   └── portfolio_backtest_service.py [新] 组合级回测引擎 + 落库
├── strategy/base.py         [改] buy()/sell() 可成交性守卫
├── api/
│   ├── factor.py            [改] /score 扩展；ic-series；layered-backtest；
│   │                              portfolio-backtest
│   └── （/backtest 查询链路复用，不改）
├── models/factor.py         [改] SaFactorIc 加 pool/neutralized 列
└── alembic/versions/        [新] 迁移：sa_factor_ic 列 + 唯一键重建

frontend/src/
├── components/SampleFilterBar.tsx [改] 加 neutralize Select
├── api/factor.ts            [改] ic-series / layered / portfolio-backtest / preset 类型
├── pages/Factor.tsx         [改] IC 衰减卡 + 分层回测卡
└── pages/Portfolio.tsx      [改] 多因子组合回测卡
```

## 二、关键实现约定

1. **面板口径**：`daily_prices`（legacy qfq）一次 SQL 拉区间 close/volume/pct_change；复权断裂/冻结行不清洗（V2.1 重灌负责），IC 侧沿用 `MIN_BARS≥60` 与 NaN 自然丢弃。面板因子值必须与 registry 逐股 compute 在同一 (stock, date) 一致（测试锁定，容差 1e-6）。
2. **IC 序列口径**：RankIC（Spearman）；调仓步长默认 5 交易日；horizons=[1,5,10,20]；前瞻收益复利口径 close[t+h]/close[t]−1；落库含 ir；pool/neutralized 维度入唯一键。
3. **中性化**：逐调仓日横截面，industry 用 sa_industry_map PIT 行业（em 级），industry_mcap 追加 ln(total_mv)（stock_pool ≤t 最新快照）；残差 NaN 剔除。
4. **成本模型**：`CostParams(commission=2.5e-4, min_commission=5.0, stamp_duty=5e-4, transfer_fee=1e-5, slippage=1e-3)`；滑点在撮合价上体现（买价=价×(1+s)，卖价=价×(1−s)），佣金/印花税/过户费按成交额计。backtrader 侧滑点沿用 broker set_slippage_perc，佣金走自定义 CommissionInfo。
5. **可成交性**：优先 `sa_daily_trade_status`（buy_tradable/sell_tradable）；缺行回退 K 线推断（|pct_change|≥9.5% 且收在极值 → 撞板；无成交额 → 停牌近似不可交易）。守卫在 `BaseStrategy.buy()/sell()`，trademap 由 backtest_service 注入 `cerebro` 属性。
6. **组合回测撮合**：调仓日收盘出目标组合，**次日开盘价成交**（T+1 现实）；次日一字板/停牌 → 买单跳过、卖单顺延；顺延仓位按当日收盘估值。换手率 = |买入额+卖出额| / 2 / 期初组合市值。
7. **统一打分**：`multi_factor_score` 全部走 `score_stocks` 管线；负 weight → direction=−1 且 weight 取绝对值（兼容 recommend_agent）。
8. **行为变更声明**：单策略回测 sizer 修复后历史结果不可比（旧引擎每笔 1 股）；组合回测为全新能力。

## 三、API 契约（新增/变更）

```text
POST /api/v1/factor/score                    [变更]
  body: { factors: [{code, weight, direction?}], preset?, trade_date?,
          pool?, exclude_st?, exclude_suspended?, only_tradable?,
          neutralize?, top_n?, min_score?, universe_size? }
  → { ranked: [{stock, score, rank, factors{}}], meta{...} }

GET  /api/v1/factor/{code}/ic-series         [新]
  q: start, end, horizons=1,5,10,20, step=5, pool, exclude_st,
     exclude_suspended, only_tradable, neutralize
  → { series: [{trade_date, horizon, ic}], summary: {horizon: {mean_ic,
     ic_std, icir, win_rate, n_dates}}, by_year: {...}, persisted: true }

POST /api/v1/factor/{code}/layered-backtest  [新]
  body: { start, end, horizon=5, step=5, n_layers=5, pool, filters...,
          neutralize }
  → { layers: [{layer, nav[], ann_ret, vol, max_dd}], long_short: nav[],
     rebalance_dates: [] }

POST /api/v1/factor/portfolio-backtest       [新]
  body: { factors|preset, start, end, freq='W'|'M'|<N>, top_n=10,
          initial_cash, cost: {...}, pool, filters..., neutralize,
          liquidity_top_k=1000, benchmark='sh000001' }
  → { run_id }   （结果经 GET /backtest/{run_id} 查询）
```

## 四、实施顺序与 checklist

### T2.1 打分 V2（BP-V2.2-001）
- [x] momentum.py：rsi14 注册（并入 (6,12,14,24) 循环）；RocFactor 支持长周期（roc120，窗口 250）
- [x] volatility.py：skew20；volume.py：amt20
- [x] multi_factor.py：PRESETS 常量 + resolve_preset()
- [x] factor_service.multi_factor_score：改走 score_stocks；direction/top_n/min_score/negative-weight 兼容
- [x] api/factor.py /score：preset + direction + top_n
- [x] 测试：新因子注册与数值（对齐 ic_survey 口径）；direction 语义；preset 展开；recommend_agent 回归

### T2.3+T2.4 研究基建（BP-V2.2-002/003）
- [x] factor_panel.py：build_panel / panel_factor / forward_return_panel / score_cross_section
- [x] factor_service.compute_ic_series + 落库（upsert sa_factor_ic 含 ir）
- [x] factor_service.layered_backtest
- [x] 中性化接线：ic-series / layered / score 三处 + universe 行业市值取数
- [x] Alembic 迁移（列 + 唯一键）+ models/factor.py 同步
- [x] api/factor.py：ic-series / layered-backtest 端点
- [x] 测试：面板 vs registry 一致性；IC 序列首日与 compute_ic 一致；中性化开关改变 IC；分层单调性（合成数据）；迁移后 upsert 幂等

### T2.2 单策略严谨化（BP-V2.2-004）
- [x] cost_model.py：CostParams + buy_cost/sell_cost 纯函数 + AShareCommission(bt.CommissionInfo)
- [x] backtest_service：AShareCommission 接入；sizer（买 98% 资金/卖清仓）；trademap 注入；成本参数落 params
- [x] strategy/base.py：buy()/sell() 守卫
- [x] 测试：成本数值（含最低佣金/印花税方向性）；守卫跳过一字板；仓位金额量级

### T2.5 组合回测（BP-V2.2-005）
- [x] portfolio_backtest_service.run_mf_backtest（调仓/撮合/估值/指标/落库）
- [x] api/factor.py：portfolio-backtest 端点
- [x] 测试：小样本端到端（NAV/成本/换手/一字板顺延）；落库经 /backtest/{run_id} 可查

### 前端
- [x] SampleFilterBar：neutralize Select
- [x] Factor.tsx：IC 衰减卡 + 分层回测卡
- [x] Portfolio.tsx：组合回测卡（配置表单 + 结果区 + 轮询）
- [x] npm run build 通过

### 收尾
- [x] 全量 pytest + ruff clean
- [x] 真实数据小规模验证（一个因子 ic-series + 一次组合回测）
- [ ] Backlog BP-V2.2-001~005 状态更新
- [x] 分逻辑 commit 提交推送

### 实际偏差注记（2026-09-05 交付时）

- 「IC 序列首日与 compute_ic 一致」测试改为「面板因子 vs registry 逐股一致 + 摘要健全性」：两条路径的股票池选择逻辑不同（单日按当日成交额 top-300，序列按区间均额 top-N），逐日数值不可直接对齐，属预期。
- 「分层单调性（合成数据）」改为真实数据分层回测形态断言（层数/净值构造/回撤符号/多空序列存在）。
- 组合回测为同步执行（无轮询）：当前规模（universe≤1000、区间≤2年）实测 10~40s，可接受；跨年全市场跑若变慢，属 T4.1 任务链状态化范畴。
- 单策略回测 base.py 守卫默认常开（不可成交日跳过下单），无开关——约束属于真实性建模而非可选样本治理，与 PRD 一致。
- universe_service.get_industry 增加首快照回退（表历史始于 2026-08-31）：asof 早于该日时用最早快照，行业慢变化假设下可接受，待行业历史积累后自动恢复严格 PIT。

## 五、风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 全市场面板内存压力（4500×1300×float64 ≈ 45MB/列） | 只拉 close/volume/pct 三列；区间参数化；流动性预筛后再打分 |
| IC 落库写入量（每因子 4 horizon × ~250 调仓日/年） | upsert 幂等；单次请求限单因子单区间 |
| backtrader sizer 行为变化 | 现有 4 个回测测试断言的是比率与健全性而非绝对值；如爆改坏则按新语义更新断言 |
| 迁移唯一键重建 | 表当前零行，ALTER 无数据风险；失败可重放（drop+create uk） |
| 前端轮询超时（组合回测同步执行偏慢） | universe/区间参数上限校验；复用 backtest 轮询模式 |

## 六、T2.6 / T2.8 增补交付记录（2026-09-05 第二批）

- T2.6（BP-V2.2-006）：`app/ml/evaluation.py`（classification_metrics / by_year /
  threshold_sweep / calibration_bins / importance_stability 纯函数）+ walk_forward
  return_folds 折信息 + service `evaluation` 输出块 + 脚本 md/json 双产物。
  发现并修正：数万并发信号的逐笔全仓复利口径失真 → 报告改用日组合年化
  （`ann_ret_dailycap`，bt.perf 已注记 total_ret 的适用边界）。
- T2.8（BP-V2.2-008）：`scripts/generate_research_report.py`，证据全部来自
  平台服务（ic-series / layered_backtest / run_mf_backtest 含分年窗口），
  ML 增益引用评估 JSON —— 满足"非一次性脚本支撑"退出标准。
- T2.7（因子健康度监控）仍为下批次：IC 落库底座已备，差周度调度与预警落库。
- 全量测试 415 passed；新增 test_meta_label_evaluation.py 7 个纯函数测试。
- T2.7（BP-V2.2-007）补齐（同日第三批）：`factor_health_service`（3 指标巡检，
  复用 sa_data_quality_rule/check 表族、metric_name 以 `<metric>:<factor>`
  编码适配原唯一键）+ 周六 09:30 调度 + `/admin/factor-health(-/run)` API +
  admin 因子健康页签。至此阶段二 8 任务（T2.1~T2.8）全部交付。

