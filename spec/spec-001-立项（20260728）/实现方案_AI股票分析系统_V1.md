# AI 股票分析系统 V1 实现方案（Implementation Plan）

> **配套需求**：[spec-需求AI股票分析系统需求文档_PRD_V1.2.md](./spec-需求AI股票分析系统需求文档_PRD_V1.2.md)
> **版本**：V1.0 · 2026-07-28
> **范围**：覆盖 PRD V1.2 第三章 V1 功能范围（MVP）全部需求

---

## 一、目标与架构

**目标**：搭建「日K数据 → K线展示 → AI 股票分析 → MA/MACD 策略回测 → 自然语言问答」的最小可运行闭环。

**架构**：后端采用 FastAPI 模块化单体（按业务领域分模块），前端 React SPA，共用现有 MySQL `stock_analysis` 库（行情只读复用，产物表加 `sa_` 前缀）。V1 不引入 Redis，用 `cachetools` 进程内缓存过渡。

**技术栈**：
- 后端：Python 3.11 + FastAPI + SQLAlchemy 2.0 + Alembic + APScheduler + pandas/TA-Lib/backtrader + LangChain
- 前端：React 18 + TypeScript + Vite + Ant Design 5 + ECharts + Zustand + React Query
- 数据库：MySQL（复用 `stock_analysis`）
- 部署：Docker Compose

---

## 二、文件结构（File Structure）

```
stock-platform/
├── backend/                              # 后端（FastAPI 模块化单体）
│   ├── pyproject.toml                    # 依赖与项目元数据
│   ├── .env.example                      # 环境变量样例（不含真实密钥）
│   ├── alembic.ini                       # Alembic 配置
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/                     # 迁移脚本
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                       # FastAPI 应用入口、路由挂载、调度启动
│   │   ├── core/
│   │   │   ├── config.py                 # 配置（pydantic-settings 读环境变量）
│   │   │   ├── database.py               # SQLAlchemy engine/session
│   │   │   ├── security.py               # JWT 签发/校验、密码哈希
│   │   │   ├── deps.py                   # FastAPI 依赖注入（get_db/get_current_user）
│   │   │   ├── cache.py                  # cachetools 进程内缓存封装
│   │   │   └── errors.py                 # 统一异常与错误码
│   │   ├── models/                       # SQLAlchemy ORM
│   │   │   ├── base.py                   # Declarative Base、命名约定
│   │   │   ├── stock.py                  # 复用既有表映射（DailyPrice/StockPool）
│   │   │   ├── ai.py                     # sa_ai_analysis / sa_ai_chat_*
│   │   │   ├── backtest.py              # sa_backtest_run / sa_backtest_result
│   │   │   ├── finance.py               # sa_money_flow / sa_financial_extra
│   │   │   └── user.py                  # sa_user
│   │   ├── schemas/                      # Pydantic 请求/响应模型
│   │   │   ├── common.py                # 通用响应包装、分页
│   │   │   ├── stock.py
│   │   │   ├── market.py
│   │   │   ├── ai.py
│   │   │   ├── backtest.py
│   │   │   └── user.py
│   │   ├── api/                          # 路由层（薄）
│   │   │   ├── router.py                # 汇总各模块路由
│   │   │   ├── stock.py                 # /stock/* 行情与详情
│   │   │   ├── market.py                # /market/* 行情总览
│   │   │   ├── analysis.py             # /analysis/* AI 分析（SSE 流）
│   │   │   ├── strategy.py            # /strategy/* 策略列表
│   │   │   ├── backtest.py            # /backtest/* 回测
│   │   │   ├── assistant.py           # /assistant/* AI 助手（SSE 流）
│   │   │   └── auth.py                 # /auth/* 注册/登录
│   │   ├── services/                     # 业务逻辑层
│   │   │   ├── market_service.py       # 大盘/涨跌家数/成交额
│   │   │   ├── indicator_service.py    # MA/MACD/KDJ 计算
│   │   │   ├── analysis_service.py     # AI 分析编排
│   │   │   ├── backtest_service.py     # 回测编排
│   │   │   ├── assistant_service.py    # AI 助手 + Function Calling
│   │   │   └── user_service.py         # 用户注册/登录
│   │   ├── ai/
│   │   │   ├── llm_client.py           # LangChain LLM 封装（DeepSeek/通义）
│   │   │   ├── stock_agent.py          # 股票分析 Agent（结构化输出）
│   │   │   ├── tools.py                # Function Calling 工具集
│   │   │   └── prompts.py              # Prompt 模板
│   │   ├── strategy/
│   │   │   ├── base.py                 # 策略基类
│   │   │   ├── ma_strategy.py          # MA 金叉/死叉
│   │   │   ├── macd_strategy.py        # MACD 金叉/死叉 + 背离
│   │   │   └── registry.py             # 策略注册表
│   │   ├── data/                        # 数据采集
│   │   │   ├── akshare_client.py       # AkShare 封装（限流/重试）
│   │   │   ├── sync_daily.py           # 日K增量同步
│   │   │   ├── sync_finance.py         # 财务/资金补采
│   │   │   └── validators.py           # 数据校验
│   │   └── scheduler.py                # APScheduler 任务注册
│   └── tests/
│       ├── conftest.py                  # pytest fixture（测试库/客户端）
│       ├── test_indicator_service.py
│       ├── test_strategy_ma.py
│       ├── test_strategy_macd.py
│       ├── test_backtest_service.py
│       ├── test_analysis_service.py
│       ├── test_assistant_service.py
│       ├── test_market_service.py
│       ├── test_auth.py
│       └── test_sync_daily.py
├── frontend/                            # 前端（React + Vite）
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts                   # 代理 /api → backend
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx                      # 路由 + 布局
│       ├── api/                         # 接口封装（axios + React Query）
│       │   ├── client.ts
│       │   ├── stock.ts
│       │   ├── market.ts
│       │   ├── analysis.ts
│       │   ├── strategy.ts
│       │   ├── backtest.ts
│       │   └── assistant.ts
│       ├── store/                       # Zustand
│       │   └── authStore.ts
│       ├── components/
│       │   ├── Layout/                  # 顶部栏+侧栏+底部风险提示
│       │   ├── StockSearch.tsx
│       │   ├── KLineChart.tsx           # ECharts K线+MA+成交量+MACD/KDJ
│       │   ├── RiskNotice.tsx
│       │   └── EmptyState.tsx
│       ├── pages/
│       │   ├── Market.tsx               # /market
│       │   ├── StockDetail.tsx          # /stock/:code
│       │   ├── Analysis.tsx             # /analysis/:code
│       │   ├── Strategy.tsx             # /strategy
│       │   ├── Backtest.tsx             # /backtest
│       │   ├── Assistant.tsx            # /assistant
│       │   └── Login.tsx                # /login
│       └── utils/
│           ├── format.ts                # 红涨绿跌色值、数字格式
│           └── sse.ts                   # SSE 流式接收封装
├── docker-compose.yml                   # backend + frontend 一键起
├── Dockerfile.backend
├── Dockerfile.frontend
└── Makefile                             # make dev / make test / make migrate
```

