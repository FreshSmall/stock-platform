# AI 股票分析系统 V2 实现方案（Implementation Plan）

> **配套需求**：[spec-需求AI股票分析系统需求文档_PRD_V2.md](./spec-需求AI股票分析系统需求文档_PRD_V2.md)
> **版本**：V2.0 · 2026-07-30
> **范围**：覆盖 PRD V2 全部 13 条业务需求（BP-V2-001~006, 008~013）
> **前置**：V1（MVP）+ V1.5（增量完善）已交付，本方案在既有代码上增量扩展。
> **架构策略**：业务优先，沿用 V1 模块化单体 + cachetools + APScheduler + MySQL；架构升级下沉 V2.5。

---

## 一、目标与架构（V2 增量）

**目标**：将平台从"分析工具"升级为"量化研究平台"——因子可研究、策略可组合、回测可深入、AI 可信赖（RAG + 多 Agent）。

**架构沿用 V1/V1.5**：FastAPI 单体（data/market/strategy/ai/user/admin 模块），React SPA，MySQL `stock_analysis` 库。
V2 重点扩展 `strategy`（因子库 + 多策略）和 `ai`（RAG + 多 Agent）两个模块。

**技术栈增量**：
- 因子计算：pandas/numpy（复用 V1.5 的 indicator_service 框架）
- 回测：backtrader（复用 V1 引擎，扩展 analyzer + 多股组合）
- 向量库：**pgvector 优先**；MySQL 环境不支持向量类型时退化为 `JSON` 列 + Python cosine
- RAG：LangChain RetrievalQA
- 不引入 Redis/Celery/ClickHouse/ES（V2.5）

---

## 二、文件结构（V2 新增/变更）

