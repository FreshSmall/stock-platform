# AI股票分析系统需求文档（PRD V2.5 运行可靠性加固）

> 版本：V2.5 再裁剪·运行可靠性加固（让日常运行可靠）
> 对应 ROADMAP：阶段四 T4.1 / T4.3
> 立项日期：2026-09-09
> 前置：阶段一~三已交付——V2.1 数据修复（质量日报/PIT 池/交易状态上线，全量重灌收尾中）、
> V2.2 研究闭环（BP-V2.2-001~008 全上线）、V3a 模拟盘闭环（BP-V3a-001~006 全上线，M3 观察期进行中）。
> 原排期 2026-12（里程碑 M4），因前置阶段提前完成而提前启动。

## 一、项目背景（V2.5 增量）

### 1.1 版本定位：为什么在功能闭环之后做"运行可靠性"

平台功能面已覆盖"数据 → 因子 → 策略 → 回测 → 模拟盘"全流程，每天 21 个调度 job 自动推进，
模拟盘进入无人值守观察期。但**"每天自动跑"本身的可靠性保障是欠账**——当前的问题不是功能缺失，
而是故障不可见：

1. **三个 job 无状态记录**：`daily_k_sync` / `daily_k_sync_retry` / `finance_sync` 直接注册在
   scheduler（`app/scheduler.py`），不落 `sa_admin_task_log`——每日管线最核心的日 K 同步恰恰是记录盲区，
   失败只能翻容器日志；finance_sync 绕开 admin 框架的原因（全量超 300s deadline）从未被正式解决。
2. **无管线视图**：21 个 job 分散注册、各自记录，"今天的管线走到哪一步、哪步失败、失败后重试了没有"
   没有统一答案；现有 admin 任务页是任务视角，不是"交易日流水线"视角。
3. **失败告警缺位**：job 异常只写 logger；代码注释里记录过调度线程冻结 20+ 小时才被发现的事故。
   质量巡检只覆盖数据内容，不覆盖"任务本身没跑 / 跑挂"；无任何推送或主动提醒机制。
4. **配置债**：`quality_check_enabled` 开关定义后全库无引用（job 无条件注册，开关形同虚设）；
   APScheduler executor 线程池未显式配置（默认 10）。

ROADMAP 阶段四已论证：单人单机、无并发流量的前提下，性能与部署类架构升级
（Redis / Celery / ClickHouse / K8s / 微服务）**全部转触发式**。本版本聚焦**任务链状态化**，
外加一页触发式升级预案存档；原列入阶段四的「数据备份与恢复」（T4.2）经立项评审**移出平台侧**——
数据库为外部阿里云 RDS，备份由云快照与运维层承接，平台侧仅在预案文档登记确认结论（见 §四 非目标）。

### 1.2 版本目标（V2.5 全量 4 项）

> 每日管线每步状态落库可查、失败自动重试并当日告警；各触发式升级项的触发条件与验收标准一页存档。

- **T4.1 → BP-V2.5-001/002/003**：管线状态模型（`pipeline_run/pipeline_step` 两表）＋
  调度全收口（三个漏记 job 纳管、步骤级失败重试、缺跑检测、pipeline_health 告警）＋
  Admin 管线视图（当日时间线 / 历史色带 / 失败步重跑）。
- **T4.3 → BP-V2.5-004**：触发式升级预案一页存档（Redis / Celery / 存储分区 / 微服务 / K8s / ES 六项）。

### 1.3 成功标准（= ROADMAP 阶段四退出条件的工程子集）

1. 任意交易日打开 Admin 管线页：当日 run 的每一步（含收口后的 daily_k_sync / finance_sync）状态、
   耗时、错误、尝试次数可查；同日重复调度幂等（UK 约束）；
2. 人为制造单步失败：按策略自动重试并计入 attempts；重试穷尽后当日落 `pipeline_health` fail 行，
   质量页红绿灯可见；交易日应有步骤缺跑时，次日 08:00 巡检告警（step_missing）；
3. 触发式升级预案一页存档完成（六项），每项含触发条件 / 方案要点 / 验收标准。

### 1.4 范围声明

本版本不新增任何投研功能与 AI 输出；AI 输出声明沿用 V2。

## 二、总体架构（V2.5 增量）

### 2.1 技术栈（无新增服务组件）