**职责划分原则**：
- `api/` 只做参数校验与调用 service，不写业务逻辑（薄路由）。
- `services/` 封装业务编排，可被 api 和 scheduler 复用。
- `ai/`、`strategy/`、`data/` 是领域能力，service 组合它们。
- `models/`（ORM）与 `schemas/`（Pydantic）分离，避免 ORM 泄漏到接口。

---

## 三、关键技术决策

### 3.1 数据库连接与 ORM

- SQLAlchemy 2.0 同步引擎（V1 不需要异步，降低复杂度；V2 升级异步）。
- 既有表（`daily_prices`/`stock_pool` 等）用 `automap_base` 或显式映射，**只读**。
- 新建 `sa_` 表用显式 ORM 定义，迁移由 Alembic 管理。

### 3.2 缓存（V1 不用 Redis）

用 `cachetools.TTLCache` 实现：
- 行情查询缓存（key=stock_code+date_range，TTL=5分钟）
- AI 分析结果冷却（key=stock_code，TTL=10分钟，配合限流）

### 3.3 AI 流式输出

FastAPI 用 `StreamingResponse` 返回 SSE（`text/event-stream`）：
- 分析 Agent：逐段（基本面/技术面/...）流式输出
- 助手：逐 token 流式
- 前端用 `EventSource` 或 `fetch + ReadableStream` 接收

### 3.4 限流

V1 用进程内令牌桶（`cachetools` + 时间窗），按用户 + 按接口限流：
- AI 分析：同一股票 10 分钟内 1 次
- 助手问答：每用户每分钟 10 条

### 3.5 策略与回测

- 用 `backtrader` 作为回测引擎，自定义 `Strategy` 子类实现 MA/MACD。
- 策略结果（收益率/最大回撤/夏普/胜率/收益曲线）由 `backtrader.Analyzer` 产出。

### 3.6 测试策略

- 单元测试：service / strategy / indicator / ai 工具函数，用 pytest + fixture。
- LLM 调用全部 mock（不依赖真实 API key，保证 CI 可跑）。
- 数据库测试用独立测试 schema 或事务回滚隔离。

---

## 四、数据库表设计（新建 `sa_` 表 DDL）

> 既有表 `daily_prices` / `stock_pool` 只读复用，DDL 见现有库。以下为新建表。

### 4.1 `sa_user`（用户与认证）

```sql
CREATE TABLE `sa_user` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(50) NOT NULL,
  `password_hash` VARCHAR(128) NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='平台用户(JWT认证)';
```

### 4.2 `sa_ai_analysis`（AI 按需分析结果）

```sql
CREATE TABLE `sa_ai_analysis` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `request_id` VARCHAR(64) NOT NULL COMMENT '本次分析请求ID',
  `stock_code` VARCHAR(10) NOT NULL,
  `score` DECIMAL(5,2) DEFAULT NULL COMMENT '综合评分0-100',
  `score_fundamental` DECIMAL(5,2) DEFAULT NULL,
  `score_technical` DECIMAL(5,2) DEFAULT NULL,
  `score_capital` DECIMAL(5,2) DEFAULT NULL,
  `score_news` DECIMAL(5,2) DEFAULT NULL,
  `score_risk` DECIMAL(5,2) DEFAULT NULL,
  `fundamentals` TEXT COMMENT '基本面分析(markdown)',
  `technicals` TEXT,
  `capital` TEXT,
  `news` TEXT,
  `risk` TEXT,
  `full_text` MEDIUMTEXT COMMENT '完整分析文本',
  `user_id` BIGINT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_request_id` (`request_id`),
  KEY `idx_code_created` (`stock_code`,`created_at` DESC),
  KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI按需股票分析结果';
