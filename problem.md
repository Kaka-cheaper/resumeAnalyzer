# 问题记录

问题1：跑一下 codesee
解决方案：先按 codesee 规则识别项目阶段——`.agents/skills/` 命中 SDD 框架但里面只有 codesee 工具自身、无业务 spec；`题目详情.md` 此时为空 → 走规划模式（scan-planning.md）。在用户补全题目内容（智能简历分析系统 PRD）后，按 5 个业务 Epic + 1 个基础设施 Epic 共 6 个 epic、14 个 feature 产出 `.codesee/features.json`，所有 feature 标 `tags:["planned"]`、confidence ≤ 0.5、refs 留空，cross_feature 用 flow/depends_on/triggers 三类，epic_flow 串起完整用户旅程。最后跑 `node .codesee/scripts/validate-features.mjs` 校验通过（exit 0，仅 1 条 error 分支覆盖率警告，规划阶段可接受）。
修改的代码文件：
- `.codesee/features.json`（新建）
- `problem.md`（新建）
应当达成的效果：项目获得一份初版功能流程图，覆盖题目所有必选模块（上传/解析/抽取/匹配/前端）与加分项（求职信息、背景信息、AI 精准评分、Redis 缓存），后续每写完一个功能可触发 sync.md 把对应 feature 从 planned 升级为 implemented 并补 refs。


问题2：把决策内容按 spec 模式规范写到 doc 目录下的 spec 中，先确定设计与拿分策略，前端先不管，后端预留接口
解决方案：在 `docs/spec/` 下落三份 Kiro 标准 spec 文档：
1. `requirements.md`：拆出 5 个模块的 FR（FR-UP/EX/SC/RS/FE）+ 9 条 NFR；每条 FR 标 P0/P1/P2 优先级；把题目 100 分评分维度（功能 30/代码 25/工程 20/技深 15/加分 10）拆成可验收的清单与对应 FR；前端只保留接口契约 FR-FE-01/02，UI 实现明确移出范围。
2. `design.md`：架构总览图（FastAPI 单体 → MiMo via Novita → Cache 抽象 → 阿里云 FC）；技术选型对比表与理由；项目分层结构（api/core/services/llm/cache/models/utils）；接口契约全集（统一响应信封 + 9 个错误码）；评分算法（启发式权重 0.5/0.3/0.2 + LLM 融合 0.6/0.4）；缓存键设计与 TTL 策略；LLM 工程化（重试/超时/JSON 模式/降级/用量记录）；性能优化与冷启动；阿里云 FC 部署设计（Web Function 模式 + Serverless Devs）；风险应对表；与评分维度的对齐表。
3. `tasks.md`：A-J 十个阶段共 30+ 任务，每任务有预估耗时/优先级/验收命令/Conventional Commits 提交语；时间盘点累计 17h（保底）+ 4h 加分 + 3h 缓冲共 24h；关键里程碑 T+5/10/12/17/24h；前端预留为阶段 J 不实现，仅声明后端零返工保证（契约固定/CORS/错误响应/Swagger）。
同时按 codesee sync 模式 A 用 RFC 6902 JSON Patch 把决策注入 `.codesee/features.json`：更新 6 个 epic.summary、14 个 feature.summary 与触发详情，前端 2 个 feature 加 `deferred` 标记（保持 planned），共 28 处变更，apply-patch.mjs 应用成功，validate 通过（exit 0）。
修改的代码文件：
- `docs/spec/requirements.md`（新建）
- `docs/spec/design.md`（新建）
- `docs/spec/tasks.md`（新建）
- `.codesee/features.json`（增量 patch）
- `.codesee/cache/sync-patch.json`（新建）
- `problem.md`（追加本条记录）
应当达成的效果：用户在动手写代码前已锁定完整设计与拿分路径——评分对齐已映射到具体 FR 与 task；24h 时间盒已切到任务粒度；前端不实现但接口契约已冻结，后续接前端零返工；codesee 画布同步反映新决策（架构选型、并发策略、缓存抽象等），所有 feature 仍标 planned，等代码落地后 sync 升级为 implemented。
