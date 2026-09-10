# AI股票分析系统 V2.5 实现方案（运行可靠性加固）

> 对应 PRD：spec-007 PRD V2.5（BP-V2.5-001~004，备份已移出平台侧）
> 实施日期：2026-09-09 起立项（ROADMAP 阶段四，~1.5 周）
> 维护约定：每完成一项勾选对应 checklist 并补"实际偏差"注记。

## 一、模块地图（新增/改动）

```text
backend/app/
├── models/
│   └── pipeline.py           [新] SaPipelineRun / SaPipelineStep 两表
├── alembic/versions/         [新] 迁移：sa_pipeline_run/step（UK/索引）
├── services/
│   ├── pipeline_service.py   [新] 拓扑注册表（单源）+ run/step 状态机 +
│   │                              失败重试调度 + pipeline_health 告警 +
│   │                              查询侧（today/history/detail）
│   ├── admin_service.py      [改] run_task / _finalize_run 挂管线钩子；
│   │                              deadline 按任务参数化；finance_sync 收口
│   └── quality_service.py    [改] 08:00 巡检增加 step_missing 检查
├── api/
│   └── admin.py              [改] /admin/pipeline/* 三个端点
├── scheduler.py              [改] cron 注册改为消费拓扑表；三漏记 job 收口；
│                                   quality_check_enabled 生效；线程池显式配置
├── core/config.py            [改] 新增 pipeline 相关配置项
└── data/
    ├── history_backfill.py   [改] tick 每日摘要行落 task_log
    └── backfill.py           [改] startup 补数结果落一行 task_log
                               （kline_rebuild 原本已每 tick 落行，未改）

frontend/src/
├── api/admin.ts              [改] fetchPipelineDaily / fetchPipelineSummary
├── components/PipelinePanel.tsx [新] 当日时间线 + 步骤 Drawer + 历史色带 +
│                                   长任务聚合区 + 失败步重跑
└── pages/Admin.tsx           [改] Segmented 加「管线」页签

docs/architecture/trigger_upgrades.md [新] BP-V2.5-004 触发式升级预案一页存档
```

## 二、拓扑注册表（单源，先于一切）

`pipeline_service.PIPELINE_TOPOLOGY`，调度注册、管线状态机、缺跑巡检、前端时间线
四处全部由它驱动——新增 job 必须在此声明（或显式豁免），一致性测试守护：

| step_key（=job id =task_name） | pipeline | 时刻 | critical | 说明 |
|---|---|---|---|---|
| pool_sync | daily | 16:25 | — | |
| index_sync | daily | 16:35 | — | |
| sentiment_sync | daily | 16:45 | — | |
| north_flow_sync | daily | 17:00 | — | |
| money_flow_detail_sync | daily | 17:05 | — | |
| sector_sync | daily | 17:10 | — | |
| **daily_k_sync** | daily | 17:30 | ✓ | 数据类，失败 → run=failed |
| dragon_tiger_sync | daily | 18:00 | — | |
| market_agent_sync | daily | 18:10 | — | |
| review_agent_sync | daily | 18:20 | — | |
| trade_status_sync | daily | 19:00 | ✓ | |
| **finance_sync** | daily | 19:30 | — | 收口（原直连注册） |
| paper_tick | daily | 20:00 | ✓ | 长任务链路 |
| delist_sync | weekly | 周六 09:00 | — | |
| factor_health_check | weekly | 周六 09:30 | — | 长任务链路 |
| industry_map_sync | weekly | 周日 09:00 | — | 长任务链路 |

**补偿段**（不新增 step_key，写回所属 step 的 attempts/status）：
`daily_k_sync_retry`（23:00）→ step_key=`daily_k_sync`。
**拓扑外 job**（显式豁免清单）：`history_backfill_tick` / `kline_rebuild_tick`（Interval 型，
每日摘要行落 task_log）、`startup_backfill`（启动 daemon，落一行 task_log）。