```
backend/
├── alembic/versions/
│   └── <rev>_v2_tables.py                # ★新增：7 张 V2 sa_ 表
├── app/
│   ├── models/
│   │   ├── factor.py                     # ★新增 sa_factor_value / sa_factor_ic
│   │   ├── portfolio.py                  # ★新增 sa_portfolio / sa_portfolio_holding
│   │   ├── knowledge.py                  # ★新增 sa_knowledge_doc / sa_knowledge_chunk
│   │   ├── news.py                       # ★新增 sa_news_sentiment
│   │   ├── agent.py                      # ★新增 sa_agent_report
│   │   └── backtest.py                   # ★变更 sa_backtest_result 加高级指标列
│   ├── factor/                           # ★新增：因子计算框架（领域能力，独立于 services）
│   │   ├── __init__.py
│   │   ├── base.py                       # Factor 基类 + FactorRegistry
│   │   ├── trend.py                      # MA/EMA/MACD/ADX/SuperTrend
│   │   ├── momentum.py                   # RSI/KDJ/ROC/CCI
│   │   ├── volatility.py                 # ATR/BOLL/HV
│   │   ├── volume.py                     # OBV/量比/换手率/量价比
│   │   ├── fundamental.py                # PE/PB/ROE/EPS/增长
│   │   ├── sentiment.py                  # 涨停/炸板/北向/连板
│   │   └── ic.py                         # IC/IR/分层收益计算
│   ├── strategy/
│   │   ├── ema_strategy.py               # ★新增
│   │   ├── trend_strategy.py             # ★新增（ADX + MA 多头）
│   │   ├── leader_strategy.py            # ★新增（板块龙头）
│   │   ├── board_strategy.py             # ★新增（打板）
│   │   ├── lowbuy_strategy.py            # ★新增（低吸）
│   │   ├── breakout_strategy.py           # ★新增（突破）
│   │   └── registry.py                   # ★变更：6 个 V2 策略 available=True + 补 cls
│   ├── services/
│   │   ├── factor_service.py             # ★新增 因子查询/IC/打分
│   │   ├── portfolio_service.py          # ★新增 组合 CRUD + 组合回测
│   │   ├── backtest_service.py           # ★变更 加高级指标 + 基准 + 多股
│   │   ├── knowledge_service.py          # ★新增 RAG 文档管理 + 向量检索
│   │   ├── news_service.py               # ★新增 新闻采集 + 情绪打分
│   │   └── agent_service.py              # ★新增 4 Agent 编排
│   ├── ai/
│   │   ├── embeddings.py                 # ★新增 文本向量化（LLM embedding）
│   │   ├── rag.py                        # ★新增 RetrievalQA 链
│   │   ├── sector_agent.py               # ★新增 板块 Agent
│   │   ├── market_agent.py               # ★新增 大盘 Agent
│   │   ├── review_agent.py               # ★新增 复盘 Agent
│   │   ├── recommend_agent.py            # ★新增 推荐 Agent
│   │   ├── tools.py                      # ★变更 扩展因子/板块/新闻工具
│   │   └── prompts.py                    # ★变更 4 Agent prompt 模板
│   ├── api/
│   │   ├── factor.py                     # ★新增 /factor/*
│   │   ├── portfolio.py                  # ★新增 /portfolio/*
│   │   ├── reports.py                    # ★新增 /reports/*
│   │   ├── news.py                       # ★新增 /news/*
│   │   ├── backtest.py                   # ★变更 加 drawdown/positions 端点
│   │   ├── strategy.py                   # ★变更 返回 8 策略
│   │   └── assistant.py                  # ★变更 RAG 知识库端点
│   ├── data/
│   │   ├── sync_factor.py                # ★新增 因子批量计算
│   │   ├── sync_news.py                  # ★新增 新闻采集
│   │   └── backfill.py                   # 沿用 V1.5
│   └── scheduler.py                      # ★变更 注册因子/新闻/Agent 任务
└── tests/                                # ★新增对应测试（见 §六）

frontend/src/
├── pages/
│   ├── Factor.tsx                        # ★新增 /factor
│   ├── FactorDetail.tsx                  # ★新增 /factor/:code
│   ├── Portfolio.tsx                     # ★新增 /portfolio
│   ├── Reports.tsx                       # ★新增 /reports
│   ├── Backtest.tsx                      # ★变更 高级指标 + 基准 + 回撤图
│   ├── Strategy.tsx                      # ★变更 8 策略激活
│   └── Assistant.tsx                     # ★变更 RAG 引用
├── components/
│   ├── FactorTree.tsx                    # ★新增 因子分类树
│   ├── ICChart.tsx                       # ★新增 IC 柱状 + 累积曲线
│   ├── LayeredReturns.tsx                # ★新增 分层收益条形
│   ├── DrawdownChart.tsx                 # ★新增 回撤曲线
│   ├── EquityVsBenchmark.tsx             # ★新增 净值对比
│   └── PositionChart.tsx                 # ★新增 持仓变化
└── api/
    ├── factor.ts / portfolio.ts / reports.ts / news.ts  # ★新增
    └── backtest.ts / assistant.ts / strategy.ts          # ★变更
```

**职责划分（沿用 V1）**：`api/` 薄路由 → `services/` 编排 → `factor/`/`strategy/`/`ai/` 领域能力；`models/`（ORM）与 `schemas/`（Pydantic）分离。

---

## 三、关键技术决策

### 3.1 因子框架设计（BP-V2-001 核心）

每个因子实现统一接口，注册到 `FactorRegistry`：

```python
# app/factor/base.py
class Factor(ABC):
    code: str            # 如 "ma5", "rsi14", "pe"
    name: str            # "MA5均线"
    category: str        # trend/momentum/volatility/volume/fundamental/sentiment
    params: dict         # 可调参数

    @abstractmethod
    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        """计算该因子在某股某日的值。"""

    def compute_series(self, db, stock, start, end) -> list[dict]:
        """区间序列（默认循环 compute，可被子类优化为批量）。"""
```

- **技术因子**（趋势/动量/波动率/成交量）复用 V1.5 `indicator_service` 现算，不落表（查询时计算）；
- **基本面/情绪因子**读 `sa_financial_extra`/`sa_market_sentiment`（V1.5 已有）；
- **因子值持久化**：仅 IC 检验和批量打分时落 `sa_factor_value`（避免全量预计算膨胀）。

### 3.2 IC 分析（BP-V2-013）

信息系数（Information Coefficient）= 因子值与未来 N 日收益的 Spearman 秩相关：
- 单期 IC：每个调仓日算一个 IC；
- 累积 IC：IC 序列累加，看因子稳定性；
- 分层收益：按因子值分 5 档，看各档未来收益；
- 因子衰减：N 取 1/5/10/20 日，看 IC 随持有期变化。