维持 FastAPI + APScheduler（进程内）+ MySQL + docker-compose 双服务部署，无新增组件与容器依赖。
明确不引入 Redis / Celery / 消息队列 / K8s（触发式结论不变，预案见 BP-V2.5-004）。

### 2.2 关键架构决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 管线模型 | 新增 `sa_pipeline_run` / `sa_pipeline_step` 两表；step 通过可空 `task_log_id` **逻辑引用** `sa_admin_task_log`（不建外键） | ROADMAP 既定命名。`task_log` 保持"单次任务执行记录"职责（手动触发/长任务照旧），`step` 表达"交易日管线拓扑中的节点"；互不侵入既有 admin 任务页与长任务框架 |
| 步记录路径 | step 由**调度入口层**统一创建/更新，业务函数体不改 | 最小侵入：21 个 job 的服务函数不动，收口发生在 `scheduler.py` 注册层与 admin_service 包装层 |
| 漏记 job 收口 | daily_k_sync / daily_k_sync_retry / finance_sync 全部改走 admin_service；同步 deadline 支持按任务覆盖（finance_sync 全量场景配置更大 deadline 或转 `_LONG_TASKS`） | 消除记录盲区的根因是"deadline 不适配"，参数化 per-task 后绕行理由消失 |
| 失败重试 | **进程内、步骤级**：失败按固定间隔（默认 5 分钟）重试，默认 2 次（per-step 可配）；穷尽当日仍失败 → step=failed + 告警。不做持久化重试队列 | 日频任务天然有"次日兜底"（daily_k_sync_retry、backfill_on_startup 先例），重试只需覆盖瞬时抖动；跨重启续跑正是 Celery 的触发条件（见预案），本版本不越界 |
| 失败/缺跑告警 | 复用 `sa_data_quality_rule/check` 表族（`check_name='pipeline_health'`），同 factor_health / paper_health 模式；缺跑检测并入 08:00 quality_check，不新增 job | 零新告警表；admin 质量页红绿灯直接消费；阈值规则表可改 |
| 长任务记录 | 长任务（重灌/修复/回测/巡检）继续走 `sa_admin_task_log`（已有 status/progress/error），管线页同屏聚合展示，**不**为长任务建 step | T4.1 的"统一记录"在 task_log 层已满足；日管线 step 只表达"按日拓扑"，混入长任务会污染 run 状态语义 |

### 2.3 表结构变更（Alembic，一张迁移，head 接 `c8d1e4f6a9b3`）

```text
sa_pipeline_run   管线日实例：run_date(Date)、pipeline_type(daily|weekly)、
                  status(running|success|partial|failed|skipped)、
                  started_at、finished_at            UK(run_date, pipeline_type)
sa_pipeline_step  管线步骤：run_id、step_key(如 daily_k_sync)、seq、
                  status(pending|running|success|failed|skipped)、attempts、
                  started_at、finished_at、duration_ms、error(Text)、
                  task_log_id(BigInteger，逻辑引用 sa_admin_task_log.id，可空)
                  UK(run_id, step_key)
```

不改动任何既有表；告警复用质量表族（`check_name='pipeline_health'` 命名空间）。

### 2.4 每日时间线（V2.5 后）

```text
08:00  quality_check        【增强】数据质量六组检查 + 管线完整性（step_missing）
16:25~20:00  交易日管线（pipeline_type=daily，13 步）
  16:25 pool_sync → 16:35 index_sync → 16:45 sentiment_sync → 17:00 north_flow_sync
  → 17:05 money_flow_detail_sync → 17:10 sector_sync → 17:30 daily_k_sync
  → 18:00 dragon_tiger_sync → 18:10 market_agent_sync → 18:20 review_agent_sync
  → 19:00 trade_status_sync → 19:30 finance_sync → 20:00 paper_tick
  （23:00 daily_k_sync_retry 为 daily_k_sync 的补偿段：并入该 step 的 attempts 记录，
    独立执行时 step 语义为"daily_k 补偿"，不新增 step_key）
周六/周日  周末管线（pipeline_type=weekly）
  周六 09:00 delist_sync + 09:30 factor_health_check；周日 09:00 industry_map_sync
全天每 10min  backfill / rebuild tick：不进 step，执行摘要低频落 task_log
进程启动     backfill_on_startup：同上
```