```python
@dataclass(frozen=True)
class StepSpec:
    step_key: str
    pipeline: str            # "daily" | "weekly"
    hour: int
    minute: int
    weekday: str = "mon-fri" # weekly 用 "sat"/"sun"
    critical: bool = False
    retry_max: int = 2       # 额外重试次数（0 = 不重试）
    retry_gap_min: int = 5   # 重试间隔（分钟）
```

## 三、表结构（Alembic 一张迁移，head 接 c8d1e4f6a9b3）

```text
sa_pipeline_run
  id BIGINT PK AUTO
  run_date DATE NOT NULL
  pipeline_type VARCHAR(8) NOT NULL        -- daily | weekly
  status VARCHAR(8) NOT NULL               -- running|success|partial|failed|skipped
  started_at DATETIME / finished_at DATETIME
  UK(run_date, pipeline_type)

sa_pipeline_step
  id BIGINT PK AUTO
  run_id BIGINT NOT NULL                   -- 逻辑引用 run.id，不建外键（房屋风格）
  step_key VARCHAR(50) NOT NULL
  seq INT NOT NULL                         -- 拓扑序，前端时间线排序
  status VARCHAR(8) NOT NULL               -- pending|running|success|failed|skipped
  attempts INT NOT NULL DEFAULT 1
  started_at / finished_at DATETIME
  duration_ms INT
  error TEXT
  task_log_id BIGINT NULL                  -- 逻辑引用 sa_admin_task_log.id
  UK(run_id, step_key)，INDEX(run_date 冗余列可选：经 run join 即可，不加)
```

## 四、关键实现约定

1. **挂钩位置在 admin_service，不在 scheduler**（最重要的设计选择）：`run_task`（同步）与
   `_finalize_run`（长任务收尾）在任务名属于拓扑时调 `pipeline_service.on_step_start /
   on_step_finish`。效果：**调度触发与手动重跑走同一条记账路径**——BP-V2.5-003 的"重跑后
   step 状态回写"零额外代码；scheduler 只剩纯注册职责。钩子内部自吞异常（记账失败只打日志，
   绝不阻断任务本体）。
2. **三个漏记 job 的失败可抛出改造（收口前置）**：`run_daily_sync` / `run_daily_sync_retry` /
   finance job 目前 catch-all 后仅打日志——若直接换注册方式，失败仍记 success。改造：
   拆出内函数（`_do_daily_sync() -> tuple[int, list[str]]`、`_do_finance_sync() -> int`，
   失败抛出），既有公开壳保留吞异常语义（调度线程防冻结注释不变，防 20 小时冻结事故重演）；
   admin runner 改调内函数。**这是行为变化点，独立 commit + 测试锁定"失败可抛出"。**
3. **deadline 按任务参数化**：`_TASK_DEADLINE_SEC: float` → `_TASK_DEADLINES: dict[str, float]`
   + 默认 300s。`finance_sync` 夜间全量给 3600s（`_run_with_deadline` 每次调用建独立单线程池，
   长跑不占别人）；手动触发的限量批 finance_sync 维持 300s 由 runner 区分（夜间调度走
   `run_task("finance_sync_nightly")` 或按 triggered_by 分流——实现时取简者，倾向注册
   `finance_sync` 一个 runner、deadline=3600，手动点击多等也可见进度）。
4. **步骤级重试（进程内）**：调度触发的 step 失败后，`pipeline_service` 请求 scheduler 注册
   一次性 `DateTrigger(now + retry_gap)` 重试 job（id=`retry:{step_key}:{n}`），attempts 递增；
   穷尽仍失败 → step=failed + 写 `pipeline_health` 告警。进程重启丢失重试是已知边界（次日
   08:00 巡检兜底；持久化重试= Celery 触发条件，不越界）。重试走 `run_task` 原路径（含
   task_log 与记账钩子）。
5. **run 状态机（纯函数，测试主战场）**：`recompute_run_status(steps) -> str`：
   任一 critical step failed → `failed`；任一非 critical failed → `partial`；
   全部 success/skipped → `success`；存在 pending/running → `running`。step 全部完成时回填
   run.finished_at。非交易日：钩子查 `_is_trade_day`，False 则不创建 run（任务照常执行，
   其内部守卫行为不变——不改变现有节假日执行语义，只影响记账）。