### 3.3 多股组合回测（BP-V2-003/005）

V1 回测是单股（`stock_pool[0]`）。V2 扩展为多股：
- **方案**：backtrader 对每只股票 `adddata`（带 `name`），策略内遍历 `self.datas` 生成信号；
- **等权或自定义权重**：通过 `sizer` 控制每只仓位；
- **基准对比**：额外加载基准（沪深 300 ETF `510300`）的 K 线，用 `TimeReturn` 同时跑策略与基准，输出净值对比。

### 3.4 RAG 向量库（BP-V2-006）

- **向量化**：文档分块（按段落，每块 ~500 字）→ LLM embedding API → 向量；
- **存储**：优先 pgvector（`sa_knowledge_chunk.embedding VECTOR(1536)`）；MySQL 不支持时退化为 `JSON` 列存向量，检索时 Python 算 cosine（文档规模 < 10 万块可接受）；
- **检索**：用户问题 → embedding → Top-K 相似块 → 注入 LLM 上下文 → 回答 + 来源标注。

> 实施前需确认 MySQL 版本是否支持 `VECTOR` 类型（MySQL 9.0+ 支持，或用 PostgreSQL+pgvector）。
> 降级方案：`embedding` 存 JSON，`knowledge_service.search()` 内存算 cosine。

### 3.5 多 Agent 编排（BP-V2-009~012）

4 个 Agent 共享统一的编排模式（参照 V1 `stock_agent`）：
```
gather_context(db, input) → 组装数据 → LLM 流式 → 解析结构化 → 落 sa_agent_report
```
- 复用 V1 `ai/llm_client` + 扩展 `tools.py`（新增 `query_sector`/`query_sentiment`/`query_news` 工具）；
- 每个 Agent 独立 prompt 模板；
- 报告每日盘后自动生成（scheduler）+ 支持手动触发。

---

## 四、数据库表设计（V2 新建 7 张 `sa_` 表 + 1 处 ALTER）

### 4.1 `sa_factor_value`（因子值，BP-V2-001）

