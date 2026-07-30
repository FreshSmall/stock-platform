# AI 股票分析系统 V1.5 实现方案（Implementation Plan）

> **配套需求**：[spec-需求AI股票分析系统需求文档_PRD_V1.5.md](./spec-需求AI股票分析系统需求文档_PRD_V1.5.md)
> **版本**：V1.5 · 2026-07-29
> **范围**：覆盖 PRD V1.5 全部 12 条需求（BP-V1.5-001~012）
> **前置**：V1（MVP）已交付，本方案在既有代码上增量扩展，**不重构 V1 架构**。

---

## 一、目标与架构（V1.5 增量）

**目标**：在 V1 模块化单体上横向扩展数据维度与展示能力，并新增 `admin` 后台模块。
不引入 Redis / Celery / ClickHouse / ES / 微服务（V2 范畴）。

**架构沿用 V1**：FastAPI 单体（data/market/ai/strategy/user 模块 + 新增 admin），
React SPA，共用 MySQL `stock_analysis` 库（既有表只读复用，产物表加 `sa_` 前缀），
cachetools 进程内缓存，APScheduler 调度。

**技术栈无变化**，仅确认：
- 指标计算继续用纯 pandas/numpy（V1 已放弃 TA-Lib，`calc_ema` 已存在可复用）；
- 采集器沿用 `akshare_client._throttle` + `tenacity @retry` 模式扩展；
- 前端沿用 antd 6 / echarts 6 / react-query 5。

---

## 二、文件结构（V1.5 新增/变更）

```
backend/
├── alembic/versions/
│   └── <rev>_v1_5_sa_tables.py          # ★新增迁移：8 张 sa_ 表 + sa_user.role 字段
├── app/
│   ├── models/
│   │   ├── sector.py                    # ★新增 sa_sector / sa_sector_stock / sa_sector_daily
│   │   ├── sentiment.py                 # ★新增 sa_market_sentiment
│   │   ├── market_data.py               # ★新增 sa_minute_price / sa_dragon_tiger /
│   │   │                                #        sa_north_flow / sa_money_flow_detail /
│   │   │                                #        sa_admin_task_log
│   │   └── stock.py                     # ★变更：新增 ChipDistribution 只读映射
│   ├── schemas/
│   │   ├── sector.py                    # ★新增
│   │   ├── sentiment.py                 # ★新增
│   │   ├── market_data.py               # ★新增（minute/dragon/north/money-flow）
│   │   ├── admin.py                     # ★新增
│   │   └── stock.py                     # ★变更：StockListItem / ChipDistribution
│   ├── api/
│   │   ├── stocks.py                    # ★新增 /stocks 股票列表
│   │   ├── sector.py                    # ★新增 /sector 板块
│   │   ├── dragon_tiger.py              # ★新增 /dragon-tiger
│   │   ├── admin.py                     # ★新增 /admin（需 admin 角色）
│   │   ├── stock.py                     # ★变更：indicators type 扩展 / kline period /
│   │   │                                #        chip-distribution / money-flow-detail
│   │   └── market.py                    # ★变更：sentiment / north-flow
│   ├── services/
│   │   ├── stock_list_service.py        # ★新增
│   │   ├── sector_service.py            # ★新增
│   │   ├── sentiment_service.py         # ★新增（涨停/炸板/连板 计算）
│   │   ├── chip_service.py              # ★新增（读 chip_distribution）
│   │   └── admin_service.py             # ★新增
│   ├── data/
│   │   ├── akshare_client.py            # ★变更：新增 fetch_minute/fetch_dragon_tiger/
│   │   │                                #        fetch_north_flow/fetch_money_flow_detail/
│   │   │                                #        fetch_sector
│   │   ├── sync_minute.py               # ★新增
│   │   ├── sync_dragon_tiger.py         # ★新增
│   │   ├── sync_north_flow.py           # ★新增
│   │   ├── sync_money_flow_detail.py    # ★新增
│   │   ├── sync_sector.py               # ★新增
│   │   ├── sync_industry.py             # ★新增（回填 stock_pool.industry）
│   │   └── validators.py                # ★变更：新增分钟K/资金校验规则
│   └── scheduler.py                     # ★变更：注册新采集任务
├── app/core/
│   ├── deps.py                          # ★变更：require_admin 依赖
│   └── ratelimit.py                     # 沿用（分钟K按需拉取限流）
└── tests/                               # ★新增对应测试（见 §六）

frontend/src/
├── pages/
│   ├── Stocks.tsx                       # ★新增 /stocks
│   ├── Sector.tsx                       # ★新增 /sector
│   ├── SectorDetail.tsx                 # ★新增 /sector/:code
│   ├── DragonTiger.tsx                  # ★新增 /dragon-tiger
│   ├── Admin.tsx                        # ★新增 /admin
│   ├── Market.tsx                       # ★变更：情绪卡片 + 北向卡片 + 龙虎榜入口
│   └── StockDetail.tsx                  # ★变更：周期切换/新指标/筹码峰/资金图/龙虎榜
├── components/
│   ├── SentimentCards.tsx               # ★新增
│   ├── NorthFlowCard.tsx                # ★新增
│   ├── ChipDistribution.tsx             # ★新增
│   ├── MoneyFlowChart.tsx               # ★新增
│   └── KLineChart.tsx                   # ★变更：周期 + EMA/BOLL/RSI
├── api/
│   ├── stocks.ts                        # ★新增
│   ├── sector.ts                        # ★新增
│   ├── dragonTiger.ts                   # ★新增
│   ├── admin.ts                         # ★新增
│   ├── market.ts                        # ★变更
│   └── stock.ts                         # ★变更
└── App.tsx                              # ★变更：注册新路由 + 导航项
```