6. **告警编码（沿 paper_health 先例）**：`check_name='pipeline_health'`，
   metric_name=`step_failed:<step_key>` / `step_missing`；规则表播种
   `(pipeline_health, step_failed)` fail=1、`(pipeline_health, step_missing)` fail=1，
   分类时按 `:` 前缀取基础 metric 查规则（同 paper_service 的 account 后缀模式）。
   **与 PRD 的一处收窄**：巡检的 step_missing 只报"无记录"的步——非 success 的步已在失败
   瞬时告警过，巡检对其做幂等刷新（UK upsert）而非新增告警，避免同一天双份红点。
7. **step_missing 巡检并入 08:00**：`run_daily_check` 增加 `("pipeline_health", "step_missing")`
   检查项——对上一交易日（daily 管线）与上一周末（weekly 管线）的 run，逐 step 比对
   应有/实有；无 run（节假日误判）以交易日历为准豁免。该项失败不阻断其余检查（沿用
   逐项 try/except 模式）。
8. **tick 型 job 每日摘要**：history_backfill / kline_rebuild 的 `tick()` 尾部对 task_log
   upsert"当日摘要行"（task_name=`history_backfill_tick` / `kline_rebuild_tick`，查询当日
   已有行则更新 finished_at/status/rows，无则新建）——进 admin 任务历史但**不**进拓扑。
9. **配置清债**：`quality_check` 注册包 `if settings.quality_check_enabled:`；
   `BackgroundScheduler(executors={"default": ThreadPoolExecutor(settings.scheduler_pool_size)})`
   显式化（默认 10，行为不变）。新增配置：`scheduler_pool_size: int = 10`、
   `pipeline_step_retry_max: int = 2`、`pipeline_step_retry_gap_min: int = 5`、
   `pipeline_enabled: bool = True`（记账总开关，事故时一键退回纯 task_log）。
10. **一致性测试（拓扑守护）**：scheduler 注册逻辑抽纯函数 `_cron_job_specs()` 返回
    [(job_id, trigger 参数)]；测试断言：cron job 集合 == 拓扑 step_key 集合 ∪ 补偿段 ∪ 豁免清单。
    新增 job 不声明即红。
11. **幂等**：step UK(run_id, step_key) upsert（attempts 累加、status 覆盖最新）；
    run UK(run_date, pipeline_type)；告警 UK(check_date, check_name, metric_name)
    （质量表族既有约束）。同日重复调度 / 手动补跑不产生重复行。
12. **慢查询防线**：history 查询按 run_date 索引扫 N 天 ≤ 7/30 行 run + 每行 ≤16 step，
    无全表风险；step.error 截断 8KB。

## 五、API 契约（新增，挂 /api/v1/admin）

```text
GET  /admin/pipeline/daily?date=yyyy-mm-dd   （缺省=最近一个有 run 的日期）
  → { run: { run_date, pipeline_type, status, started_at, finished_at },
      steps: [ { step_key, seq, status, attempts, started_at, finished_at,
                 duration_ms, error, task_log: { status, rows_affected, result,
                 error, triggered_by } | null } ],
      long_tasks: [ { task_name, status, started_at, rows_affected } ] }   # 当日非拓扑任务
GET  /admin/pipeline/summary?days=30
  → [ { run_date, status, total, ok, failed, skipped } ]                  # 历史色带
GET  /admin/pipeline/steps/{run_id}/{step_key}
  → 步骤明细（含 task_log 完整 result_json/error）                          # Drawer 用
重跑：复用既有 POST /admin/tasks/{name}/run（记账钩子自动回写 step）
```

前端 PipelinePanel：当日横向步骤条（计划时刻/实际耗时/状态色）＋汇总卡（成功率/总耗时/
失败步/告警数）＋步骤 Drawer（错误栈/attempts/task_log result）＋近 30 日色带（复用
QualityPanel 迷你趋势模式）＋当日长任务区＋失败步「重跑」按钮（复用 runTask + 轮询模式）。
Admin.tsx Segmented 增加 `{ label: '管线', value: 'pipeline' }`。