### 2.5 Admin 管线页（新页签「管线」，复用 Admin.tsx 壳与轮询模式）

当日 run 时间线（每步一节点：计划时刻 / 实际开始 / 耗时 / 状态色）＋ 步骤明细 Drawer
（错误栈 / attempts / 关联 task_log 的 result_json）＋ 近 7/30 日状态色带（同质量页迷你趋势模式）＋
当日长任务执行区（task_log 聚合）＋ 失败步骤"重跑"入口（复用既有 `run_task`）。

## 三、V2.5 功能范围

### 3.1 任务链状态化（T4.1）

#### BP-V2.5-001 · 管线状态模型【P0】（ROADMAP T4.1）

**用户故事**：作为平台唯一用户，我要"某交易日管线整体与每一步"有落库的事实记录
（状态 / 耗时 / 错误 / 尝试次数），让"今天数据为什么没 ready"变成查表，而不是翻容器日志。

**需求点**：
1. 两表 DDL + Alembic 迁移（可执行可回滚），字段见 2.3；
2. **管线拓扑配置化**：step 清单（step_key / 计划时刻 / 所属 pipeline_type / 是否交易日限定）收敛为
   单一注册表（代码常量），调度 job 注册从同一来源生成——消除"调度注册与管线定义两处维护"的漂移；
3. run 生命周期：当日该类型首个 step 启动时创建 run；全部 success → success；任一 failed →
   partial（其余步正常）/ failed（关键步失败，关键步=数据类）；非交易日 → skipped（不创建 run）；
4. step 幂等：UK(run_id, step_key) upsert；重复执行（手动重跑 / 补跑）更新既有行并累加 attempts。

**验收标准**：迁移 up/down/up 全周期验证；合成调度下 run/step 状态流转（成功 / 失败 / 跳过 / 重试计数）有测试锁定；
调度注册与拓扑注册表一致性有测试守护（新增 job 必须声明 step 归属或显式豁免）。

#### BP-V2.5-002 · 调度全收口与失败重试告警【P0】（ROADMAP T4.1，依赖 BP-001）

**用户故事**：作为平台唯一用户，任何 job 失败（或根本没跑）我要当天在页面上看到红绿灯，
且瞬时失败已被自动重试消化，不需要我盯着日志。

**需求点**：
1. **收口三个漏记 job**：daily_k_sync / daily_k_sync_retry / finance_sync 全部经 admin_service 执行并落
   `sa_admin_task_log`；同步 deadline 支持按任务覆盖（`_TASK_DEADLINE_SEC` 参数化 per-task，
   finance_sync 全量场景配置更大值或转入 `_LONG_TASKS` 异步链路，取消其"绕开框架"的特例注释）；
2. **步骤级失败重试**：step 执行异常后按固定间隔（默认 5 分钟，可配）自动重试，默认 2 次（per-step 可配）；
   重试计入 attempts；当日重试穷尽仍失败 → step=failed + `pipeline_health` 告警
   （metric_name=`step_failed:<step_key>`，fail 阈值 1）；
3. **缺跑检测**：08:00 quality_check 增加管线完整性检查——上一交易日应有 step 无记录或非 success →
   `step_missing` 告警；
4. **配置清债**：`quality_check_enabled` 生效（False 时不注册质量 job）；scheduler executor
   线程池大小可配（默认 10 显式声明）；
5. tick 型 job（history_backfill / kline_rebuild）与 backfill_on_startup 的执行摘要低频落 task_log
   （如每次 tick 聚合或每日一行），不再完全游离于记录体系之外。

**验收标准**：模拟 daily_k_sync 失败→重试→第二次成功：run=success、step attempts=2、无告警；
穷尽失败→当日 pipeline_health 有 fail 行且质量页红绿灯可见；finance_sync 出现在 task_log 且超时行为
符合配置；quality_check_enabled=False 时质量 job 不注册（测试锁定）。

#### BP-V2.5-003 · Admin 管线视图【P1】（ROADMAP T4.1，依赖 BP-001/002）

**用户故事**：作为平台唯一用户，我要在一个页面看到当日管线全景（每步状态 / 耗时 / 错误）并能一键重跑
失败步骤，而不是逐个任务页翻、逐张告警找。

**需求点**：
1. Admin 页新增「管线」页签：当日 run 时间线视图（步骤节点状态色：成功绿 / 运行蓝 / 失败红 / 跳过灰，
   节点含计划时刻与实际耗时）＋ 汇总卡（成功率 / 总耗时 / 失败步数 / 告警数）；
