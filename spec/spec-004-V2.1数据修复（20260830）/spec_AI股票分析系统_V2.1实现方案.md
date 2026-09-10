# AI 股票分析系统 V2.1 实现方案（Implementation Plan）

> **配套需求**：[spec-需求AI股票分析系统需求文档_PRD_V2.1.md](./spec-需求AI股票分析系统需求文档_PRD_V2.1.md)
> **版本**：V2.1 · 2026-08-30
> **范围**：覆盖 PRD V2.1 全部 8 条需求（BP-V2.1-001~008）
> **前置**：V2 已上线（12 条需求），本方案在既有代码上增量扩展，**不引入任何新中间件**
> （Redis/Celery/ClickHouse 按 ROADMAP 阶段四结论为触发式，长任务用进程内异步，见 §3.4）。

---

## 一、目标与架构（V2.1 增量）

**目标**：修复 step1 报告确认的四类数据问题——复权断裂、幸存者偏差、ST/涨跌停未过滤、
行业粒度不足；把一次性修复脚本产品化为平台能力（admin 任务 + 每日质量巡检）。

**架构沿用**：FastAPI 模块化单体（api 薄路由 → services 业务 → data 采集）、
React SPA、MySQL `stock_analysis` 同库、APScheduler 调度、cachetools 进程内缓存。
**新增组件：零。**

**本方案的三条主线**（与 PRD §2.3 对应）：

1. **存储根治**：新建 `sa_kline_daily`（不复权）+ `sa_adjust_factor`（因子表），全市场重灌；
   读取层 `market_service.get_kline` 按 `adjust` 参数折算，配置开关 `kline_source` 控制
   读旧表（legacy）还是新表（v2），灰度校验后切换，可随时回滚；
2. **样本治理**：`sa_stock_lifecycle`（含退市股）+ `sa_daily_trade_status`（ST/停牌/一字板）
   + `sa_industry_map`，IC / 回测 / 打分接口增加 `pool / exclude_st / exclude_suspended / only_tradable` 参数；
3. **质量运营**：`check_adjustment.py` 等脚本逻辑产品化为 `quality_service` + 每日 08:00 巡检落库
   `sa_data_quality_check`，admin 页可视。

---

## 二、文件结构（V2.1 新增/变更）

```
backend/
├── alembic/versions/
│   └── <rev>_v2_1_data_repair.py         # ★新增迁移：8 张新表 + sa_admin_task_log ALTER
├── app/
│   ├── models/
│   │   ├── kline.py                      # ★新增 SaKlineDaily / SaAdjustFactor /
│   │   │                                #        SaStockLifecycle / SaDailyTradeStatus /
│   │   │                                #        SaKlineSyncState
│   │   ├── quality.py                    # ★新增 SaDataQualityRule / SaDataQualityCheck
│   │   ├── market_data.py                # ★变更：SaAdminTaskLog 加 progress/result_json 字段
│   │   └── __init__.py                   # ★变更：导出新模型（避免重蹈 SaHistorySyncState 未注册）
│   ├── data/
│   │   ├── akshare_client.py             # ★变更：新增 fetch_daily_quotes_raw / fetch_daily_quotes_hfq
│   │   ├── sync_kline.py                 # ★新增：raw+因子 upsert（新表写入路径）
│   │   ├── kline_rebuild.py              # ★新增：全市场重灌（状态表+tick，仿 history_backfill）
│   │   ├── sync_delist.py                # ★新增：退市名单 → sa_stock_lifecycle
│   │   ├── sync_trade_status.py          # ★新增：交易状态回填/增量
│   │   ├── sync_industry_map.py          # ★新增：东财板块成分 → sa_industry_map
│   │   ├── repair_daily.py               # ★新增：冻结/错位检测与修复（产品化 repair_tencent_only）
│   │   └── validators.py                 # ★变更：raw 行校验规则
│   ├── services/
│   │   ├── market_service.py             # ★变更：get_kline 加 adjust 参数 + _apply_adjust
│   │   │                                #        + kline_source 开关分流
│   │   ├── universe_service.py           # ★新增：get_pool_asof（PIT 股票池）
│   │   ├── quality_service.py            # ★新增：巡检执行 + 规则管理 + 查询
│   │   ├── neutralize.py                 # ★新增：行业/市值中性化工具（横截面回归残差）
│   │   ├── admin_service.py              # ★变更：run_task 异步化 + 进度回写 + 新任务注册
│   │   ├── factor_service.py             # ★变更：IC/打分加样本过滤参数、池参数
│   │   └── backtest_service.py           # ★变更：回测加样本过滤 + 撮合消费 tradable
│   ├── api/
│   │   ├── admin.py                      # ★变更：run 异步返回 run_id、runs/{id} 状态、
│   │   │                                #        failures 下载、quality 三接口
│   │   ├── factor.py / backtest.py       # ★变更：过滤参数透传
│   ├── core/
│   │   └── config.py                     # ★变更：kline_source / quality_check_enabled 等开关
│   └── scheduler.py                      # ★变更：注册巡检/状态/退市/行业/重灌 tick 任务
├── scripts/
│   ├── check_adjustment.py               # 退役 → 逻辑并入 quality_service（保留只读对照）
│   └── repair_tencent_only.py            # 退役 → 逻辑并入 repair_daily
└── tests/                                # ★新增对应测试（见 §六各任务）

frontend/src/
├── pages/Admin.tsx                       # ★变更：任务进度列 + 修复任务表单 + 「数据质量」页签
├── components/QualityPanel.tsx           # ★新增：红绿灯 + 30 日趋势 + 异常明细
├── api/admin.ts                          # ★变更：runs 状态轮询 + quality 接口
└── pages/Backtest.tsx / Factor*.tsx      # ★变更：「样本过滤」折叠区（三复选 + PIT 池）
```