```sql
CREATE TABLE `sa_factor_value` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `factor_code` VARCHAR(30) NOT NULL COMMENT '因子代码',
  `stock_code` VARCHAR(10) NOT NULL,
  `trade_date` DATE NOT NULL,
  `value` DECIMAL(18,6) DEFAULT NULL,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_factor_code_date` (`factor_code`,`stock_code`,`trade_date`),
  KEY `idx_date_code` (`trade_date`,`stock_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='因子每日计算值';
```

### 4.2 `sa_factor_ic`（因子有效性，BP-V2-001/013）

```sql
CREATE TABLE `sa_factor_ic` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `factor_code` VARCHAR(30) NOT NULL,
  `trade_date` DATE NOT NULL COMMENT '调仓日',
  `horizon` INT NOT NULL DEFAULT 5 COMMENT '收益计算天数',
  `ic` DECIMAL(8,4) DEFAULT NULL COMMENT 'Spearman秩相关',
  `ir` DECIMAL(8,4) DEFAULT NULL COMMENT '信息比率IC均值/IC标准差',
  `win_rate` DECIMAL(8,4) DEFAULT NULL,
  `layered_returns` JSON DEFAULT NULL COMMENT '5档分层收益',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_factor_date_horizon` (`factor_code`,`trade_date`,`horizon`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='因子IC检验';
```

### 4.3 `sa_portfolio` + `sa_portfolio_holding`（组合，BP-V2-005）

```sql
CREATE TABLE `sa_portfolio` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT DEFAULT NULL,
  `name` VARCHAR(50) NOT NULL,
  `description` VARCHAR(200) DEFAULT NULL,
  `benchmark` VARCHAR(20) DEFAULT '510300' COMMENT '基准代码',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户组合';

CREATE TABLE `sa_portfolio_holding` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `portfolio_id` BIGINT NOT NULL,
  `stock_code` VARCHAR(10) NOT NULL,
  `weight` DECIMAL(6,4) NOT NULL DEFAULT 0.5 COMMENT '权重0-1',
  `added_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_pf_stock` (`portfolio_id`,`stock_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='组合持仓';
```

### 4.4 `sa_backtest_result` 变更（BP-V2-003）

```sql
ALTER TABLE `sa_backtest_result`
  ADD COLUMN `calmar` DECIMAL(10,4) DEFAULT NULL COMMENT '卡玛比率' AFTER `sharpe`,
  ADD COLUMN `information_ratio` DECIMAL(10,4) DEFAULT NULL AFTER `calmar`,
  ADD COLUMN `profit_loss_ratio` DECIMAL(10,4) DEFAULT NULL COMMENT '盈亏比' AFTER `information_ratio`,
  ADD COLUMN `drawdown_curve` JSON DEFAULT NULL COMMENT '回撤曲线' AFTER `equity_curve`,
  ADD COLUMN `position_curve` JSON DEFAULT NULL COMMENT '持仓变化' AFTER `drawdown_curve`,
  ADD COLUMN `benchmark_curve` JSON DEFAULT NULL COMMENT '基准净值曲线' AFTER `position_curve`,
  ADD COLUMN `benchmark_return` DECIMAL(10,4) DEFAULT NULL AFTER `benchmark_curve`;
```

### 4.5 `sa_knowledge_doc` + `sa_knowledge_chunk`（RAG，BP-V2-006）

```sql
CREATE TABLE `sa_knowledge_doc` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `title` VARCHAR(200) NOT NULL,
  `source` VARCHAR(100) DEFAULT NULL COMMENT '来源(研报/公告/财报)',
  `stock_code` VARCHAR(10) DEFAULT NULL COMMENT '关联个股',
  `doc_date` DATE DEFAULT NULL,
  `content` MEDIUMTEXT,
  `status` VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT 'pending/embedded/failed',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_stock` (`stock_code`),
  FULLTEXT KEY `ft_title` (`title`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='RAG知识库文档';

CREATE TABLE `sa_knowledge_chunk` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `doc_id` BIGINT NOT NULL,
  `chunk_index` INT NOT NULL,
  `text` TEXT NOT NULL,
  `embedding` JSON DEFAULT NULL COMMENT '向量(pgvector不可用时用JSON)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_doc_idx` (`doc_id`,`chunk_index`),
  KEY `idx_doc` (`doc_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文档分块+向量';
```

### 4.6 `sa_news_sentiment`（新闻情绪，BP-V2-008）

```sql
CREATE TABLE `sa_news_sentiment` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `pub_time` DATETIME NOT NULL,
  `title` VARCHAR(300) NOT NULL,
  `content` TEXT,
  `source` VARCHAR(50) DEFAULT NULL,
  `stock_codes` JSON DEFAULT NULL COMMENT '关联个股列表',
  `sector` VARCHAR(50) DEFAULT NULL COMMENT '关联板块',
  `sentiment` DECIMAL(4,3) DEFAULT NULL COMMENT '情绪分-1~+1',
  `summary` VARCHAR(500) DEFAULT NULL COMMENT 'LLM摘要',
  PRIMARY KEY (`id`),
  KEY `idx_pub_time` (`pub_time`),
  KEY `idx_sector` (`sector`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='新闻与情绪';
```

### 4.7 `sa_agent_report`（Agent 报告，BP-V2-009~012）

```sql
CREATE TABLE `sa_agent_report` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `agent` VARCHAR(20) NOT NULL COMMENT 'sector/market/review/recommend',
  `trade_date` DATE NOT NULL,
  `title` VARCHAR(200) DEFAULT NULL,
  `target` VARCHAR(50) DEFAULT NULL COMMENT '板块代码/股票代码/留空(大盘)',
  `summary` TEXT,
  `content` MEDIUMTEXT COMMENT '完整报告(markdown)',
  `scores` JSON DEFAULT NULL COMMENT '评分维度',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_agent_date` (`agent`,`trade_date`),
  KEY `idx_target` (`target`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent分析报告';
```

> 共新建 8 张表 + 1 处 ALTER，由一个 Alembic 迁移承接。

---

## 五、API 设计（V2 增量，REST + SSE）

> 沿用 `/api/v1` + `{code:0,msg:"ok",data}`。

### 5.1 因子（BP-V2-001/013）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/factor?category=` | 因子列表（分类） |
| GET | `/factor/{code}/compute?stock=&start=&end=` | 因子值序列 |
| GET | `/factor/{code}/ic?horizon=&start=&end=` | IC 分析（IC/IR/胜率） |
| GET | `/factor/{code}/layered-returns?horizon=` | 分层收益 |
| POST | `/factor/score` | 多因子加权打分（BP-V2-004） |

### 5.2 策略（BP-V2-002）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/strategy` | 8 种策略全部 available |
| GET | `/strategy/{code}/params` | 参数定义 |

### 5.3 回测（BP-V2-003）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/backtest` | 回测（增 benchmark 参数） |
| GET | `/backtest/{run_id}` | 结果（含 calmar/IR/盈亏比） |
| GET | `/backtest/{run_id}/drawdown` | 回撤曲线 |
| GET | `/backtest/{run_id}/positions` | 持仓变化 |

### 5.4 组合（BP-V2-005）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/portfolio` | 列表/创建 |
| GET/PUT/DELETE | `/portfolio/{id}` | 详情/更新/删除 |
| POST | `/portfolio/{id}/backtest` | 组合回测 |

### 5.5 RAG 知识库（BP-V2-006）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/assistant/knowledge` | 上传文档（分块+向量化） |
| GET | `/assistant/knowledge` | 文档列表 |
| DELETE | `/assistant/knowledge/{id}` | 删除 |
| POST | `/assistant/sessions/{id}/messages` | 问答（自动 RAG） |

### 5.6 Agent 报告（BP-V2-009~012）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/reports?agent=&date=` | 报告列表 |
| GET | `/reports/{id}` | 报告详情 |
| POST | `/reports/{agent}/generate` | 手动触发生成 |

### 5.7 新闻情绪（BP-V2-008）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/news?stock=&sector=&date=` | 新闻 + 情绪分 |

---

## 六、实现任务分解（Task Breakdown）

> 采用 TDD。任务按依赖顺序编排，每任务产出可独立测试的改动。
> 分 5 个阶段（J~N，承接 V1.5 的 I）。

### 阶段 J：因子库（BP-V2-001/013，P0，研究基石）

#### Task J1：因子框架 + ORM + 迁移
**Files**: `factor/base.py`, `models/factor.py`, `alembic/<rev>_v2_tables.py`
- [ ] Step 1: `factor/base.py`：`Factor` 抽象基类 + `FactorRegistry`（register/get/list）。
- [ ] Step 2: `models/factor.py`：`SaFactorValue` + `SaFactorIC` ORM。
- [ ] Step 3: Alembic 迁移：建 `sa_factor_value`/`sa_factor_ic` + `sa_backtest_result` ALTER。
- [ ] Step 4: 测试 `test_factor_base`：注册/查询/分类过滤。
- [ ] Step 5: Commit `feat(factor): 因子框架 + 表结构`。

#### Task J2：6 大类因子实现
**Files**: `factor/{trend,momentum,volatility,volume,fundamental,sentiment}.py`
- [ ] Step 1: trend（MA/EMA/MACD 复用 indicator_service + 新增 ADX/SuperTrend）。
- [ ] Step 2: momentum（RSI/KDJ 复用 + 新增 ROC/CCI）。
- [ ] Step 3: volatility（BOLL 复用 + 新增 ATR/HV）。
- [ ] Step 4: volume（新增 OBV/量比/换手率/量价比）。
- [ ] Step 5: fundamental（PE/PB/ROE/EPS/增长，读 stock_pool/sa_financial_extra）。
- [ ] Step 6: sentiment（涨停/炸板/北向/连板，读 sa_market_sentiment/sa_north_flow）。
- [ ] Step 7: 测试每个因子 `compute` 返回合理值。
- [ ] Step 8: Commit `feat(factor): 6大类因子实现`。

#### Task J3：因子 service + IC 分析 + API
**Files**: `services/factor_service.py`, `api/factor.py`
- [ ] Step 1: `factor/ic.py`：IC（Spearman）/IR/分层收益计算。
- [ ] Step 2: `factor_service`：compute_series / compute_ic / layered_returns / multi_factor_score。
- [ ] Step 3: `api/factor.py`：5 个端点注册。
- [ ] Step 4: 测试 IC 计算（已知数据验证）。
- [ ] Step 5: Commit `feat(factor): IC分析 + 因子API`。

---

### 阶段 K：多策略 + 高级回测（BP-V2-002/003，P0）

#### Task K1：6 种新策略
**Files**: `strategy/{ema,trend,leader,board,lowbuy,breakout}_strategy.py`, `registry.py`
- [ ] Step 1: EMA 策略（金叉死叉，复用 calc_ema）。
- [ ] Step 2: trend（ADX>阈值 + MA 多头排列）。
- [ ] Step 3: leader（板块涨幅 Top N + 放量）。
- [ ] Step 4: board（触及涨停 + 封板率）。
- [ ] Step 5: lowbuy（回踩 MA20 + 缩量）。
- [ ] Step 6: breakout（突破 BOLL 上轨 + 放量）。
- [ ] Step 7: registry：6 个 available=True + 补 cls。
- [ ] Step 8: 测试每个策略产生买卖信号。
- [ ] Step 9: Commit `feat(strategy): 6种新策略`。

#### Task K2：高级回测指标 + 基准对比
**Files**: `services/backtest_service.py`(改)
- [ ] Step 1: 加 analyzer：Calmar（年化收益/最大回撤）、returns（算 IR）、VWR（盈亏比）。
- [ ] Step 2: 回撤曲线：从 DrawDown.len / DrawDown.max 提取逐日序列。
- [ ] Step 3: 持仓曲线：扩展 `_TradeRecorder` 记录逐日仓位。
- [ ] Step 4: 基准：加载基准 K 线 → TimeReturn → 净值对比序列。
- [ ] Step 5: 测试高级指标非空。
- [ ] Step 6: Commit `feat(backtest): 高级指标 + 基准对比`。

#### Task K3：组合回测 + API
**Files**: `services/portfolio_service.py`, `models/portfolio.py`, `api/portfolio.py`, `api/backtest.py`(改)
- [ ] Step 1: portfolio ORM + service（CRUD + 组合净值）。
- [ ] Step 2: 组合回测：多股 adddata + sizer 权重。
- [ ] Step 3: API：portfolio CRUD + backtest drawdown/positions 端点。
- [ ] Step 4: 测试组合回测（2 股等权）。
- [ ] Step 5: Commit `feat(portfolio): 组合管理 + 回测`。

---

### 阶段 L：RAG 知识库（BP-V2-006，P0）

#### Task L1：文档管理 + 向量化
**Files**: `models/knowledge.py`, `ai/embeddings.py`, `services/knowledge_service.py`
- [ ] Step 1: 先确认 MySQL 是否支持 VECTOR 类型；不支持则用 JSON 降级。
- [ ] Step 2: ORM：`SaKnowledgeDoc` + `SaKnowledgeChunk`。
- [ ] Step 3: `ai/embeddings.py`：调 LLM embedding API（mock 可测）。
- [ ] Step 4: `knowledge_service`：ingest_doc（分块 + 向量化 + 落库）/ search（cosine Top-K）。
- [ ] Step 5: 测试 ingest + search（mock embedding）。
- [ ] Step 6: Commit `feat(rag): 文档管理 + 向量检索`。

#### Task L2：RAG 问答链
**Files**: `ai/rag.py`, `api/assistant.py`(改)
- [ ] Step 1: `ai/rag.py`：RetrievalQA（问题→检索→注入→LLM→回答+来源）。
- [ ] Step 2: assistant API：knowledge 端点 + 问答自动 RAG。
- [ ] Step 3: 测试问答返回带来源标注。
- [ ] Step 4: Commit `feat(rag): RAG问答链`。

---

### 阶段 M：多 Agent（BP-V2-009~012，P1）

#### Task M1：工具扩展 + 4 Agent
**Files**: `ai/{sector,market,review,recommend}_agent.py`, `ai/tools.py`(改), `ai/prompts.py`(改), `services/agent_service.py`, `models/agent.py`, `api/reports.py`
- [ ] Step 1: tools 扩展：query_sector/query_sentiment/query_news/query_factor。
- [ ] Step 2: 4 个 Agent（gather_context → LLM → 结构化 → 落库）。
- [ ] Step 3: agent_service：统一编排 + 报告 CRUD。
- [ ] Step 4: API：reports 端点（列表/详情/触发）。
- [ ] Step 5: 测试每个 Agent（mock LLM）。
- [ ] Step 6: Commit `feat(ai): 4个Agent + 报告`。

---

### 阶段 N：新闻情绪 + 调度 + 前端

#### Task N1：新闻采集 + 情绪打分
**Files**: `models/news.py`, `data/sync_news.py`, `services/news_service.py`, `api/news.py`
- [ ] Step 1: 新闻采集（AkShare 财联社，复用 _with_timeout 超时保护）。
- [ ] Step 2: LLM 情绪打分（-1~+1）+ 实体识别。
- [ ] Step 3: API + 测试。
- [ ] Step 4: Commit `feat(news): 采集 + 情绪打分`。

#### Task N2：scheduler 注册 V2 任务
**Files**: `scheduler.py`(改)
- [ ] Step 1: 注册：因子计算（盘后）、IC 检验（周末）、新闻（盘中）、Agent 报告（盘后）。
- [ ] Step 2: Commit `feat(scheduler): V2任务注册`。

#### Task N3：前端（因子/组合/报告 3 新页 + 回测/策略/助手增强）
**Files**: `pages/{Factor,FactorDetail,Portfolio,Reports}.tsx`, `components/{FactorTree,ICChart,LayeredReturns,DrawdownChart,EquityVsBenchmark,PositionChart}.tsx`
- [ ] Step 1: 因子研究页（分类树 + IC 图 + 分层收益）。
- [ ] Step 2: 组合页（CRUD + 净值对比）。
- [ ] Step 3: 报告页（4 Tab + 详情）。
- [ ] Step 4: 回测增强（高级指标 + 回撤/持仓/净值图）。
- [ ] Step 5: 策略页 8 策略激活。
- [ ] Step 6: 助手 RAG 引用展示。
- [ ] Step 7: tsc 类型检查通过。
- [ ] Step 8: Commit `feat(frontend): V2页面`。

---

## 七、依赖关系与里程碑

```text
J(因子库) ──► K(多策略+高级回测) ──► N(前端)
        └──► M(多Agent，依赖因子+V1.5数据)
L(RAG) ──► N(助手增强)
N(新闻) ──► M(复盘Agent)
```

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 | 阶段 J | 因子库 30+ 可算，IC 分析可用 |
| M2 | 阶段 K | 8 策略可回测，10+ 指标 + 基准对比 |
| M3 | 阶段 L | RAG 文档导入 + 问答带来源 |
| M4 | 阶段 M+N | 4 Agent 报告 + 全部前端页面 |

**性能验收**：因子计算 <500ms、IC<10s、RAG<1s、组合回测<15s、Agent<30s。

---

## 八、Spec 覆盖矩阵（自检）

| PRD V2 需求 | 对应任务 |
|---|---|
| BP-V2-001 因子库 | J1、J2、J3 |
| BP-V2-002 多策略 | K1 |
| BP-V2-003 高级回测 | K2 |
| BP-V2-004 多因子 | J3（multi_factor_score） |
| BP-V2-005 组合 | K3 |
| BP-V2-006 RAG | L1、L2 |
| BP-V2-008 新闻情绪 | N1 |
| BP-V2-009 板块 Agent | M1 |
| BP-V2-010 大盘 Agent | M1 |
| BP-V2-011 复盘 Agent | M1 |
| BP-V2-012 推荐 Agent | M1 |
| BP-V2-013 因子研究平台 | J3（IC/分层） |
| BP-V2-007 ES（下沉V2.5）| — |
| BP-V2-014 模拟交易（下沉V3）| — |

覆盖完整，无遗漏。

---

## 九、风险与注意事项

| 风险 | 影响 | 缓解 |
|---|---|---|
| MySQL 不支持 VECTOR 类型 | RAG 阻塞 | L1 Step 1 先探查；降级 JSON+cosine |
| backtrader 多股组合性能 | 组合回测慢 | 限制组合 ≤ 10 股；加缓存 |
| LLM embedding 成本 | RAG 导入贵 | 分块粒度 ≥500 字；批量 embedding |
| 因子全量预计算膨胀 | 表过大 | 仅 IC/打分时落表；技术因子现算 |
| 新闻/研报采集受数据源限制 | 数据稀疏 | 复用 _with_timeout + 失败降级 |
| Agent LLM 调用费用 | 成本 | 报告每日生成 1 次 + 手动触发限流 |

> 各 Agent/embedding 实施时 mock LLM 测试，保证 CI 可跑（沿用 V1 策略）。