## 六、实施顺序与 checklist

> 顺序原则：先地基（表 + 拓扑）→ 挂钩记账（无行为变化）→ 收口与重试（行为变化）→
> 巡检与前端 → 预案文档。每步以"状态机纯函数"与"幂等"两条测试主线推进。

### BP-V2.5-001 管线状态模型
- [x] models/pipeline.py 两表 + models/__init__ 导出
- [x] Alembic 迁移 `e7a2c9d4f1b6`（两表、UK、run_id 索引）；up/down/up 全周期验证（真实库执行通过）
- [x] pipeline_service：StepSpec / PIPELINE_TOPOLOGY（16 步）/ TASK_ALIASES / NON_TOPOLOGY_CRON_JOBS
- [x] ensure_run / on_step_start / on_step_finish（自吞异常）
- [x] recompute_run_status 纯函数 + 测试（success/partial/failed/running 全分支）
- [x] scheduler 抽 `_cron_job_specs()`；拓扑一致性测试（job id 集合 == 拓扑 ∪ 补偿 ∪ 巡检）

### BP-V2.5-002 调度全收口与失败重试告警
- [x] 内函数化改造：`_do_daily_sync` / `_do_daily_sync_retry`（失败可抛出、总量失败 raise；壳函数保留吞异常语义）
- [x] deadline 参数化（`_TASK_DEADLINES`：finance_sync_nightly=3600s，其余默认 300s）
- [x] run_task / run_task_async(_worker) 挂 on_step_* 钩子；手动触发与长任务收尾同路径记账
- [x] scheduler 三漏记 job 改走 admin 路径：daily_k_sync（17:30）、daily_k_sync_retry（23:00，别名写回
      daily_k_sync step）、finance_sync_nightly（19:30，别名写回 finance_sync step）
- [x] 步骤级重试（DateTrigger 一次性 job `retry:{step}:{n}` + attempts 递增 + 穷尽告警 step_failed:<key>）
- [x] 规则播种（pipeline_health 两条）+ quality_service 增加 step_missing 检查（epoch 守卫 + 失败步刷新）
- [x] quality_check_enabled 生效 + scheduler_pool_size 显式化（ThreadPoolExecutor）+ pipeline_enabled 总开关
- [x] 摘要行：history_backfill 每日一行 upsert、startup_backfill 一行（kline_rebuild 原本已逐 tick 落行）
- [x] 测试：重试恢复（attempts=2 无告警）/ 穷尽失败告警 / 手动失败即时告警 / 非交易日跳过 /
      开关关闭跳过 / 别名并步 / UK 幂等（test_pipeline.py 21 用例全绿）

### BP-V2.5-003 Admin 管线视图
- [x] api/admin.py 两端点（daily 内嵌 task_log；独立 detail 端点取消，见偏差注记 ①）
- [x] admin.ts 封装 + 类型（PipelineDaily/StepRow/LongTask/SummaryRow）
- [x] PipelinePanel（汇总卡/步骤表带状态圆点/详情 Drawer/近 30 日色带/长任务区/失败步重跑）
- [x] Admin.tsx「管线」页签；30s 自刷 + 重跑后失效重取（复用 react-query 模式）
- [x] npm run build（tsc）通过

### BP-V2.5-004 触发式升级预案存档
- [x] docs/architecture/trigger_upgrades.md：六项 × { 触发条件 / 方案要点 / 验收标准 / 预估规模 }，
      结论以 ROADMAP 阶段四裁剪为准细化；附录 B 当前形态基线（部署/数据量/QPS 参照）
- [x] 附录 A：RDS 云快照确认清单（4 项待运维确认后回填）
- [x] 需求池 §3.3 A01~A05 回链；BP-V2.5-001~004 状态回填

### 上线与收尾
- [x] ruff 全部新文件通过（quality_service 存量 F401/F841 未触碰；admin_service E402 为存量）
- [x] 全量 pytest **455 passed / 0 failed**（test_tencent_stability 网络限速稳定性套件排除——
      与本次改动无关的真实网络节流测试，其两个调度门控用例已单独通过；test_pipeline 21 用例含全量内）