**职责划分原则（沿用 V1）**：`api/` 薄路由 → `services/` 业务编排 → `data/` 采集、
`models/`（ORM）与 `schemas/`（Pydantic）分离。

---

## 三、关键技术决策

### 3.1 涨停/炸板/连板的计算口径（BP-V1.5-006/011 核心）

基于 `daily_prices` 当日数据，纯 SQL + Python 计算，写入 `sa_market_sentiment`：

- **涨停判定**：`pct_change >= 涨停阈值`。阈值按板块：主板 ±10%，创业板/科创板 ±20%，ST ±5%。
  （板块规则由代码前缀推断：300/688=20%，其余=10%；ST 由名称包含 "ST" 判断。）
- **炸板**：当日 `high` 触及涨停价（`high >= 前收 * (1+阈值)`）但 `close` 未封住（`close < 涨停价`）。
- **封板率** = 涨停家数 / (涨停家数 + 炸板家数)。
- **连板高度**：维护每只股票的连续涨停天数（涨停+1，否则归零），取当日全市场最大值。
  连板状态需按交易日滚动计算，建议在 `sa_market_sentiment` 旁维护一张
  `sa_limit_up_streak`（stock_code, trade_date, streak_days）辅助。
- **涨停梯队**：按 streak_days 分组统计家数（1 板 X 家 / 2 板 Y 家 …）。

> ⚠ 前一日收盘价取自 `daily_prices` 前一交易日；涨跌停价按四舍五入到分。
> 该计算是 V1.5 最复杂的业务逻辑，需重点测试（§六 阶段 C）。

### 3.2 分钟K采集策略（BP-V1.5-001）

- **盘后批量**：scheduler 在交易日盘后同步**热门股池**（成交额 Top 200，来自当日 `daily_prices`）
  的 5/15/30/60 分钟K，写入 `sa_minute_price`；
- **按需拉取**：个股详情页首次请求某周期分钟K且库里无近期数据时，触发
  `POST /stock/{code}/minute/fetch` 同步拉取（限流：同股同周期 10 分钟 1 次）；
- **数据源**：`ak.stock_zh_a_hist_min_em(symbol, period, adjust='qfq')`，
  列：时间/开盘/收盘/最高/最低/成交量/成交额/最新价。

### 3.3 周/月K聚合（BP-V1.5-009）

**不建新表**。在 `market_service.get_kline` 内，当 `period in (w, m)` 时从 `daily_prices`
拉日K后用 pandas resample 聚合：
- 周K：`resample('W-FRI')`，OHLC 取 周 open/最高 high/最低 low/末日 close，volume/amount 求和；
- 月K：`resample('M')` 同理。
- 聚合后指标随 K 线重算（周期切换时前端重新请求 indicators）。

### 3.4 筹码峰（BP-V1.5-007）

零采集。新增 `ChipDistribution` 只读映射现有 `chip_distribution` 表，
`chip_service.get_chip(code, date)` 返回 {价格分布, 获利盘比例, 平均成本, 90%集中度}。
> 实施前需 `DESC chip_distribution` 确认实际字段名（PRD V1.2 §2.2.1 记录有该表 39 万行，
> 但字段名未在现有 ORM 中映射，第一步先探查）。

### 3.5 缓存策略（沿用 cachetools）

| 数据 | TTL | 说明 |
|---|---|---|
| 板块排行 | 5 分钟 | 盘后数据不变 |
| 情绪指标 | 5 分钟 | 当日计算结果 |
| 筹码峰 | 10 分钟 | 低频变动 |
| 分钟K查询 | 5 分钟 | |
| 股票列表 | 1 分钟 | 分页结果短缓存，避免高频打库 |

### 3.6 admin 鉴权

`sa_user` 新增 `role` 字段（`user`/`admin`，默认 `user`）。
`core/deps.py` 新增 `require_admin` 依赖（在 `get_current_user` 基础上校验 role），
所有 `/admin/*` 路由挂该依赖。

---

## 四、数据库表设计（V1.5 新建 `sa_` 表 DDL）

> 既有表 `daily_prices` / `stock_pool` / `chip_distribution` / `minute_prices` 只读复用。
> `sa_user` 变更：加 `role` 列（ALTER）。

### 4.1 `sa_minute_price`（分钟K，BP-V1.5-001）