2. 步骤明细 Drawer：错误栈、attempts、关联 task_log 详情（status / rows_affected / result_json / error）；
3. 历史视图：按日翻阅 + 近 7/30 日每日状态色带（复用质量页迷你趋势组件模式）；
4. 当日长任务区：task_log 中非管线任务（重灌 / 修复 / 回测 / 巡检）聚合列表，复用既有任务 Drawer；
5. 失败步骤"重跑"入口：调用既有 `run_task`，完成后回写 step 状态与 attempts；
6. API：`GET /admin/pipeline/today`、`GET /admin/pipeline/history?days=`、`GET /admin/pipeline/step/{id}`
   （命名实现时可调，语义不变）。

**验收标准**：页面与库中 run/step 状态一致（含失败重试后的中间态）；重跑入口生效并反映在 step 记录；
`npm run build` 通过。

### 3.2 触发式升级预案（T4.3）

#### BP-V2.5-004 · 触发式升级预案存档【P1】（ROADMAP T4.3）

**用户故事**：未来某天"页面变慢 / 要上分钟 K / 任务要跨重启续跑"时，我要打开一页文档就知道哪条触发
条件命中了、按什么方案升级、怎么验收——而不是重新论证一遍架构。

**需求点**：
1. 一页存档 `docs/architecture/trigger_upgrades.md`，覆盖六项：
   Redis（BP-V2-A01）/ Celery+Redis（A02）/ ClickHouse·MySQL 分区（A03）/ 微服务拆分（A04，结论"不做"）/
   K8s·部署规范化（A05）/ ES 全文检索（BP-V2-007）；
2. 每项固定结构：**触发条件**（可观测、可判定，如"页面 >3s"、"任务需跨重启续跑"）/ 推荐方案要点 /
   验收标准 / 预估规模 / 关联需求池条目；内容以 ROADMAP 阶段四裁剪结论为准细化；
3. 同步需求池 §3.3：A01~A05 条目回链预案文档；RDS 云快照策略（备份移出平台侧后的唯一数据库备份层）
   的确认结论登记入预案附录。

**验收标准**：文档覆盖六项且结构齐全；需求池条目回链生效。

## 四、非目标（本轮明确不做）

- **数据库备份与恢复（原 T4.2：mysqldump 定时备份 / 保留策略 / 异机存放 / 恢复演练）——
  2026-09-09 立项评审移出平台侧**：数据库为外部阿里云 RDS，备份由云快照（云控制台运维配置）承接，
  平台侧仅在触发式预案（BP-V2.5-004）登记快照确认结论；若未来需要平台自控备份（云外恢复、
  跨环境迁移），按触发条件补入预案后再立项；
- Celery / Redis / ClickHouse / K8s / 微服务拆分——触发式升级，未触发不动工（预案见 BP-V2.5-004）；
- 邮件 / IM 告警推送渠道——站内红绿灯够用；推送待"连续多日无人值守"成为常态再立项（P2）；
- 跨重启断点续跑的持久化任务队列——这正是 Celery 的触发条件本身，本版本不越界；
- 前端构建产物 / 报告文件等资产备份——代码在 git、报告可由平台服务再生；
- Prometheus / Grafana 等监控指标体系与 APM——单人单机无需独立监控栈。

## 五、与需求池 / ROADMAP 的衔接

- 本 PRD 的 4 条需求登记为需求池 **`BP-V2.5-001~004`**（V2.5 章节扩充；原 BP-V2-A01~A05 保留并回链
  触发式预案）；
- 对应 ROADMAP 阶段四任务：T4.1（001~003，规模 L）/ T4.3（004，S）；
  T4.2 数据备份与恢复移出平台侧（决策与理由见 §四 非目标），ROADMAP 阶段四退出标准已同步调整；
- 里程碑：**M4 运行可靠**（原 2026-12 底，随前置阶段提前交付相应提前）；
- 后续衔接：阶段五 V3b AI 深度化按特性滚动立项（T5.3 策略自动优化仅依赖阶段二，已满足，可并行启动）；
  `pipeline_run/step` 的状态语义为 Celery 触发式升级预留了迁移路径（step 记录结构可直接映射为任务队列消息）。