- [ ] 灰度顺序：迁移（已应用）→ 记账钩子 → 三 job 收口 → 重试与告警 → 前端（随服务重启一并生效）
- [ ] 首周每日核对：管线页 vs task_log vs 容器日志三方一致；故意制造一次失败演练告警链路——**运营项**
- [x] Backlog / ROADMAP 状态回填；分逻辑 commit

### 实际偏差注记（2026-09-10 交付时）

1. **API 收敛为两个端点**：`GET /admin/pipeline/daily`（步骤内嵌最近一次 task_log，Drawer 无需二次
   请求）+ `GET /admin/pipeline/summary`；原设计的独立 steps 详情端点取消。
2. **finance 收口用"别名任务"而非转长任务**：新增 admin 任务 `finance_sync_nightly`（不封顶、deadline
   3600s），调度 job id 仍为拓扑键 `finance_sync`，经 `TASK_ALIASES` 写回同一 step 节点——手动限量批
   （300s）与夜间全量两种既有行为原样保留，且不占用单 worker 长任务池（避免与 paper_tick 串行排队）。
3. **23:00 补偿段同样按别名纳管**：`daily_k_sync_retry` 成为正式 admin 任务（可手动触发/有历史），
   其执行写回 `daily_k_sync` step 的 attempts/status，不新增步骤节点。
4. **测试适配发现真实缺陷**：`test_scheduler_retry` 原本 patch `run_daily_sync`，重构后重试核心直调
   `_do_daily_sync` 绕过了 patch——**测试内触发了真实全市场同步**（首次全量跑挂起 35 分钟的根因）。
   已改为 patch `_do_daily_sync` 并在用例中注记。教训：收口类重构必须核对所有既有 patch 点。
5. **MySQL REPEATABLE READ 快照语义**：记账钩子用独立短会话提交；测试断言会话若用只读 `commit()`
   结束事务，新快照不刷新（读到旧值），必须 `rollback()`。已写入测试助手注释。
6. **kline_rebuild 未按原计划加每日摘要**：调研确认其 tick 本就逐次落 task_log 行（带进度），
   维持现状不改；history_backfill / startup_backfill 按计划新增。
7. **daily_k_sync 的失败语义**：仅"全部代码失败"（如源被封）判 failed；部分失败保持 success
   （23:00 补偿重放是设计内路径），失败数进日志行。

## 七、风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 记账钩子拖慢/阻断任务本体 | 钩子自吞异常 + `pipeline_enabled` 一键退回纯 task_log（迁移保留不回滚） |
| 收口改变 job 语义（deadline 杀长跑同步） | deadline 按任务参数化（finance 3600s）；收口独立 commit，灰度首日盯 task_log 耗时分布 |
| 内函数化改造引入回归 | 壳函数语义保留（吞异常）；"失败可抛出"用例锁定；17:30/23:00 两条路径各留测试 |
| 重试风暴（数据源被封时连打） | 重试默认 2 次封顶 + 5 分钟间隔；数据类本有 23:00 补偿与次日全量兜底 |
| DateTrigger 重试跨重启丢失 | 已知边界：次日 08:00 巡检兜底；持久化重试写入 Celery 触发条件（预案） |
| 非交易日误报 step_missing | 巡检以交易日历（`_is_trade_day`）豁免；日历不可用时降级为不告警（宁可漏报不误报） |
| 告警与巡检同日双写 | 均走 UK 幂等 upsert；巡检对已告警步只刷新不新增 |
| weekly run 与 daily run 同日冲突 | UK 含 pipeline_type，两 run 并存互不干扰 |

## 八、与后续阶段的衔接（非代码）

- `sa_pipeline_step` 的状态语义为 Celery 触发式升级预留迁移路径（step 记录结构可直接映射
  任务队列消息）；
- 观察期发现的调度类问题（如 executor 争抢、misfire 频度）回填本文"实际偏差注记"，
  作为阶段五 T5.x 立项输入。