**职责划分原则（沿用）**：`api/` 薄路由 → `services/` 业务编排 → `data/` 采集与校验；
`models/`（ORM）与 `schemas/`（Pydantic）分离。

---

## 三、关键技术决策

### 3.1 复权模型：raw 存储 + 三层因子维护（BP-001 核心）

**存储**：`sa_kline_daily` 只存**不复权** OHLCV（腾讯源 `day` 数据 / 东财 `adjust=""`），
`pct_change` 直接取源端值（它本身就是真实复权后涨跌幅，是整套体系的锚）。

**因子表 `sa_adjust_factor` 的三层维护**：

| 层 | 时机 | 方法 | 精度 |
|---|---|---|---|
| 初始化锚定 | 重灌某股全历史时 | 额外抓一次**后复权**序列（东财 `adjust="hfq"`），`factor_t = hfq_close_t / raw_close_t` 逐日落库 | 精确（两个序列同源同时点） |
| 增量维护 | 每日 17:30 增量 | `factor_t = factor_{t-1} × (1+pct_t/100) ÷ (raw_t/raw_{t-1})`；无除权日该比值≈1，因子不变 | 有舍入噪声，仅过渡 |
| 事件重锚定 | 检测到除权事件 | `|(1+pct_t/100) − raw_ret_t| > 0.005` 判定除权 → 重抓该股 hfq 全历史，重算整条因子链 | 恢复精确 |

**折算公式**（读取层）：`hfq = raw × factor_t`；`qfq = raw × factor_t / factor_latest`。
qfq 与 hfq 的日收益完全相等（相邻两日因子比相同），**研究侧从 qfq 切 hfq 无行为回归**；
qfq 仅用于 K 线展示（视觉上贴近当前价）。volume / amount 不做复权调整（标准做法，
量类因子口径不受影响）。指数代码在因子表无行，factor 恒为 1（benchmark 路径零改动）。

**防复发**：这就是 PRD §2.3.3 的"除权事件防线"——事件检测挂在每日增量之后，
触发即把该股丢进 `kline_rebuild` 重灌队列（复用状态表断点机制）。

### 3.2 `get_kline` 折算插入点与缓存安全（BP-001 的读取层）

现状：`market_service.get_kline`（`app/services/market_service.py:199`）是全部下游
（4 个 factor 模块、factor_service 预取、backtest_service、api/stock、ai/tools、stock_agent）
的唯一咽喉；daily 分支走 `_get_kline_cached`（Session 级缓存存**原始 ORM 行**），
w/m 分支绕开缓存另查。**改动只在这一个函数**：

```python
def get_kline(db, code, start=None, end=None, period="d", adjust="qfq"):
    # kline_source(settings) == "v2" 时查 SaKlineDaily，否则查 DailyPrice（现状）
    rows = _load_rows(db, code, start, end, period)      # 缓存与切片逻辑不变，存 raw
    return _apply_adjust(db, code, rows, adjust, end)     # ★切片之后、返回之前施加
```

- **折算必须在 `_slice_rows` 之后、返回之前**施加，缓存里始终存 raw——否则同 Session 内
  不同 adjust 请求互相污染（这是本次调研确认的关键实现约束）；
- w/m 分支在构建 DataFrame 前对行同样过一遍 `_apply_adjust`；
- **无未来函数**：`_apply_adjust` 的 `factor_latest` 取"截至 end 日"的最新因子
  （end=None 时取全序列最新），因子计算用 `end=trade_date` 截断的场景天然安全；
- 旧表（legacy）路径 `_apply_adjust` 直通返回（无因子表），保证切换前零变化。

### 3.3 写入与切换策略（灰度 → 切换 → 回滚）

```text
阶段①双写      17:30 daily_k_sync：旧路径写 daily_prices（不动）＋新路径写 sa_kline_daily/sa_adjust_factor
              （每股多一次 raw 抓取，沿用 _throttle 步调；23:00 自愈同理双跑）
阶段②重灌      kline_rebuild tick（IntervalTrigger，仿 history_backfill：断点状态表+静默窗+max_instances=1）
              全市场 5,152 只灌 raw+因子，2,400 只污染股优先
阶段③校验      quality_service 产出「新旧表一致性报告」：抽样 500 只比对
              v2 折算 qfq vs legacy close（逐日偏差）、close2close vs pct_change 偏离>0.5% 格数
阶段④切换      settings.kline_source: "legacy" → "v2"（.env 改一行，重启生效）
阶段⑤收尾      稳定运行 ≥2 周后旧路径停写；daily_prices 保留只读一个版本后退役
```

**回滚**：`kline_source` 改回 `"legacy"` 即可，旧表数据在阶段⑤之前从未被破坏。
切换点用配置而非代码分支删除，保证随时可退。

### 3.4 长任务异步化（PRD §2.4 的最小实现，三层复用）