```

### 4.3 `sa_ai_chat_session` / `sa_ai_chat_message`（助手对话）

```sql
CREATE TABLE `sa_ai_chat_session` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64) NOT NULL,
  `user_id` BIGINT NOT NULL,
  `title` VARCHAR(100) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_session_id` (`session_id`),
  KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI助手会话';

CREATE TABLE `sa_ai_chat_message` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64) NOT NULL,
  `role` VARCHAR(10) NOT NULL COMMENT 'user/assistant/tool',
  `content` MEDIUMTEXT NOT NULL,
  `tool_calls` TEXT COMMENT 'Function Calling 元数据(JSON)',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_session_created` (`session_id`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI助手消息';
```

### 4.4 `sa_backtest_run` / `sa_backtest_result`（回测）

```sql
CREATE TABLE `sa_backtest_run` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `run_id` VARCHAR(64) NOT NULL,
  `user_id` BIGINT DEFAULT NULL,
  `strategy` VARCHAR(20) NOT NULL COMMENT 'ma/macd',
  `params` JSON NOT NULL COMMENT '策略参数',
  `stock_pool` JSON NOT NULL COMMENT '股票池',
  `start_date` DATE NOT NULL,
  `end_date` DATE NOT NULL,
  `initial_cash` DECIMAL(18,2) NOT NULL,
  `commission` DECIMAL(6,4) NOT NULL DEFAULT 0.0003,
  `slippage` DECIMAL(6,4) NOT NULL DEFAULT 0.0001,
  `status` VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT 'pending/running/done/failed',
  `error` TEXT,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `finished_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_run_id` (`run_id`),
  KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='回测任务';

CREATE TABLE `sa_backtest_result` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `run_id` VARCHAR(64) NOT NULL,
  `return_rate` DECIMAL(10,4) DEFAULT NULL COMMENT '总收益率%',
  `max_drawdown` DECIMAL(10,4) DEFAULT NULL,
  `sharpe` DECIMAL(8,4) DEFAULT NULL,
  `win_rate` DECIMAL(8,4) DEFAULT NULL,
  `equity_curve` JSON DEFAULT NULL COMMENT '收益曲线[(date,equity),...]',
  `trades` JSON DEFAULT NULL COMMENT '交易明细',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_run_id` (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='回测结果指标';
```

### 4.5 `sa_money_flow` / `sa_financial_extra`（数据补采）

```sql
CREATE TABLE `sa_money_flow` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `trade_date` DATE NOT NULL,
  `main_net_inflow` DECIMAL(18,2) DEFAULT NULL COMMENT '主力净流入(元)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_date` (`stock_code`,`trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='主力资金净流入';

CREATE TABLE `sa_financial_extra` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `stock_code` VARCHAR(10) NOT NULL,
  `report_date` DATE NOT NULL COMMENT '财报期',
  `roe` DECIMAL(10,4) DEFAULT NULL,
  `eps` DECIMAL(12,4) DEFAULT NULL,
  `revenue_growth` DECIMAL(10,4) DEFAULT NULL COMMENT '营收增长率%',
  `profit_growth` DECIMAL(10,4) DEFAULT NULL COMMENT '净利润增长率%',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_report` (`stock_code`,`report_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='深度财务补充';
```

---

## 五、API 设计（REST + SSE）

> 所有接口前缀 `/api/v1`。响应统一包装：`{code:0, msg:"ok", data:{...}}`。
> 需登录的接口走 JWT Bearer。

### 5.1 认证 `/auth`

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| POST | `/auth/register` | 注册 | 否 |
| POST | `/auth/login` | 登录，返回 JWT | 否 |
| GET | `/auth/me` | 当前用户 | 是 |

### 5.2 行情总览 `/market`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/market/indices` | 三大指数（上证/深证/创业板）点位+涨跌幅 |
| GET | `/market/summary` | 涨跌家数、市场成交额 |
| GET | `/market/hot-stocks` | 热门个股（按成交额/涨幅排序）|

### 5.3 股票详情 `/stock`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stock/search?q=` | 股票搜索（代码/名称/拼音）|
| GET | `/stock/{code}` | 基础信息（名称/行业/市值/PE/PB）|
| GET | `/stock/{code}/kline?start=&end=` | 日K数据 |
| GET | `/stock/{code}/indicators?start=&end=&type=ma\|macd\|kdj` | 技术指标 |

### 5.4 AI 分析 `/analysis`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/analysis/{code}` | 触发分析（返回 request_id）|
| GET | `/analysis/{code}/stream?request_id=` | SSE 流式输出分析结果 |
| GET | `/analysis/{code}/latest` | 最近一次分析结果 |

### 5.5 策略 `/strategy`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/strategy` | 策略列表（V1 返回 ma/macd，其余置灰）|

### 5.6 回测 `/backtest`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/backtest` | 提交回测（返回 run_id）|
| GET | `/backtest/{run_id}` | 查询回测状态与结果 |
| GET | `/backtest/{run_id}/trades` | 交易明细（分页）|

### 5.7 AI 助手 `/assistant`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/assistant/sessions` | 新建会话 |
| GET | `/assistant/sessions` | 会话列表 |
| POST | `/assistant/sessions/{id}/messages` | 发送消息（触发 SSE）|
| GET | `/assistant/sessions/{id}/messages/{msg_id}/stream` | SSE 流式回答 |

---

## 六、实现任务分解（Task Breakdown）

> 采用 TDD：每个功能先写失败测试 → 实现 → 测试通过 → 提交。
> 任务按依赖顺序编排，每个任务产出可独立测试的改动。

### 阶段 A：项目骨架与基础设施

#### Task A1：后端项目初始化

**Files**:
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/core/config.py`
- Create: `backend/.env.example`

- [ ] **Step 1**: 创建 `pyproject.toml`，声明依赖：fastapi、uvicorn、sqlalchemy[2.0]、alembic、pydantic-settings、pymysql、cryptography、cachetools、apscheduler、pandas、numpy、talib、backtrader、langchain、langchain-community、httpx、python-jose、passlib[bcrypt]、akshare。

- [ ] **Step 2**: 创建 `app/core/config.py`，用 pydantic-settings 从环境变量读取配置（DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME、LLM_API_KEY、LLM_MODEL、JWT_SECRET、JWT_ALG=HS256、JWT_EXP_MINUTES=1440）。

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    db_host: str
    db_port: int = 3306
    db_user: str
    db_password: str
    db_name: str = "stock_analysis"
    llm_api_key: str
    llm_model: str = "deepseek-chat"
    jwt_secret: str
    jwt_alg: str = "HS256"
    jwt_exp_minutes: int = 1440
    class Config:
        env_file = ".env"

settings = Settings()
```

- [ ] **Step 3**: 创建 `app/main.py`，初始化 FastAPI 应用、CORS、路由占位、健康检查 `/api/v1/health`。

- [ ] **Step 4**: 创建 `.env.example`（不含真实值），并 `pip install -e .` 安装。

- [ ] **Step 5**: 运行 `uvicorn app.main:app --reload`，访问 `/api/v1/health` 返回 `{"code":0,"msg":"ok","data":{"status":"up"}}`。

- [ ] **Step 6**: Commit `feat(backend): 项目初始化与配置`。

#### Task A2：数据库连接与 ORM 基类

**Files**:
- Create: `backend/app/core/database.py`
- Create: `backend/app/models/base.py`
- Create: `backend/app/models/stock.py`（既有表映射）

- [ ] **Step 1**: 在 `database.py` 创建 engine 与 `SessionLocal`（连接现有 `stock_analysis` 库）。

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings

url = f"mysql+pymysql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}?charset=utf8mb4"
engine = create_engine(url, pool_pre_ping=True, pool_size=10, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

class Base(DeclarativeBase): pass
```

- [ ] **Step 2**: 在 `models/stock.py` 显式映射既有只读表 `DailyPrice`、`StockPool`（字段对应现有 DDL）。

- [ ] **Step 3**: 写测试 `tests/test_db.py`：连接库后能查询 `daily_prices` 一行（用 600519）。

- [ ] **Step 4**: 运行 `pytest tests/test_db.py -v` 通过。

- [ ] **Step 5**: Commit `feat(backend): 数据库连接与既有表映射`。

#### Task A3：Alembic 迁移与新建 `sa_` 表

**Files**:
- Create: `backend/alembic.ini`, `backend/alembic/env.py`
- Create: `backend/app/models/{user,ai,backtest,finance}.py`
- Create: `backend/alembic/versions/0001_init_sa_tables.py`

- [ ] **Step 1**: `alembic init alembic`，配置 `env.py` 指向 `Base.metadata` 与数据库 URL。

- [ ] **Step 2**: 在各 models 文件定义 §四 的 `sa_` 表 ORM（对应 DDL 字段）。

- [ ] **Step 3**: `alembic revision --autogenerate -m "init sa tables"` 生成迁移。

- [ ] **Step 4**: `alembic upgrade head` 在 `stock_analysis` 库创建 `sa_` 表。

- [ ] **Step 5**: 验证：`SHOW TABLES LIKE 'sa\_%'` 返回 8 张表。

- [ ] **Step 6**: Commit `feat(backend): Alembic 迁移与 sa_ 表`。

#### Task A4：通用响应、依赖注入、缓存

**Files**:
- Create: `app/schemas/common.py`, `app/core/deps.py`, `app/core/cache.py`, `app/core/errors.py`

- [ ] **Step 1**: `common.py` 定义 `ApiResponse[T]`、`PageParams`、`PageResult[T]`。

- [ ] **Step 2**: `deps.py` 提供 `get_db()`（yield session）、`get_current_user()`（解析 JWT）。

- [ ] **Step 3**: `cache.py` 封装 `TTLCache` 工具函数 `cached(ttl, key)`。

- [ ] **Step 4**: `errors.py` 定义 `BizError(code, msg)` 与全局异常处理器。

- [ ] **Step 5**: Commit `feat(backend): 通用响应/依赖/缓存`。

---

### 阶段 B：数据与行情

#### Task B1：股票搜索与详情

**Files**:
- Create: `app/api/stock.py`, `app/services/market_service.py`(部分), `app/schemas/stock.py`

- [ ] **Step 1**: 写测试 `tests/test_market_service.py::test_stock_search`：搜索"茅台"返回 600519。

- [ ] **Step 2**: 实现 `stock_search(q)`：查 `stock_pool`（名称/代码模糊匹配）。

- [ ] **Step 3**: 实现 `get_stock_info(code)`：从 `stock_pool` 取基础信息（名称/行业/市值/PE/PB/上市日）。

- [ ] **Step 4**: 实现 K线接口：查 `daily_prices`，支持日期范围，**加 5 分钟 TTLCache**。

- [ ] **Step 5**: 注册路由 `/stock/search`、`/stock/{code}`、`/stock/{code}/kline`。

- [ ] **Step 6**: `pytest` 通过，手动验证 600519 K线 < 500ms。

- [ ] **Step 7**: Commit `feat(market): 股票搜索与详情接口`。

#### Task B2：技术指标计算（MA/MACD/KDJ）

**Files**:
- Create: `app/services/indicator_service.py`, `tests/test_indicator_service.py`

- [ ] **Step 1**: 写失败测试 `test_calc_ma`：给定收盘价序列，MA5/M10/M20 正确。

- [ ] **Step 2**: 实现 `calc_ma(closes, periods=[5,10,20])`（pandas rolling）。

- [ ] **Step 3**: 写测试 `test_calc_macd`：验证 MACD 金叉/死叉点。

- [ ] **Step 4**: 实现 `calc_macd(closes)`（EMA12/EMA26/DIF/DEA/MACD柱）。

- [ ] **Step 5**: 写测试 `test_calc_kdj`：验证 KDJ 计算。

- [ ] **Step 6**: 实现 `calc_kdj(highs, lows, closes)`。

- [ ] **Step 7**: 路由 `/stock/{code}/indicators?type=` 返回指标序列。

- [ ] **Step 8**: Commit `feat(market): MA/MACD/KDJ 指标计算`。

#### Task B3：行情总览

**Files**:
- Modify: `app/services/market_service.py`, `app/api/market.py`

- [ ] **Step 1**: 实现 `get_indices()`：三大指数（用 `daily_prices` 查指数代码或 AkShare 实时）。

- [ ] **Step 2**: 实现 `get_market_summary()`：当日涨跌家数（统计 `daily_prices` 当日 pct_change）+ 总成交额。

- [ ] **Step 3**: 实现 `get_hot_stocks(sort='amount'|'pct_change', limit=20)`。

- [ ] **Step 4**: 注册 `/market/*` 路由，加缓存。

- [ ] **Step 5**: Commit `feat(market): 行情总览接口`。

#### Task B4：数据采集与调度

**Files**:
- Create: `app/data/akshare_client.py`, `app/data/sync_daily.py`, `app/data/validators.py`, `app/scheduler.py`

- [ ] **Step 1**: `akshare_client.py` 封装 AkShare 常用接口（日K、财务、资金），含限流与重试（`tenacity`）。

- [ ] **Step 2**: `validators.py`：校验缺失值、异常涨跌幅（>±20% 告警）、重复键。

- [ ] **Step 3**: `sync_daily.py`：增量拉取当日全市场日K写入 `daily_prices`（UPSERT）。

- [ ] **Step 4**: `sync_finance.py`：补采 ROE/EPS/营收增长写入 `sa_financial_extra`，主力资金写入 `sa_money_flow`。

- [ ] **Step 5**: 写测试 `test_sync_daily.py`：mock akshare，验证 UPSERT 与校验。

- [ ] **Step 6**: `scheduler.py` 用 APScheduler 注册：交易日 16:00 同步日K，每季度补财务。

- [ ] **Step 7**: Commit `feat(data): 数据采集与定时同步`。

---

### 阶段 C：策略与回测

#### Task C1：策略基类与 MA 策略

**Files**:
- Create: `app/strategy/base.py`, `app/strategy/ma_strategy.py`, `app/strategy/registry.py`, `tests/test_strategy_ma.py`

- [ ] **Step 1**: 写失败测试 `test_ma_golden_cross_signal`：MA5 上穿 MA20 产生买入信号。

- [ ] **Step 2**: `base.py` 定义策略基类（基于 backtrader.Strategy：参数声明、信号生成接口）。

- [ ] **Step 3**: `ma_strategy.py` 实现 `MAStrategy(bt.Strategy)`：params=(fast=5, slow=20)，`next()` 中 `crossover(self.fast, self.slow)` 触发买卖。

- [ ] **Step 4**: 测试通过。

- [ ] **Step 5**: Commit `feat(strategy): MA 金叉死叉策略`。

#### Task C2：MACD 策略

**Files**:
- Create: `app/strategy/macd_strategy.py`, `tests/test_strategy_macd.py`

- [ ] **Step 1**: 写测试 `test_macd_strategy`：金叉买入、死叉卖出、顶背离减仓。

- [ ] **Step 2**: 实现 `MACDStrategy(bt.Strategy)`：DIF/DEA 金叉死叉 + 简单背离检测。

- [ ] **Step 3**: 测试通过。

- [ ] **Step 4**: Commit `feat(strategy): MACD 策略`。

#### Task C3：回测服务

**Files**:
- Create: `app/services/backtest_service.py`, `app/api/backtest.py`, `app/schemas/backtest.py`, `tests/test_backtest_service.py`

- [ ] **Step 1**: 写测试 `test_run_backtest_ma`：给定 600519 一年数据 + MA 策略，返回收益率/回撤/夏普/胜率。

- [ ] **Step 2**: 实现 `run_backtest(run_id)`：从库读日K → 构建 `bt.Cerebro` → 加载策略 → 加 Analyzer → `run()` → 提取指标。

- [ ] **Step 3**: Analyzer 产出：`return_rate`、`max_drawdown`、`sharpe`、`win_rate`、`equity_curve`、`trades`。

- [ ] **Step 4**: 路由 `POST /backtest`（写 `sa_backtest_run`，异步执行）、`GET /backtest/{run_id}`（状态+结果）。

- [ ] **Step 5**: 验证单股 5 年回测 < 10s。

- [ ] **Step 6**: Commit `feat(backtest): 回测引擎与接口`。

#### Task C4：策略列表接口

**Files**:
- Create: `app/api/strategy.py`

- [ ] **Step 1**: 实现 `GET /strategy`：从 `registry` 返回 ma/macd 元信息（名称/描述/参数定义），其余策略置灰标注 V2。

- [ ] **Step 2**: Commit `feat(strategy): 策略列表接口`。

---

### 阶段 D：AI 分析中心

#### Task D1：LLM 客户端封装

**Files**:
- Create: `app/ai/llm_client.py`, `app/ai/prompts.py`

- [ ] **Step 1**: `llm_client.py` 用 LangChain 封装 LLM（DeepSeek/通义），提供 `chat(messages)` 与 `stream_chat(messages)`。

- [ ] **Step 2**: `prompts.py` 定义股票分析 Prompt 模板（基本面/技术面/资金面/消息面/风险 + 综合评分，要求结构化 JSON 输出）。

- [ ] **Step 3**: 写测试 `test_llm_client`：mock LLM，验证 stream 产出。

- [ ] **Step 4**: Commit `feat(ai): LLM 客户端与 Prompt`。

#### Task D2：股票分析 Agent

**Files**:
- Create: `app/ai/stock_agent.py`, `app/services/analysis_service.py`, `app/api/analysis.py`, `tests/test_analysis_service.py`

- [ ] **Step 1**: 写测试 `test_analyze_stock`：mock LLM + 真实数据，验证返回结构化评分（0-100）+ 5 维分析。

- [ ] **Step 2**: `stock_agent.py`：编排——取行情/财务/资金 → 组装上下文 → 调 LLM 流式 → 解析 JSON 评分。

- [ ] **Step 3**: `analysis_service.py`：限流（同股 10 分钟 1 次，用 TTLCache）+ 结果落 `sa_ai_analysis`。

- [ ] **Step 4**: 路由 `POST /analysis/{code}`（生成 request_id）、`GET /analysis/{code}/stream`（SSE 流式）、`GET /analysis/{code}/latest`。

- [ ] **Step 5**: SSE 端点用 `StreamingResponse(media_type="text/event-stream")`，逐段 yield。

- [ ] **Step 6**: 验证响应 < 15s，每段附风险提示。

- [ ] **Step 7**: Commit `feat(ai): 股票分析 Agent 与 SSE 流`。

---

### 阶段 E：AI 助手

#### Task E1：Function Calling 工具集

**Files**:
- Create: `app/ai/tools.py`

- [ ] **Step 1**: 定义工具（LangChain `@tool`）：`query_kline(code, days)`、`search_signals(macd_golden=True)`、`run_backtest_light(strategy, code, days)`。

- [ ] **Step 2**: 写测试 `test_tools`：每个工具用真实/模拟数据验证返回结构。

- [ ] **Step 3**: Commit `feat(ai): Function Calling 工具集`。

#### Task E2：助手服务与会话管理

**Files**:
- Create: `app/services/assistant_service.py`, `app/api/assistant.py`, `app/schemas/ai.py`, `tests/test_assistant_service.py`

- [ ] **Step 1**: 写测试 `test_assistant_analyze_question`：问"分析 600519"，mock LLM 触发 `query_kline` 工具，返回分析。

- [ ] **Step 2**: `assistant_service.py`：会话 CRUD（落 `sa_ai_chat_*`）+ 多轮上下文 + 工具调用循环 + 流式回答。

- [ ] **Step 3**: 路由：`POST /sessions`、`GET /sessions`、`POST /sessions/{id}/messages`、SSE `GET .../stream`。

- [ ] **Step 4**: Function Calling 可视化：工具调用步骤作为 SSE 事件推送（前端渲染步骤条）。

- [ ] **Step 5**: Commit `feat(ai): 助手会话与流式问答`。

---

### 阶段 F：用户认证与限流

#### Task F1：注册/登录/JWT

**Files**:
- Create: `app/core/security.py`, `app/services/user_service.py`, `app/api/auth.py`, `tests/test_auth.py`

- [ ] **Step 1**: 写测试 `test_register_and_login`：注册→登录返回 token→`/auth/me` 返回用户。

- [ ] **Step 2**: `security.py`：`hash_password`/`verify_password`（bcrypt）、`create_token`/`decode_token`（python-jose）。

- [ ] **Step 3**: `user_service.py`：注册（查重+哈希+落库）、登录（校验+签发）。

- [ ] **Step 4**: 路由 `/auth/register`、`/auth/login`、`/auth/me`，保护需登录接口。

- [ ] **Step 5**: Commit `feat(auth): 注册登录与 JWT`。

#### Task F2：接口限流

**Files**:
- Modify: `app/core/deps.py`

- [ ] **Step 1**: 实现令牌桶限流依赖 `RateLimit(key_func, capacity, refill)`。

- [ ] **Step 2**: 应用到 AI 分析（同股 10 分钟 1 次）与助手（每用户每分钟 10 条）。

- [ ] **Step 3**: 写测试 `test_rate_limit`：超限返回 429。

- [ ] **Step 4**: Commit `feat(auth): 接口限流`。

---

### 阶段 G：前端骨架

#### Task G1：前端项目初始化

**Files**:
- Create: `frontend/*`（package.json、vite.config.ts、tsconfig.json、main.tsx）

- [ ] **Step 1**: `npm create vite@latest frontend -- --template react-ts`，装 antd、echarts、axios、@tanstack/react-query、zustand、react-router-dom。

- [ ] **Step 2**: `vite.config.ts` 配 `/api` 代理到 `http://localhost:8000`。

- [ ] **Step 3**: `api/client.ts`：axios 实例 + 请求拦截器注入 JWT + 响应拦截器解包 `ApiResponse`。

- [ ] **Step 4**: `store/authStore.ts`：Zustand 管理 token/user。

- [ ] **Step 5**: Commit `feat(frontend): 项目初始化`。

#### Task G2：全局布局与路由

**Files**:
- Create: `src/App.tsx`, `src/components/Layout/`, `src/components/StockSearch.tsx`, `src/components/RiskNotice.tsx`

- [ ] **Step 1**: `App.tsx` 配置路由（6 页 + 登录），未登录跳 `/login`。

- [ ] **Step 2**: Layout 组件：顶部栏（Logo+搜索+用户）、左侧导航（行情/分析/策略/回测/助手）、固定底部风险提示。

- [ ] **Step 3**: `StockSearch`：下拉候选 + 回车跳详情页。

- [ ] **Step 4**: Commit `feat(frontend): 全局布局与路由`。

---

### 阶段 H：前端页面（对应 PRD §5.3）

#### Task H1：行情总览页 `/market`

**Files**:
- Create: `src/pages/Market.tsx`, `src/api/market.ts`

- [ ] **Step 1**: `api/market.ts` 封装 3 个接口（React Query）。

- [ ] **Step 2**: 页面：指数卡片（红涨绿跌）、涨跌家数条形、热门个股表（点击跳详情，表头排序）。

- [ ] **Step 3**: 加载用 Skeleton，非交易日空态提示。

- [ ] **Step 4**: Commit `feat(frontend): 行情总览页`。

#### Task H2：股票详情页 `/stock/:code`

**Files**:
- Create: `src/pages/StockDetail.tsx`, `src/components/KLineChart.tsx`, `src/api/stock.ts`

- [ ] **Step 1**: `KLineChart`：ECharts 蜡烛图 + MA（图例显隐）+ 成交量副图 + MACD/KDJ 切换副图。

- [ ] **Step 2**: 交互：滚轮缩放、拖拽平移、双击重置、十字光标浮窗、周期切换（日K，其余置灰）。

- [ ] **Step 3**: 右侧财务卡片（PE/PB/ROE/EPS/营收/净利润增长）。

- [ ] **Step 4**: 「AI 分析」按钮跳 `/analysis/:code`。

- [ ] **Step 5**: Commit `feat(frontend): 股票详情页与K线`。

#### Task H3：AI 分析页 `/analysis/:code`

**Files**:
- Create: `src/pages/Analysis.tsx`, `src/api/analysis.ts`, `src/utils/sse.ts`

- [ ] **Step 1**: `utils/sse.ts`：封装 SSE 接收（fetch + ReadableStream）。

- [ ] **Step 2**: 综合评分卡（大号分数 + 等级 + 5 维雷达图）。

- [ ] **Step 3**: 分析正文 SSE 打字机渲染，每段附风险提示。

- [ ] **Step 4**: 「重新分析」（限流 Toast）、评分维度点击定位、复制。

- [ ] **Step 5**: Commit `feat(frontend): AI 分析页（SSE）`。

#### Task H4：策略列表页 `/strategy`

**Files**:
- Create: `src/pages/Strategy.tsx`, `src/api/strategy.ts`

- [ ] **Step 1**: 策略卡片网格（V1 两张可用，其余置灰 V2 标注）。

- [ ] **Step 2**: 点击「回测此策略」跳 `/backtest?strategy=ma`。

- [ ] **Step 3**: Commit `feat(frontend): 策略列表页`。

#### Task H5：回测页 `/backtest`

**Files**:
- Create: `src/pages/Backtest.tsx`, `src/api/backtest.ts`

- [ ] **Step 1**: 配置表单（策略/参数/股票池/日期/资金/费率/滑点）+ inline 校验。

- [ ] **Step 2**: 结果区：指标卡（收益率/回撤/夏普/胜率）+ 收益曲线（叠加买卖点）+ 交易明细表（分页/排序/导出 CSV）。

- [ ] **Step 3**: 进度条 + 取消，空结果/失败提示。

- [ ] **Step 4**: Commit `feat(frontend): 回测页`。

#### Task H6：AI 助手页 `/assistant`

**Files**:
- Create: `src/pages/Assistant.tsx`, `src/api/assistant.ts`

- [ ] **Step 1**: 对话流（用户右/AI 左，支持表格列表渲染），底部输入 + 示例 Chips。

- [ ] **Step 2**: SSE 流式打字机，Function Calling 步骤条（折叠展开工具结果）。

- [ ] **Step 3**: 每条 AI 回复附风险提示 + 复制/重新生成。

- [ ] **Step 4**: Commit `feat(frontend): AI 助手页`。

#### Task H7：登录页与全局规范

**Files**:
- Create: `src/pages/Login.tsx`, `src/utils/format.ts`

- [ ] **Step 1**: 登录/注册表单，成功后存 token 跳首页。

- [ ] **Step 2**: `format.ts`：红涨绿跌色值、千分位、百分比、价格格式。

- [ ] **Step 3**: 全站统一加载/空/错态组件。

- [ ] **Step 4**: Commit `feat(frontend): 登录页与全局规范`。

---

### 阶段 I：部署与联调

#### Task I1：Docker 化

**Files**:
- Create: `Dockerfile.backend`, `Dockerfile.frontend`, `docker-compose.yml`, `Makefile`

- [ ] **Step 1**: `Dockerfile.backend`（python:3.11-slim + 安装 + uvicorn）。

- [ ] **Step 2**: `Dockerfile.frontend`（node 构建 + nginx 静态）。

- [ ] **Step 3**: `docker-compose.yml`：backend + frontend 两服务，环境变量注入。

- [ ] **Step 4**: `Makefile`：`make dev`（起前后端）、`make test`、`make migrate`。

- [ ] **Step 5**: `docker compose up` 起服务，访问前端正常。

- [ ] **Step 6**: Commit `feat: Docker 化与一键启动`。

#### Task I2：端到端联调

- [ ] **Step 1**: 完整走通：登录 → 搜索 600519 → 看K线 → AI 分析 → 选 MA 策略回测 → 助手提问。

- [ ] **Step 2**: 验证非功能指标：K线 < 500ms、AI < 15s、回测 < 10s、首屏 < 3s。

- [ ] **Step 3**: 修复联调问题，补充集成测试。

- [ ] **Step 4**: Commit `test: 端到端联调与修复`。

---

## 七、依赖关系与里程碑

```text
A(骨架) ──► B(数据行情) ──► C(策略回测)
       │                  ──► D(AI分析) ──► E(AI助手)
       └──► F(认证限流)
G(前端骨架) ──► H(前端页面) ──► I(部署联调)
```

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 | 阶段 A+B+C | 后端能查行情、算指标、跑回测 |
| M2 | 阶段 D+E+F | AI 分析与助手问答可用 |
| M3 | 阶段 G+H | 前端 6 页可交互 |
| M4 | 阶段 I | Docker 部署、端到端联调通过 |

---

## 八、Spec 覆盖矩阵（自检）

| PRD V1.2 需求 | 对应任务 |
|---|---|
| §3.1 日K采集 | B4 |
| §3.1 基础财务/资金补采 | B4（sa_financial_extra/sa_money_flow）|
| §3.2 K线+MA/MACD/KDJ+成交量 | B1、B2、H2 |
| §3.2 行情总览（指数/涨跌家数/成交额）| B3、H1 |
| §3.3 股票分析 Agent（5维+评分）| D1、D2、H3 |
| §3.4 MA/MACD 策略 | C1、C2 |
| §3.4 回测（收益率/回撤/夏普/胜率/曲线）| C3、H5 |
| §3.5 AI 助手 + Function Calling | E1、E2、H6 |
| §4.1 性能（K线<500ms/AI<15s/回测<10s/首屏<3s）| 各阶段验证 + I2 |
| §4.2 合规（公开数据源/无交易/风险提示）| B4 数据源选择、RiskNotice 全站、AI 风险声明 |
| §4.3 可用性（任务告警/重跑）| B4 scheduler 日志 |
| §4.4 安全（JWT/限流/密钥环境变量）| F1、F2、A1 config |
| §5 前端 6 页交互 | H1–H7 |
| §2.2 数据层（复用+sa_表）| A3 |

覆盖完整，无遗漏。