```sql
CREATE TABLE `sa_minute_price` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `trade_date` DATE NOT NULL COMMENT '所属交易日',
  `trade_time` DATETIME NOT NULL COMMENT '分钟时间戳',
  `period` TINYINT NOT NULL COMMENT '周期(分钟):1/5/15/30/60/120',
  `open` DECIMAL(10,2) DEFAULT NULL,
  `close` DECIMAL(10,2) DEFAULT NULL,
  `high` DECIMAL(10,2) DEFAULT NULL,
  `low` DECIMAL(10,2) DEFAULT NULL,
  `volume` BIGINT DEFAULT NULL,
  `amount` DECIMAL(18,2) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_period_time` (`stock_code`,`period`,`trade_time`),
  KEY `idx_date_period` (`trade_date`,`period`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分钟K行情';
```

### 4.2 `sa_dragon_tiger` / 席位明细（BP-V1.5-002）

```sql
CREATE TABLE `sa_dragon_tiger` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `trade_date` DATE NOT NULL,
  `stock_code` VARCHAR(10) NOT NULL,
  `stock_name` VARCHAR(50) DEFAULT NULL,
  `reason` VARCHAR(100) DEFAULT NULL COMMENT '上榜原因',
  `net_buy` DECIMAL(18,2) DEFAULT NULL COMMENT '净买入',
  `buy_amount` DECIMAL(18,2) DEFAULT NULL,
  `sell_amount` DECIMAL(18,2) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_date_code` (`trade_date`,`stock_code`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='龙虎榜个股';

CREATE TABLE `sa_dragon_tiger_seat` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `trade_date` DATE NOT NULL,
  `stock_code` VARCHAR(10) NOT NULL,
  `side` TINYINT NOT NULL COMMENT '1=买,2=卖',
  `rank` TINYINT NOT NULL COMMENT '席位序号1-5',
  `seat_name` VARCHAR(100) NOT NULL,
  `buy_amount` DECIMAL(18,2) DEFAULT NULL,
  `sell_amount` DECIMAL(18,2) DEFAULT NULL,
  `net_amount` DECIMAL(18,2) DEFAULT NULL,
  `is_institution` TINYINT DEFAULT 0 COMMENT '1=机构席位',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_date_code_side_rank` (`trade_date`,`stock_code`,`side`,`rank`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='龙虎榜席位明细';
```

### 4.3 `sa_north_flow`（北向资金，BP-V1.5-003）

```sql
CREATE TABLE `sa_north_flow` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `trade_date` DATE NOT NULL,
  `channel` VARCHAR(10) NOT NULL COMMENT 'sh=沪股通,sz=深股通',
  `net_buy` DECIMAL(18,2) DEFAULT NULL COMMENT '净买入额',
  `buy_amount` DECIMAL(18,2) DEFAULT NULL,
  `sell_amount` DECIMAL(18,2) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_date_channel` (`trade_date`,`channel`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='北向资金';
```

### 4.4 `sa_money_flow_detail`（四档资金，BP-V1.5-004）

```sql
CREATE TABLE `sa_money_flow_detail` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `trade_date` DATE NOT NULL,
  `super_net` DECIMAL(18,2) DEFAULT NULL COMMENT '超大单净流入',
  `big_net` DECIMAL(18,2) DEFAULT NULL COMMENT '大单净流入',
  `medium_net` DECIMAL(18,2) DEFAULT NULL COMMENT '中单净流入',
  `small_net` DECIMAL(18,2) DEFAULT NULL COMMENT '小单净流入',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_date` (`stock_code`,`trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分单资金明细';
```

### 4.5 板块三表（BP-V1.5-005）

```sql
CREATE TABLE `sa_sector` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `sector_code` VARCHAR(20) NOT NULL,
  `sector_name` VARCHAR(50) NOT NULL,
  `sector_type` VARCHAR(10) NOT NULL COMMENT 'industry/concept',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_type` (`sector_code`,`sector_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='板块定义';

CREATE TABLE `sa_sector_stock` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `sector_code` VARCHAR(20) NOT NULL,
  `sector_type` VARCHAR(10) NOT NULL,
  `stock_code` VARCHAR(10) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sector_stock` (`sector_code`,`sector_type`,`stock_code`),
  KEY `idx_stock` (`stock_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='板块成分股';

CREATE TABLE `sa_sector_daily` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `sector_code` VARCHAR(20) NOT NULL,
  `sector_type` VARCHAR(10) NOT NULL,
  `trade_date` DATE NOT NULL,
  `pct_change` DECIMAL(8,4) DEFAULT NULL,
  `amount` DECIMAL(18,2) DEFAULT NULL,
  `limit_up_count` INT DEFAULT NULL COMMENT '涨停家数',
  `main_net_inflow` DECIMAL(18,2) DEFAULT NULL,
  `leader_code` VARCHAR(10) DEFAULT NULL COMMENT '领涨股代码',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sector_date` (`sector_code`,`sector_type`,`trade_date`),
  KEY `idx_date_type` (`trade_date`,`sector_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='板块每日统计';
```

### 4.6 `sa_market_sentiment` + `sa_limit_up_streak`（BP-V1.5-006/011）

```sql
CREATE TABLE `sa_market_sentiment` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `trade_date` DATE NOT NULL,
  `limit_up_count` INT DEFAULT NULL COMMENT '涨停家数',
  `limit_down_count` INT DEFAULT NULL COMMENT '跌停家数',
  `failed_limit_count` INT DEFAULT NULL COMMENT '炸板家数',
  `seal_rate` DECIMAL(8,4) DEFAULT NULL COMMENT '封板率',
  `max_streak` INT DEFAULT NULL COMMENT '最高连板数',
  `up_count` INT DEFAULT NULL COMMENT '上涨家数',
  `down_count` INT DEFAULT NULL COMMENT '下跌家数',
  `streak_ladder` JSON DEFAULT NULL COMMENT '涨停梯队{1:X,2:Y,...}',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='市场情绪日统计';

CREATE TABLE `sa_limit_up_streak` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `trade_date` DATE NOT NULL,
  `streak_days` INT NOT NULL DEFAULT 0 COMMENT '连续涨停天数',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_date` (`stock_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='连板辅助表';
```

### 4.7 `sa_admin_task_log`（BP-V1.5-010）

```sql
CREATE TABLE `sa_admin_task_log` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `task_name` VARCHAR(50) NOT NULL,
  `started_at` DATETIME NOT NULL,
  `finished_at` DATETIME DEFAULT NULL,
  `status` VARCHAR(20) NOT NULL COMMENT 'running/success/failed',
  `rows_affected` INT DEFAULT NULL,
  `error` TEXT DEFAULT NULL,
  `triggered_by` VARCHAR(50) DEFAULT NULL COMMENT 'scheduler/manual:<user>',
  PRIMARY KEY (`id`),
  KEY `idx_task_started` (`task_name`,`started_at` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采集任务执行日志';
```

### 4.8 `sa_user` 变更（BP-V1.5-010）

```sql
ALTER TABLE `sa_user`
  ADD COLUMN `role` VARCHAR(10) NOT NULL DEFAULT 'user' COMMENT 'user/admin' AFTER `password_hash`,
  ADD COLUMN `status` TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用,0=禁用' AFTER `role`;
```

> 共新建 11 张表 + 1 处 ALTER，全部由一个 Alembic 迁移承接（§五 Task A1）。

---

## 五、API 设计（V1.5 增量，REST）

> 沿用 V1：前缀 `/api/v1`，响应 `{code:0,msg:"ok",data:{...}}`，分页用 `PageResult`。

### 5.1 股票列表 `/stocks`（BP-V1.5-012）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stocks?industry=&tag=&sort=&order=&page=&size=` | 分页列表（sort: pct_change/amount/total_mv/pe；tag: limit_up/limit_down/top_gainers）|

### 5.2 分钟K `/stock/{code}`（BP-V1.5-001）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stock/{code}/minute?period=5&start=&end=` | 分钟K（period:1/5/15/30/60/120）|
| POST | `/stock/{code}/minute/fetch?period=` | 按需拉取（限流：同股同周期 10 分钟 1 次）|

### 5.3 龙虎榜 `/dragon-tiger`（BP-V1.5-002）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/dragon-tiger?date=` | 当日龙虎榜列表 |
| GET | `/dragon-tiger/{code}` | 个股龙虎榜历史 |
| GET | `/dragon-tiger/{code}/{date}/seats` | 席位明细 |

### 5.4 北向资金 `/market`（BP-V1.5-003）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/market/north-flow?days=` | 北向资金当日 + 近 N 日 |

### 5.5 分单资金 `/stock/{code}`（BP-V1.5-004）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stock/{code}/money-flow-detail?days=` | 四档资金明细 |

### 5.6 板块 `/sector`（BP-V1.5-005）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sector?type=industry\|concept&sort=&limit=` | 板块排行 |
| GET | `/sector/{code}` | 板块详情 |
| GET | `/sector/{code}/stocks?page=&size=` | 成分股（分页）|

### 5.7 情绪指标 `/market`（BP-V1.5-006/011）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/market/sentiment` | 涨停/跌停/炸板/封板/连板/梯队 |

### 5.8 指标扩展 `/stock/{code}`（BP-V1.5-007）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stock/{code}/indicators?type=ema\|rsi\|boll` | type 枚举扩展（V1 ma/macd/kdj 基础上新增）|
| GET | `/stock/{code}/chip-distribution` | 筹码峰（读 chip_distribution）|

### 5.9 多周期 K 线 `/stock/{code}`（BP-V1.5-009）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stock/{code}/kline?period=d\|w\|m` | K 线增 period 参数 |

### 5.10 admin `/admin/*`（BP-V1.5-010，需 admin 角色）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/datasources` | 数据源列表与状态 |
| POST | `/admin/datasources/{name}/test` | 测试连通性 |
| GET | `/admin/tasks` | 采集任务列表 |
| POST | `/admin/tasks/{name}/run` | 手动触发任务 |
| GET | `/admin/tasks/{name}/logs` | 任务执行历史 |
| GET | `/admin/users` | 用户列表 |
| PATCH | `/admin/users/{id}` | 禁用/启用/改角色 |

---

## 六、实现任务分解（Task Breakdown）

> 采用 TDD：先写失败测试 → 实现 → 通过 → 提交。任务按依赖顺序编排。
> 每个任务产出可独立测试的改动。章节编号对应 PRD 需求。

### 阶段 A：基础设施与表结构

#### Task A1：Alembic 迁移——V1.5 新表 + sa_user 变更
**Files**: `models/sector.py`, `models/sentiment.py`, `models/market_data.py`, `alembic/versions/<rev>_v1_5_sa_tables.py`, `models/user.py`(改)
- [ ] Step 1: 按第四章 DDL 定义 11 张新表 ORM（`sa_minute_price`/`sa_dragon_tiger`/`sa_dragon_tiger_seat`/`sa_north_flow`/`sa_money_flow_detail`/`sa_sector`/`sa_sector_stock`/`sa_sector_daily`/`sa_market_sentiment`/`sa_limit_up_streak`/`sa_admin_task_log`）。
- [ ] Step 2: `sa_user` 加 `role`/`status` 字段。
- [ ] Step 3: `alembic revision --autogenerate -m "v1.5 sa tables"` 生成迁移。
- [ ] Step 4: `alembic upgrade head`，验证 `SHOW TABLES LIKE 'sa\_%'` 含 11 张新表。
- [ ] Step 5: Commit `feat(db): V1.5 新建 11 张 sa_ 表 + sa_user 角色字段`。

#### Task A2：admin 鉴权依赖 + ChipDistribution 映射
**Files**: `core/deps.py`(改), `models/stock.py`(改), `schemas/admin.py`
- [ ] Step 1: 先 `DESC chip_distribution` 探查字段，新增 `ChipDistribution` 只读映射。
- [ ] Step 2: `deps.py` 新增 `require_admin`（基于 `get_current_user` 校验 role）。
- [ ] Step 3: 测试 `test_require_admin`：非 admin 返回 403。
- [ ] Step 4: Commit `feat(core): admin 鉴权 + ChipDistribution 映射`。

---

### 阶段 B：数据采集（data 层）

> 所有采集器照搬 `akshare_client._throttle` + `@retry` 模式；mock akshare 写测试。

#### Task B1：分钟K采集（BP-V1.5-001）
**Files**: `data/akshare_client.py`(改), `data/sync_minute.py`, `tests/test_sync_minute.py`
- [ ] Step 1: `akshare_client.fetch_minute(symbol, period, ...)` 封装 `stock_zh_a_hist_min_em`。
- [ ] Step 2: `sync_minute.sync_one(db, code, period, date)` UPSERT 到 `sa_minute_price`。
- [ ] Step 3: `sync_minute.sync_hot_pool(db, top_n=200, periods=[5,15,30,60])` 批量。
- [ ] Step 4: 测试 mock akshare 验证 UPSERT 与字段映射。
- [ ] Step 5: Commit `feat(data): 分钟K采集`。

#### Task B2：龙虎榜采集（BP-V1.5-002）
**Files**: `akshare_client.py`(改), `data/sync_dragon_tiger.py`, `tests/test_sync_dragon_tiger.py`
- [ ] Step 1: `fetch_dragon_tiger(date)` + `fetch_dragon_tiger_seats(date, code)`。
- [ ] Step 2: `sync_dragon_tiger.sync_date(db, date)` 写个股 + 席位明细。
- [ ] Step 3: 测试验证席位 is_institution 标记。
- [ ] Step 4: Commit `feat(data): 龙虎榜采集`。

#### Task B3：北向资金采集（BP-V1.5-003）
**Files**: `akshare_client.py`(改), `data/sync_north_flow.py`, `tests/test_sync_north_flow.py`
- [ ] Step 1: `fetch_north_flow(date)` 沪深股通净买入。
- [ ] Step 2: `sync_north_flow.sync_date(db, date)`。
- [ ] Step 3: 测试 + Commit `feat(data): 北向资金采集`。

#### Task B4：分单资金采集（BP-V1.5-004）
**Files**: `akshare_client.py`(改), `data/sync_money_flow_detail.py`, `tests/test_sync_money_flow_detail.py`
- [ ] Step 1: `fetch_money_flow_detail(code)` 四档净流入。
- [ ] Step 2: `sync_money_flow_detail.sync_one(db, code, date)`。
- [ ] Step 3: 验证口径：super_net + big_net ≈ sa_money_flow.main_net_inflow。
- [ ] Step 4: Commit `feat(data): 分单资金采集`。

#### Task B5：板块采集（BP-V1.5-005）
**Files**: `akshare_client.py`(改), `data/sync_sector.py`, `tests/test_sync_sector.py`
- [ ] Step 1: `fetch_sector_list(type)` + `fetch_sector_stocks(code, type)` + `fetch_sector_daily(code, date)`。
- [ ] Step 2: `sync_sector` 全量同步板块定义/成分/日统计。
- [ ] Step 3: 测试 + Commit `feat(data): 板块数据采集`。

#### Task B6：行业字段回填（V1 遗留）
**Files**: `data/sync_industry.py`, `tests/test_sync_industry.py`
- [ ] Step 1: `fetch_industry(code)` 回填 `stock_pool.industry`。
- [ ] Step 2: 批量回填 NULL 行业（注意 stock_pool 只读约定——若不允许写，
  则建 `sa_stock_industry` 补充表替代回填，二选一，**实施前与团队确认**）。
- [ ] Step 3: Commit `feat(data): 行业字段补全`。

> ⚠ Task B6 注意：PRD V1.2 约定 `stock_pool` 只读。若不能写，方案改为新建
> `sa_stock_industry(stock_code, industry)` 补充表，查询时 LEFT JOIN。

---

### 阶段 C：情绪指标计算（最复杂，重点测试）

#### Task C1：涨停判定与连板计算（BP-V1.5-006/011）
**Files**: `services/sentiment_service.py`, `tests/test_sentiment_service.py`
- [ ] Step 1: 写测试 `test_limit_up_threshold`：主板 10%、创业/科创 20%、ST 5%。
- [ ] Step 2: 实现 `classify_limit(code, name, pct_change)` 返回涨/跌停/炸板/普通。
- [ ] Step 3: 写测试 `test_streak_rollover`：连板天数滚动（涨停+1/断板归零）。
- [ ] Step 4: 实现 `compute_streak(db, trade_date)` 写 `sa_limit_up_streak`。
- [ ] Step 5: 实现 `compute_sentiment(db, trade_date)` 聚合写 `sa_market_sentiment`
  （含 streak_ladder JSON 梯队）。
- [ ] Step 6: 覆盖炸板（high 触及但 close 未封）、封板率边界。
- [ ] Step 7: Commit `feat(market): 情绪指标与连板计算`。

---

### 阶段 D：行情/指标/资金 service

#### Task D1：技术指标扩展——RSI/BOLL（BP-V1.5-007）
**Files**: `services/indicator_service.py`(改), `tests/test_indicator_service.py`(改)
- [ ] Step 1: 写测试 `test_calc_rsi`（RSI6/12/24，验证超买超卖区间）。
- [ ] Step 2: 实现 `calc_rsi(closes, periods=[6,12,24])`。
- [ ] Step 3: 写测试 `test_calc_boll`（上/中/下轨）。
- [ ] Step 4: 实现 `calc_boll(closes, n=20, k=2)`。
- [ ] Step 5: EMA 直接复用已有 `calc_ema`（无需新增）。
- [ ] Step 6: `api/stock.py` 的 indicators `type` 枚举扩展为 `ma|ema|macd|kdj|rsi|boll`。
- [ ] Step 7: Commit `feat(market): RSI/BOLL 指标 + indicators 扩展`。

#### Task D2：多周期 K 线聚合（BP-V1.5-009）
**Files**: `services/market_service.py`(改), `tests/test_market_service.py`(改)
- [ ] Step 1: 写测试 `test_kline_weekly_aggregation`（周K OHLC/volume 正确）。
- [ ] Step 2: `get_kline` 加 `period` 参数，`w/m` 时 pandas resample 聚合。
- [ ] Step 3: `api/stock.py` kline 加 `period` query 参数。
- [ ] Step 4: Commit `feat(market): 周/月K聚合`。

#### Task D3：筹码峰 service（BP-V1.5-007）
**Files**: `services/chip_service.py`, `tests/test_chip_service.py`
- [ ] Step 1: 写测试 `test_chip_distribution`：返回获利盘比例/平均成本/集中度。
- [ ] Step 2: 实现 `get_chip(db, code, date)` 读 `chip_distribution` 聚合计算。
- [ ] Step 3: `api/stock.py` 加 `/stock/{code}/chip-distribution`。
- [ ] Step 4: Commit `feat(market): 筹码峰`。

#### Task D4：分单资金 / 北向资金 / 龙虎榜 service（BP-V1.5-002/003/004）
**Files**: `services/market_service.py`(改) 或新建 `services/market_data_service.py`
- [ ] Step 1: `get_money_flow_detail(db, code, days)`、`get_north_flow(db, days)`。
- [ ] Step 2: 龙虎榜 service：`list_dragon_tiger(date)`、`get_seats(code, date)`。
- [ ] Step 3: 对应 API 路由 + 缓存（5 分钟）。
- [ ] Step 4: Commit `feat(market): 资金/北向/龙虎榜查询`。

---

### 阶段 E：板块与股票列表

#### Task E1：板块 service + API（BP-V1.5-005）
**Files**: `services/sector_service.py`, `api/sector.py`, `tests/test_sector_service.py`
- [ ] Step 1: `list_sectors(type, sort, limit)`、`get_sector_detail(code)`、`list_sector_stocks(code, page)`。
- [ ] Step 2: 注册 `/sector/*` 路由 + 缓存。
- [ ] Step 3: 测试 + Commit `feat(sector): 板块中心接口`。

#### Task E2：股票列表 service + API（BP-V1.5-012）
**Files**: `services/stock_list_service.py`, `api/stocks.py`, `tests/test_stock_list_service.py`
- [ ] Step 1: 写测试 `test_list_stocks_pagination`：分页 + 行业筛选 + 排序。
- [ ] Step 2: 实现 `list_stocks(db, industry, tag, sort, order, page, size)` 返回 `PageResult`。
  - 确认 `stock_pool` 上 `(trade_date, industry)` 及排序字段索引；
  - 行业 NULL 归「未分类」或被「全部」覆盖；
  - tag（limit_up/limit_down/top_gainers）基于 pct_change 过滤。
- [ ] Step 3: 注册 `/stocks` 路由 + 1 分钟缓存。
- [ ] Step 4: 验证分页查询 < 800ms。
- [ ] Step 5: Commit `feat(market): 股票列表（分页筛选）`。

---

### 阶段 F：admin 后台

#### Task F1：admin service + API（BP-V1.5-010）
**Files**: `services/admin_service.py`, `api/admin.py`, `tests/test_admin_service.py`
- [ ] Step 1: 数据源状态：`list_datasources()`（检测 akshare/tushare 连通 + 限流配置）。
- [ ] Step 2: 任务管理：`list_tasks()`、`run_task(name, user)`（写入 `sa_admin_task_log`，触发对应 sync_*）、`task_logs(name)`。
- [ ] Step 3: 用户管理：`list_users()`、`update_user(id, role/status)`。
- [ ] Step 4: 注册 `/admin/*`，全部挂 `require_admin` 依赖。
- [ ] Step 5: 测试（含权限 403）+ Commit `feat(admin): 后台管理接口`。

---

### 阶段 G：调度器注册

#### Task G1：注册 V1.5 采集任务（BP-V1.5-001~006）
**Files**: `scheduler.py`(改)
- [ ] Step 1: 新增 jobs（均在交易日、Asia/Shanghai）：
  - 分钟K盘后同步（15:30 热门股池）
  - 龙虎榜 18:00、北向资金 16:00、分单资金 16:30、板块 17:00
  - 情绪指标计算 16:00（日K同步后）
- [ ] Step 2: 每个 job 执行前后写 `sa_admin_task_log`。
- [ ] Step 3: Commit `feat(scheduler): V1.5 采集任务注册`。

---

### 阶段 H：前端页面（对应 PRD §5.3）

#### Task H1：前端骨架扩展
**Files**: `App.tsx`(改), `components/Layout/AppLayout.tsx`(改), `api/*.ts`(新增)
- [ ] Step 1: 新增路由 `/stocks` `/sector` `/sector/:code` `/dragon-tiger` `/admin`。
- [ ] Step 2: 左侧导航加「股票」「板块」「管理」（管理仅 admin 可见，读 user.role）。
- [ ] Step 3: 新增 api 封装（stocks/sector/dragonTiger/admin + market/stock 扩展）。
- [ ] Step 4: Commit `feat(frontend): V1.5 路由与导航`。

#### Task H2：股票列表页 `/stocks`（BP-V1.5-012）
**Files**: `pages/Stocks.tsx`, `api/stocks.ts`
- [ ] Step 1: antd Table：分页 + 表头排序（pct_change/amount/total_mv/pe）。
- [ ] Step 2: 行业下拉筛选 + 快捷标签。
- [ ] Step 3: 行点击跳 `/stock/:code`；红涨绿跌；加载 Skeleton；空态。
- [ ] Step 4: Commit `feat(frontend): 股票列表页`。

#### Task H3：行情总览情绪面（BP-V1.5-006/011/003/002）
**Files**: `pages/Market.tsx`(改), `components/SentimentCards.tsx`, `components/NorthFlowCard.tsx`
- [ ] Step 1: SentimentCards：涨停/跌停/炸板/封板率/连板高度/梯队阶梯图。
- [ ] Step 2: NorthFlowCard：沪深股通净流入 + 迷你趋势。
- [ ] Step 3: 龙虎榜入口卡片。
- [ ] Step 4: 非交易日降级提示。
- [ ] Step 5: Commit `feat(frontend): 行情总览情绪面`。

#### Task H4：股票详情扩展（BP-V1.5-001/004/007/008/009/002）
**Files**: `pages/StockDetail.tsx`(改), `components/KLineChart.tsx`(改), `components/ChipDistribution.tsx`, `components/MoneyFlowChart.tsx`
- [ ] Step 1: KLineChart：周期切换（1/5/15/30/60 分/日/周/月）+ 指标 Tab 加 EMA/RSI/BOLL。
- [ ] Step 2: ChipDistribution：筹码峰横向柱状 + 获利盘/集中度。
- [ ] Step 3: MoneyFlowChart：四档资金堆叠柱状 + 主力折线。
- [ ] Step 4: 财务卡片「查看趋势」展开折线。
- [ ] Step 5: 龙虎榜/北向区块（该股上榜时展示）。
- [ ] Step 6: 分钟K缺失降级 + 按需拉取按钮。
- [ ] Step 7: Commit `feat(frontend): 股票详情扩展`。

#### Task H5：板块中心页（BP-V1.5-005）
**Files**: `pages/Sector.tsx`, `pages/SectorDetail.tsx`, `api/sector.ts`
- [ ] Step 1: 板块排行表（行业/概念 Tab，排序）+ 资金流向条形 + 涨停分布。
- [ ] Step 2: 板块详情：成分股表（龙头高亮、涨停/领涨标记）。
- [ ] Step 3: 点击跳个股详情。
- [ ] Step 4: Commit `feat(frontend): 板块中心页`。

#### Task H6：龙虎榜页（BP-V1.5-002）
**Files**: `pages/DragonTiger.tsx`, `api/dragonTiger.ts`
- [ ] Step 1: 日期选择 + 上榜列表 + 机构/游资筛选。
- [ ] Step 2: 席位明细折叠展开。
- [ ] Step 3: Commit `feat(frontend): 龙虎榜页`。

#### Task H7：admin 后台页（BP-V1.5-010）
**Files**: `pages/Admin.tsx`, `api/admin.ts`
- [ ] Step 1: 数据源管理（状态 + 测试连通性按钮）。
- [ ] Step 2: 任务管理（列表 + 立即执行 + 历史日志抽屉）。
- [ ] Step 3: 用户管理（ProTable + 禁用/角色，二次确认）。
- [ ] Step 4: 403 处理（非 admin）。
- [ ] Step 5: Commit `feat(frontend): admin 后台页`。

---

### 阶段 I：联调与验收

#### Task I1：端到端联调
- [ ] Step 1: 完整走通：股票列表 → 个股详情（多周期+新指标+筹码+资金）→ 板块中心 → 龙虎榜 → admin 触发任务。
- [ ] Step 2: 验证非功能指标（见 §七里程碑验收）。
- [ ] Step 3: 修复联调问题，补集成测试。
- [ ] Step 4: Commit `test: V1.5 端到端联调`。

---

## 七、依赖关系与里程碑

```text
A(表结构/鉴权) ──► B(采集) ──► C(情绪计算) ──► D(行情/指标/资金 service)
                                          └──► E(板块/股票列表)
                                          └──► F(admin)
                                          └──► G(scheduler 注册)
H(前端骨架) ──► H2~H7(前端页面) ──► I(联调)
```

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 | 阶段 A+B+C+D | 后端能采集分钟K/龙虎榜/北向/分单/板块，能算情绪指标与 RSI/BOLL/筹码 |
| M2 | 阶段 E+F+G | 板块/股票列表/admin 接口可用，调度任务注册 |
| M3 | 阶段 H | 前端 5 个新页面 + 详情/总览扩展可交互 |
| M4 | 阶段 I | 端到端联调，性能达标 |

**性能验收清单**：
- 日K < 500ms、分钟K < 800ms、指标 < 500ms、板块 < 1s、情绪 < 800ms、股票列表 < 800ms（均 P95）；
- AI 分析 < 15s、回测 < 10s、首屏 < 3s（V1 不退化）。

---

## 八、Spec 覆盖矩阵（自检）

| PRD V1.5 需求 | 对应任务 |
|---|---|
| BP-V1.5-001 分钟K | B1、H4 |
| BP-V1.5-002 龙虎榜 | B2、D4、H6、H4 |
| BP-V1.5-003 北向资金 | B3、D4、H3 |
| BP-V1.5-004 分单资金 | B4、D4、H4 |
| BP-V1.5-005 板块中心 | B5、E1、H5 |
| BP-V1.5-006 情绪指标 | C1、H3 |
| BP-V1.5-007 指标扩展(EMA/RSI/BOLL/筹码) | D1、D3、H4 |
| BP-V1.5-008 资金/财务图表化 | D4、H4 |
| BP-V1.5-009 多周期切换 | D2、H4 |
| BP-V1.5-010 admin 后台 | A2、F1、G、H7 |
| BP-V1.5-011 行情总览增强 | C1、H3 |
| BP-V1.5-012 股票列表 | E2、H2 |
| §4.1 性能 | 各阶段验证 + I1 |
| §4.2 合规（公开数据源/无交易/风险提示） | B* 数据源选择、RiskNotice 沿用 |
| §4.3 可用性（任务告警/重跑） | G + F1（sa_admin_task_log）|
| §4.4 安全（admin 鉴权/限流） | A2、F1、ratelimit |

覆盖完整，无遗漏。

---

## 九、风险与注意事项

| 风险 | 影响 | 缓解 |
|---|---|---|
| 涨停判定口径与实际有偏差（ST/新股/特殊处理） | 情绪数据不准 | C1 重点测试，按代码前缀+名称判断，边界用例覆盖 |
| `chip_distribution` 字段名未知 | 筹码峰阻塞 | A2 第一步先 `DESC` 探查再映射 |
| `stock_pool` 只读约定 vs 行业回填 | B6 方案不定 | 实施前确认；备选 `sa_stock_industry` 补充表 |
| AkShare 接口 schema 随版本变化 | 采集失败 | 采集器 `@retry` + 字段缺失降级 + admin 日志告警 |
| 分钟K数据量大、打库 | 性能/数据源限流 | 仅热门股池批量 + 按需拉取 + 索引 + 缓存 |
| 数据源限流被 ban | 全量采集失败 | `_throttle` 间隔 + 失败降级 + 不阻断页面 |

> 各采集器实施时，第一步均为「用真实数据源验证一次接口 schema」，避免凭记忆写字段映射
> （V1 的 `fetch_money_flow`/`fetch_financial_abstract` 即因未验证而留 stub）。