| 层 | 适用 | 实现 |
|---|---|---|
| A. 即发即忘 | 秒级~分钟级任务（修复、巡检触发） | `api/admin.py` run 端点改用 FastAPI `BackgroundTasks` 调 `admin_service.run_task`，立即返回 log_id；`run_task` 本就先写 `status="running"` 日志行（`admin_service.py:174-229`），前端用现有 logs 接口 + 新增 `runs/{run_id}` 轮询 |
| B. 断点续跑 | 小时级~天级任务（全市场重灌、生命周期初始化） | 完全复用 `history_backfill` 已验证范式：状态表 + `IntervalTrigger` tick + `max_instances=1` + 静默窗（`history_backfill.py:44-82` 的常量体系照搬） |
| C. 300s 内任务 | 既有 15 个任务 | 不动，`_run_with_deadline`（`admin_service.py:151`）照旧 |

`sa_admin_task_log` ALTER 加 `progress_done / progress_total / result_json` 三列；
B 层任务的批次循环按批回写进度，A 层任务结束回写结果摘要。
**不引入 Celery**；此机制即 ROADMAP T4.1 要求的"最小 job 记录"，阶段四在其上收口。

### 3.5 交易状态推导口径（BP-005）

| 标注 | 判定 | 数据来源 |
|---|---|---|
| 一字板 | `open==high==low==close` 且触及当日涨跌停价（按前收 × 阈值，四舍五入到分） | `sa_kline_daily` 纯计算，**可回填全历史** |
| 涨跌停 | `pct_change` 达阈值（主板 10% / 创业科创 20% / 北交 30% / ST 减半） | 同上（代码前缀 300/688/8xx/4xx + 名称 ST） |
| 停牌 | 当日 `stock_pool` 快照在池但无行情行 | 快照表 join，平台运行期可回溯 |
| ST | 历史时点名称含 "ST"（`stock_pool` 历史快照）+ 当前 ST 名单回溯 | **覆盖率受限**：快照仅 2026-07 起存在，更早年份标注 NULL，过滤接口显式返回覆盖率 |

`buy_tradable = ¬(停牌 ∨ 一字涨停)`；`sell_tradable = ¬(停牌 ∨ 一字跌停)`。
回填用 pandas 向量化（一次拉全市场 OHLC 分组计算），增量挂在 17:30 同步之后每日跑。

### 3.6 行业映射（BP-006）

- **主源东财**：`ak.stock_board_industry_cons_em(symbol=板块名)` 逐板块抓成分，
  ~86 个行业板块 × 1 次请求（`fetch_sector_list("industry")` 已有板块清单，
  `akshare_client.py:1009`，本任务只补 cons 抓取），`industry_code` 用东财板块 code（BKxxxx，稳定）；
- 申万（`sw_index_*` 系列）可用性待验证，作为可选增强任务（D5b），失败不阻塞；
- 读取统一：`universe_service.get_industry(db, codes, source="em")`；`stock_pool.industry`
  与 `sa_stock_industry` 停止新写入，查询侧逐步切到新表（保留一个版本的兼容读取）；
- **市值序列不建表**：`get_circ_mv_series(db, codes, dates)` 按需计算
  `circ_mv = amount ÷ (turnover/100)`，amount 缺失段用 `raw_close × 当期股本`（财务表）
  近似并打标——避免再引一张亿级行表，BP-008 回补后精确段自然扩大。

### 3.7 质量巡检（BP-007）

`check_adjustment.py` 的全表扫描（85 行脚本一次拉全库进 pandas）**不可直接复用**——
产品化改为**增量扫描**：只查"昨日新增行 + 昨日结算行"的窗口，全市场单日 ~5,000 行，秒级。
检查项与阈值存 `sa_data_quality_rule`（可配置），结果写 `sa_data_quality_check`。
周期性全量校验（如每月一次复权全表比对）作为一个特殊 check_type 走 B 层长任务。

---

## 四、数据库设计（V2.1 新建 `sa_` 表 DDL）

> 8 张新表 + 1 处 ALTER，一个 Alembic 迁移承接（§六 Task A1）。
> 手写迁移（沿用 `f2b7d9a4c6e8` 风格），不动 `daily_prices` / `stock_pool`。

### 4.1 `sa_kline_daily`（不复权日K，BP-001/002/008）

```sql
CREATE TABLE `sa_kline_daily` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `trade_date` DATE NOT NULL,
  `open` DECIMAL(10,2) DEFAULT NULL,
  `close` DECIMAL(10,2) DEFAULT NULL,
  `high` DECIMAL(10,2) DEFAULT NULL,
  `low` DECIMAL(10,2) DEFAULT NULL,
  `volume` BIGINT DEFAULT NULL COMMENT '手',
  `amount` DECIMAL(18,2) DEFAULT NULL COMMENT '元',
  `pct_change` DECIMAL(8,4) DEFAULT NULL COMMENT '源端真实复权涨跌幅(%)，复权锚',
  `turnover` DECIMAL(8,4) DEFAULT NULL COMMENT '换手率(%)',
  `source` VARCHAR(10) DEFAULT NULL COMMENT 'tencent/em',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_date` (`stock_code`,`trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='不复权日K（V2.1 复权体系目标表）';
```

### 4.2 `sa_adjust_factor`（复权因子，BP-001）

```sql
CREATE TABLE `sa_adjust_factor` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `trade_date` DATE NOT NULL,
  `adj_factor` DECIMAL(20,8) NOT NULL COMMENT 'hfq=raw×factor；除权日跳变',
  `anchored` TINYINT NOT NULL DEFAULT 1 COMMENT '1=hfq锚定精确 0=pct_change增量推导',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_date` (`stock_code`,`trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='按日累计复权因子';
```

### 4.3 `sa_stock_lifecycle`（含退市股，BP-004）

```sql
CREATE TABLE `sa_stock_lifecycle` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `stock_name` VARCHAR(50) DEFAULT NULL,
  `exchange` VARCHAR(10) DEFAULT NULL,
  `list_date` DATE DEFAULT NULL,
  `delist_date` DATE DEFAULT NULL COMMENT 'NULL=在市',
  `list_status` VARCHAR(10) NOT NULL DEFAULT 'L' COMMENT 'L上市 D退市 P暂停',
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code` (`stock_code`),
  KEY `idx_delist` (`delist_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票生命周期（PIT宇宙依据）';
```

### 4.4 `sa_daily_trade_status`（交易状态，BP-005）

```sql
CREATE TABLE `sa_daily_trade_status` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `trade_date` DATE NOT NULL,
  `is_st` TINYINT DEFAULT NULL COMMENT 'NULL=数据源无法回溯',
  `is_suspended` TINYINT NOT NULL DEFAULT 0,
  `limit_status` VARCHAR(16) NOT NULL DEFAULT 'none' COMMENT 'none/limit_up/limit_down/limit_up_one_word/limit_down_one_word',
  `buy_tradable` TINYINT NOT NULL DEFAULT 1,
  `sell_tradable` TINYINT NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_date` (`stock_code`,`trade_date`),
  KEY `idx_date_status` (`trade_date`,`limit_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日频交易状态（ST/停牌/涨跌停/可成交）';
```

### 4.5 `sa_industry_map`（行业映射，BP-006）

```sql
CREATE TABLE `sa_industry_map` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `industry_code` VARCHAR(20) NOT NULL COMMENT '东财BKxxxx / 申万行业码（稳定编码）',
  `industry_name` VARCHAR(50) NOT NULL,
  `industry_level` VARCHAR(10) NOT NULL DEFAULT 'em' COMMENT 'em=东财行业 sw_l1/sw_l2=申万',
  `effective_date` DATE NOT NULL,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_level_date` (`stock_code`,`industry_level`,`effective_date`),
  KEY `idx_level_code` (`industry_level`,`industry_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个股行业映射（多源多级）';
```

### 4.6 `sa_data_quality_rule` / `sa_data_quality_check`（BP-007）

```sql
CREATE TABLE `sa_data_quality_rule` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `check_name` VARCHAR(50) NOT NULL COMMENT 'adjustment_break/frozen/row_baseline/... ',
  `metric_name` VARCHAR(50) NOT NULL,
  `warn_threshold` DECIMAL(18,4) DEFAULT NULL,
  `fail_threshold` DECIMAL(18,4) NOT NULL,
  `enabled` TINYINT NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_check_metric` (`check_name`,`metric_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质量巡检规则（阈值可配置）';

CREATE TABLE `sa_data_quality_check` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `check_date` DATE NOT NULL,
  `check_name` VARCHAR(50) NOT NULL,
  `metric_name` VARCHAR(50) NOT NULL,
  `metric_value` DECIMAL(18,4) DEFAULT NULL,
  `status` VARCHAR(10) NOT NULL COMMENT 'pass/warn/fail',
  `detail` JSON DEFAULT NULL COMMENT '异常明细（股票级，上限条数内联）',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_date_check_metric` (`check_date`,`check_name`,`metric_name`),
  KEY `idx_date` (`check_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每日质量巡检结果';
```

### 4.7 `sa_kline_sync_state`（重灌断点状态，仿 `sa_history_sync_state`）

```sql
CREATE TABLE `sa_kline_sync_state` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `target_start` DATE NOT NULL,
  `earliest_bar` DATE DEFAULT NULL,
  `status` VARCHAR(10) NOT NULL DEFAULT 'pending' COMMENT 'pending/done/failed',
  `attempts` SMALLINT NOT NULL DEFAULT 0,
  `priority` TINYINT NOT NULL DEFAULT 1 COMMENT '0=污染股优先队列',
  `last_error` TEXT DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_kline_code` (`stock_code`),
  KEY `idx_kline_status` (`status`,`attempts`,`priority`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='sa_kline_daily 全量重灌断点状态';
```

### 4.8 `sa_admin_task_log` 变更（长任务进度）

```sql
ALTER TABLE `sa_admin_task_log`
  ADD COLUMN `progress_done` INT DEFAULT NULL AFTER `rows_affected`,
  ADD COLUMN `progress_total` INT DEFAULT NULL AFTER `progress_done`,
  ADD COLUMN `result_json` TEXT DEFAULT NULL AFTER `progress_total`;
```

---

## 五、API 设计（V2.1 增量）

> 沿用规范：`/api/v1` 前缀、`{code:0,msg,data}` 响应、JWT；admin 接口需 admin 角色。

### 5.1 admin 任务（PRD §6.2）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/admin/tasks/{name}/run` | 沿用；长任务（`kline_rebuild`、`daily_k_repair`、`quality_full_check`、`amount_backfill`）走异步：立即返回 `{run_id}`；短任务行为不变 |
| GET | `/admin/tasks/runs/{run_id}` | ★ 新增：status + progress_done/total + result_json |
| GET | `/admin/tasks/runs/{run_id}/failures` | ★ 新增：失败清单 CSV（读 result_json / 状态表） |
| GET | `/admin/tasks/{name}/logs` | 沿用 |

新注册任务名：`kline_rebuild_batch`（手动推一批）、`kline_rebuild_reset`、`daily_k_repair`（body: `{kind: frozen|misaligned|codes, codes?}`）、`quality_check`、`quality_full_check`、`delist_sync`、`trade_status_backfill`、`industry_map_sync`、`amount_backfill`。

### 5.2 数据质量（PRD §6.1）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/quality/daily?date=` | 当日各检查项（状态/指标值/阈值） |
| GET | `/admin/quality/trend?days=30` | 各检查项时序 |
| GET | `/admin/quality/detail?date=&check=&status=&page=` | 异常明细（股票级） |
| POST | `/admin/quality/check/run` | 手动触发巡检（A 层异步） |

### 5.3 研究接口样本治理参数（PRD §6.3）

| 接口 | 新增参数（全部有默认值，缺省=现状行为） |
|---|---|
| `POST /factor/score` | `pool: current\|pit`、`exclude_st`、`exclude_suspended`、`only_tradable` |
| `GET /factor/ic` | 同上 |
| `POST /backtest`（单策略 / 组合） | 同上四参数；撮合层消费 tradable（信号日 buy_tradable=0 不开仓，sell_tradable=0 顺延卖出） |

---

## 六、实现任务分解（Task Breakdown）

> TDD：先写失败测试 → 实现 → 通过 → 提交。测试隔离方式沿用现状：
> patch `app.data.akshare_client` 层断网；DB fixture 连真实 `stock_analysis`
> （conftest 现状），**新表测试自建自清数据，不碰生产行**。

### 阶段 A：基础设施

#### Task A1：Alembic 迁移——8 张新表 + ALTER
**Files**: `models/kline.py`, `models/quality.py`, `models/market_data.py`(改), `models/__init__.py`(改), `alembic/versions/<rev>_v2_1_data_repair.py`
- [ ] Step 1: 按第四章 DDL 写 ORM（Mapped[] + UniqueConstraint 风格，参照 `SaStockIndustry`）。
- [ ] Step 2: `sa_admin_task_log` 加三列；`models/__init__.py` 导出全部新模型。
- [ ] Step 3: 手写迁移（照 `f2b7d9a4c6e8` 风格），`down_revision = "f2b7d9a4c6e8"`。
- [ ] Step 4: `alembic upgrade head` + downgrade/upgrade 一遍验证可回滚。
- [ ] Step 5: Commit `feat(db): V2.1 数据修复 8 张新表 + 任务日志进度字段`。

#### Task A2：配置开关与种子规则
**Files**: `core/config.py`(改), `services/quality_service.py`(骨架), 迁移 data seed
- [ ] Step 1: Settings 加 `kline_source: str = "legacy"`、`quality_check_enabled: bool = True`。
- [ ] Step 2: `sa_data_quality_rule` 种子 6 条（§3.7 检查项默认阈值）写入迁移。
- [ ] Step 3: Commit `feat(config): kline_source 开关 + 质量规则种子`。

---

### 阶段 B：复权体系与写入链路（BP-001/002 主体）

#### Task B1：数据源扩展——raw / hfq 抓取
**Files**: `data/akshare_client.py`(改), `tests/test_akshare_client.py`(改)
- [ ] Step 1: **先用真实数据源验证 schema**（腾讯 fqkline 不带 qfq 后缀返回 `day` 键即为 raw；
  hfq 参数与返回键 `hfqday` 需实测；东财 `adjust=""` / `"hfq"` 为准）。
- [ ] Step 2: `fetch_daily_quotes_raw(symbol, start, end)`：腾讯 raw 为主源（extended=True 带 amount/turnover）、
  东财 `adjust=""` 兜底，`@retry` 沿用。
- [ ] Step 3: `fetch_daily_quotes_hfq(symbol, start, end)`：东财 hfq（仅重灌/重锚定时用）。
- [ ] Step 4: 测试（mock + 一次真实冒烟）+ Commit `feat(data): raw/hfq 日K抓取`。

#### Task B2：新表写入路径 + 因子维护
**Files**: `data/sync_kline.py`, `tests/test_sync_kline.py`
- [ ] Step 1: `upsert_kline_rows(db, rows)`（仿 `upsert_daily_rows`，ON DUPLICATE KEY UPDATE，source 列）。
- [ ] Step 2: `init_adjust_factors(db, code)`：raw 与 hfq 逐日比对落因子（anchored=1）。
- [ ] Step 3: `maintain_factor_incremental(db, code, new_rows)`：pct_change 锚定推导（anchored=0）；
  返回是否检测到除权事件。
- [ ] Step 4: 测试：构造分红序列（raw 跳跌但 pct 平滑）验证因子跳变与事件检测。
- [ ] Step 5: Commit `feat(data): sa_kline_daily 写入与复权因子维护`。

#### Task B3：全市场重灌（tick 断点范式）
**Files**: `data/kline_rebuild.py`, `tests/test_kline_rebuild.py`
- [ ] Step 1: `ensure_state(db, priority_codes)`：播种全市场，污染清单 priority=0（依赖 B4 的检测或
  `check_adjustment.py` 现跑一次导出清单）。
- [ ] Step 2: `run_batch(db, batch_size)` + `tick()`（静默窗/熔断/max_instances 照抄 history_backfill 常量体系）。
- [ ] Step 3: 每批回写 `sa_admin_task_log` 进度（§3.4 B 层）。
- [ ] Step 4: 测试 + Commit `feat(data): kline 全量重灌（断点续跑）`。

#### Task B4：读取层折算（get_kline 改造）
**Files**: `services/market_service.py`(改), `tests/test_market_service.py`(改), `tests/test_kline_cache.py`(改)
- [ ] Step 1: 写测试 `test_apply_adjust`：qfq/hfq/raw 三口径、`end` 截断无未来函数、指数 factor=1。
- [ ] Step 2: 写测试 `test_kline_cache_stays_raw`：同 Session 先 qfq 后 raw，缓存不被污染。
- [ ] Step 3: 实现 `_apply_adjust` + `get_kline(..., adjust="qfq")` + `kline_source` 分流
  （w/m 分支同样过折算）。
- [ ] Step 4: 抽查既有测试全绿（默认参数零变化）。
- [ ] Step 5: Commit `feat(market): get_kline 复权折算 + v2 数据源开关`。

#### Task B5：增量链路双写 + 除权事件防线
**Files**: `scheduler.py`(改), `data/sync_kline.py`(改), `tests/test_scheduler_retry.py`(改)
- [ ] Step 1: `run_daily_sync` 在旧路径后追加新路径（raw 抓取 + upsert + 因子维护）。
- [ ] Step 2: 因子维护检出除权 → `sa_kline_sync_state` 该股重置为 pending（priority=0）。
- [ ] Step 3: `kline_rebuild_tick` 注册 IntervalTrigger（settings 可关）。
- [ ] Step 4: 测试 + Commit `feat(scheduler): 日K双写与除权事件重灌`。

---

### 阶段 C：存量修复（BP-002/003）

#### Task C1：脏数据检测产品化
**Files**: `data/repair_daily.py`, `tests/test_repair_daily.py`
- [ ] Step 1: 移植 `check_adjustment.py` / ic_survey 的冻结（≥5 日 close 与 pct 不动）与
  错位（段内价格水平偏离历史中枢 >5 倍）检测为纯函数，输入 DataFrame 输出清单。
- [ ] Step 2: 单测覆盖 001331（冻结）、600066（错位）两个真实形态的构造用例。
- [ ] Step 3: Commit `feat(data): 冻结/错位检测`。

#### Task C2：修复任务产品化
**Files**: `data/repair_daily.py`(续), `services/admin_service.py`(改), `api/admin.py`(改)
- [ ] Step 1: `repair(db, kind|codes)`：腾讯双 host 重抓指定窗口（移植 `repair_tencent_only.py`，
  30 连败熔断），写回走 `upsert_kline_rows`。
- [ ] Step 2: 注册 `daily_k_repair` 任务 + run 端点 A 层异步化（§3.4）+ `runs/{run_id}` 接口。
- [ ] Step 3: `sa_admin_task_log` 进度回写打通；前端 Admin 任务表加进度列与修复表单。
- [ ] Step 4: 测试 + Commit `feat(admin): 修复任务产品化与长任务进度`。

#### Task C3：重灌执行（Runbook，见 §七）
- [ ] Step 1: 启动重灌（污染 2,400 只 priority=0 先行），观察 2~3 个交易日节奏。
- [ ] Step 2: 出具新旧一致性报告（quality_service：抽样 500 只逐日比对 + 全市场断裂计数）。
- [ ] Step 3: 达到 BP-002 验收（偏离>0.5% 清零）后切 `kline_source="v2"`，观察 2 天。
- [ ] Step 4: Commit / 记录 `docs/reviews/v2_1_kline_cutover.md`（方案评审记录，BP-001 验收项）。

---

### 阶段 D：样本治理（BP-004/005/006）

#### Task D1：退市名单与生命周期表
**Files**: `data/sync_delist.py`, `tests/test_sync_delist.py`
- [ ] Step 1: **验证 akshare 1.18.80 退市接口 schema**（`stock_info_sh_delist` /
  `stock_info_sz_delist("终止上市公司")`，字段名以实测为准）。
- [ ] Step 2: `sync_lifecycle(db)`：在市股（stock_pool + list_date）∪ 退市股 → upsert，
  首次跑走 B 层长任务（全量 ~1.5 万行）。
- [ ] Step 3: 注册 `delist_sync`（每周六 09:00）。测试 + Commit `feat(data): 股票生命周期表`。

#### Task D2：PIT 取池服务
**Files**: `services/universe_service.py`, `tests/test_universe_service.py`
- [ ] Step 1: `get_pool_asof(db, date) -> list[str]`：`list_date<=date AND (delist_date IS NULL OR delist_date>date)`。
- [ ] Step 2: 验收用例：2022-06-30 的池含其后退市股；边界（当日上市/退市）单测。
- [ ] Step 3: Commit `feat(services): point-in-time 股票池`。

#### Task D3：IC / 回测接池与过滤参数
**Files**: `services/factor_service.py`(改), `services/backtest_service.py`(改), `api/factor.py`(改), `api/backtest.py`(改)
- [ ] Step 1: IC 与打分：`pool="pit"` 时逐调仓日 `get_pool_asof`；`exclude_st/exclude_suspended/only_tradable`
  join `sa_daily_trade_status` 过滤。
- [ ] Step 2: 回测：同参数；撮合层——信号日 `buy_tradable=0` 放弃该笔开仓，持仓日
  `sell_tradable=0` 顺延至下一可卖日。
- [ ] Step 3: 写测试：跌停日信号被过滤、停牌持仓顺延卖出。
- [ ] Step 4: 用 PIT 池重跑一次 IC 普查出对比报告（BP-004 验收）。
- [ ] Step 5: Commit `feat(research): 样本治理参数接入 IC/回测`。

#### Task D4：交易状态回填与增量
**Files**: `data/sync_trade_status.py`, `scheduler.py`(改), `tests/test_sync_trade_status.py`
- [ ] Step 1: 检测纯函数（§3.5 口径）+ 向量化回填（全历史，B 层长任务）。
- [ ] Step 2: 增量任务 `trade_status_sync` 挂 19:00（17:30 日K之后）；ST 名称口径与覆盖率统计。
- [ ] Step 3: 验收：抽 20 个人工核对日 ≥95%（BP-005 验收）。
- [ ] Step 4: Commit `feat(data): 交易状态标注`。

#### Task D5：行业映射（东财主源 + 申万可选）
**Files**: `data/sync_industry_map.py`, `services/universe_service.py`(改), `tests/test_sync_industry_map.py`
- [ ] Step 1: `fetch_industry_cons(board)`（`ak.stock_board_industry_cons_em`，_throttle 步调）
  → 全板块成分 upsert `sa_industry_map`（level='em'），每周日 09:00 增量。
- [ ] Step 2: `get_industry(db, codes)` 统一读取；`stock_list_service` 行业筛选项切新表。
- [ ] Step 3: （可选 D5b）申万接口验证可用则补 level='sw_l1/sw_l2'，不可用则记录结论跳过。
- [ ] Step 4: 验收：覆盖率 ≥95%、退市股有归属。Commit `feat(data): 行业映射升级`。

#### Task D6：市值序列与中性化工具
**Files**: `services/universe_service.py`(改), `services/neutralize.py`, `tests/test_neutralize.py`
- [ ] Step 1: `get_circ_mv_series`（amount/turnover 推导 + 股本近似段打标）。
- [ ] Step 2: `neutralize_cross_section(df, industry, mktcap)`：横截面回归取残差
  （statsmodels OLS 或 numpy lstsq，带 NaN 处理）；构造用例：注入已知行业/市值效应 → 残差消除。
- [ ] Step 3: Commit `feat(research): 市值序列与中性化工具`。

---

### 阶段 E：质量运营（BP-007，BP-008）

#### Task E1：质量巡检执行器
**Files**: `services/quality_service.py`(实现), `tests/test_quality_service.py`
- [ ] Step 1: 6 项检查实现（增量窗口扫描，§3.7）：adjustment_break / frozen / row_baseline /
  field_missing（amount、turnover）/ coverage（trade_status、industry、lifecycle）/ amplitude_anomaly。
- [ ] Step 2: 结果 upsert `sa_data_quality_check`（当日重跑覆盖）；规则表读阈值。
- [ ] Step 3: 测试：注入坏行（删一行 / 改 close / 冻结序列）→ 对应检查 fail 且明细到股票级。
- [ ] Step 4: Commit `feat(quality): 每日巡检执行器`。

#### Task E2：调度 + admin API + 前端
**Files**: `scheduler.py`(改), `api/admin.py`(改), `frontend: QualityPanel.tsx / Admin.tsx / api/admin.ts`
- [ ] Step 1: `quality_check` 每日 08:00（`_run_admin_task` 走日志链路；失败项 ERROR 日志）。
- [ ] Step 2: quality 四接口（§5.2）+ 前端页签：红绿灯卡片、30 日趋势、明细表、CSV 导出。
- [ ] Step 3: 连续 5 交易日观察（BP-007 验收）。Commit `feat(quality): 巡检调度与可视`。

#### Task E3：amount/turnover 回补（P2，可顺延）
**Files**: `data/repair_daily.py`(扩展), `services/admin_service.py`(改)
- [ ] Step 1: `amount_backfill` 任务：对新表中 amount IS NULL 的日期段腾讯 extended 重抓。
- [ ] Step 2: 回补后重跑 amt20 IC 对比（BP-008 验收）。Commit `feat(data): 量额字段回补`。

---

### 阶段 G：联调与验收

#### Task G1：端到端验收
- [ ] Step 1: PRD §1.3 四句声明逐条验证（断裂数、PIT 池、过滤开关、中性化用例）。
- [ ] Step 2: V2 回归：因子打分 / 回测 / K 线页 / AI 分析默认参数输出与迁移前一致（抽样比对）。
- [ ] Step 3: 性能：get_kline <500ms、带过滤回测增幅 <30%、巡检 <10min（PRD §4.3）。
- [ ] Step 4: Commit `test: V2.1 端到端验收`；更新 PRD/需求池条目状态。

---

## 七、切换与回滚 Runbook（BP-002 重灌执行）

```text
D0  准备    ① alembic upgrade（A1）；② mysqldump 备份 daily_prices 与 sa_admin_task_log
D1  启动    ③ 跑一次 check_adjustment.py 导出污染清单（2,175 只）→ ensure_state(priority=0)
            ④ 开 kline_rebuild_tick（settings 开关），batch=15/次、间隔 10min、静默窗 17:15-18:45
D1~D5 观察  ⑤ 每日看进度（runs/{run_id}）与质量巡检；预计 2,400 只优先队列 2~3 天完成，
            全市场滚动 ~1.5 周（qps 极低，保护数据源）
D5  校验    ⑥ quality_full_check：全市场 close2close vs pct_change 偏离>0.5% 计数 → 目标 0
            ⑦ 抽样 500 只 v2-qfq vs legacy-close 逐日比对 → 报告入 docs/reviews/
切换        ⑧ kline_source="v2"（.env）重启；观察 2 个交易日（巡检绿 + K 线页无异常工单）
回滚        任何异常：kline_source 改回 "legacy" 重启（分钟级）；旧表数据未动过
收尾        稳定 2 周后：daily_k_sync 停写旧路径；daily_prices 转只读，退役计划入 V2.2
```

---

## 八、依赖关系与里程碑

```text
A(表/配置) ──► B1(raw/hfq抓取) ──► B2(写入+因子) ──► B3(重灌tick) ──► C3(重灌执行+切换)
 │                                      └─► B4(读取折算) ──┐                       │
 │                D1(生命周期) ──► D2(PIT池) ──► D3(IC/回测接参) ◄──────────────────┘
 │                D4(交易状态) ──► D3
 │                D5(行业) ──► D6(市值+中性化)
 └─► C1(检测) ──► C2(修复产品化) ──► F: 并入 C2/E2（admin 前端）
E1(巡检) ──► E2(调度+可视)；E3 依赖 B3 完成
G(验收) 依赖上述全部
```

| 里程碑 | 时点 | 内容 | 对应 PRD 批次 |
|---|---|---|---|
| M1 | 第 1 周末 | A + B1~B4 + D1/D2 + C1：新表/折算/生命周期就绪（未切换线上读取） | 批次 1 |
| M2 | 第 2 周末 | B5 + C2/C3 + D3/D4：重灌完成、断裂清零、`kline_source=v2`、过滤开关生效 | 批次 2 |
| M3 | 第 3 周末 | D5/D6 + E1/E2 + G：行业/中性化、质量日报上线、PRD 验收清单全过（E3 视带宽） | 批次 3 |

---

## 九、Spec 覆盖矩阵（自检）

| PRD V2.1 需求 | 对应任务 |
|---|---|
| BP-V2.1-001 复权体系重构 | A1、B1、B2、B4、B5、C3（评审记录） |
| BP-V2.1-002 全市场历史重刷 | B3、C3（Runbook §七） |
| BP-V2.1-003 脏数据修复产品化 | C1、C2 |
| BP-V2.1-004 PIT 股票池 | D1、D2、D3 |
| BP-V2.1-005 交易状态标注 | D4、D3（过滤/撮合） |
| BP-V2.1-006 行业升级与中性化基础 | D5、D6 |
| BP-V2.1-007 数据质量日报 | A2、E1、E2 |
| BP-V2.1-008 amount/turnover 回补 | E3（P2，可顺延） |
| PRD §2.4 长任务异步化 | A1（ALTER）、C2、B3（进度回写） |
| PRD §4.3 性能不退化 | B4（折算向量化）、G1 |
| PRD §4.4 兼容性（默认行为零变化） | B4 默认 qfq、D3 参数默认值、G1 回归 |

覆盖完整，无遗漏。

---

## 十、风险与注意事项

| 风险 | 影响 | 缓解 |
|---|---|---|
| 腾讯 fqkline 的 raw/hfq 返回键与预期不符（`day`/`hfqday` 未经实测） | B1 阻塞 | B1 Step 1 强制先真实冒烟；东财 `adjust=""/"hfq"` 为兜底源（接口语义确定） |
| akshare 退市接口字段名与预期不符 | D1 阻塞 | D1 Step 1 先实测 schema；两所分别拉取 + 与交易所披露数量核对 |
| 增量推导因子（pct_change 锚定）舍入漂移 | 因子精度下降 | anchored 标记区分；事件重锚定恢复精确；巡检加"因子连续性"抽检 |
| Session 级 K 线缓存被折算污染 | 数据错误（隐蔽） | B4 专项测试 `test_kline_cache_stays_raw`；折算只施加于返回值 |
| 全市场重灌打爆数据源被 ban | 重灌中断数天 | batch=15 + 10min 间隔 + 静默窗 + 双源轮换 + 熔断（history_backfill 已验证参数） |
| 测试直连真实库误写生产表 | 数据污染 | 新测试只操作自建行并 teardown；重灌类测试全 mock 数据源 |
| ST 历史名称快照仅 2026-07 起存在 | 早期年份 ST 过滤覆盖率低 | 接口显式返回覆盖率；PRD 已声明该局限；后续可引第三方历史名单增强 |
| 申万接口不可用 | 行业粒度停留东财 | D5b 可选，不阻塞验收（东财 ~86 类已满足粒度要求） |

> 沿用 V1.5 实现方案的教训：**每个采集器实施的第一步都是用真实数据源验证一次接口 schema**，
> 不凭记忆写字段映射（B1、D1、D5 均已把验证列为 Step 1）。
