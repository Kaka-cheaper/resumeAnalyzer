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


问题3：开干 A1（项目骨架与健康检查）
解决方案：按 tasks.md 阶段 A1 落地 FastAPI 项目最小可运行骨架。
- 写 `requirements.txt` 锁死全套依赖版本（fastapi 0.115 / uvicorn 0.30 / pdfplumber 0.11 / openai 1.51 / pydantic 2.9 / pydantic-settings 2.5 / tenacity 9.0 / redis 5.1 / pytest 8.3 / ruff 0.6）
- 写 `pyproject.toml` 配 ruff（line-length=100，开 E/W/F/I/B/C4/UP/N）+ pytest（asyncio_mode=auto）
- 写 `.env.example` 列 16 个环境变量（含 MIMO_* / CACHE_BACKEND / 评分权重 / CORS）
- 写 `.gitignore` 屏蔽 venv、__pycache__、samples、*.pdf、.env、.s/、.codesee/cache/
- 创建 9 个包：app/{api,core,services,models,llm,cache,utils} + tests/，每个含 __init__.py（doc 注释说明分层职责）
- 写 `app/main.py`：FastAPI 实例化（含中文 title/description），CORS 中间件（默认 allow_origins=["*"]，从 CORS_ORIGINS 环境变量解析），注册 health 路由，根路径 `/` 返回演示入口
- 写 `app/api/health.py`：APIRouter 实现 `GET /health`，返回 `{code,message,data:{status,version},meta:{elapsed_ms,request_id}}` 形状（A1 用 dict 兜住，A2 再抽象到 core/response.py）
- 验收：建 venv → 装 fastapi/uvicorn/httpx → 启动 uvicorn 监听 127.0.0.1:8765 → httpx 请求 `/health` 返回 200 + 期望响应 + 根路径 200 + Swagger UI 可达 + OpenAPI schema 已含 /health
修改的代码文件：
- `requirements.txt`、`pyproject.toml`、`.env.example`、`.gitignore`（新建）
- `app/__init__.py`、`app/main.py`（新建）
- `app/api/__init__.py`、`app/api/health.py`（新建）
- `app/{core,services,models,llm,cache,utils}/__init__.py`（新建占位）
- `tests/__init__.py`（新建占位）
- `problem.md`（追加本条记录）
应当达成的效果：项目骨架可运行，FastAPI 启动正常，健康检查返回标准信封，Swagger UI 与 OpenAPI 路径已就绪；后续 A2 阶段可在此基础上替换响应封装、注入异常体系；codesee features.json 暂不更新（骨架属于非业务功能改动，所有 planned feature 都未真正落地实现）。


问题4：开干 A2（基础设施：配置/异常/日志/响应封装）
解决方案：按 tasks.md A2 完整落地框架基础设施，从此 B-F 阶段写业务时不用关心响应格式与错误处理。
- `app/core/config.py`：`Settings(BaseSettings)` + `get_settings()` lru_cache 单例；16 个配置项含校验（数值范围、Literal 枚举、权重一致性 field_validator）；派生属性 `max_upload_size_bytes` / `cors_origins_list` / `is_llm_configured`
- `app/core/exceptions.py`：`AppException` 基类（code / message / http_status / suggestion / details）+ 9 个子类映射 design.md §4.6 错误码全集（InvalidFileType/FileTooLarge/MissingParameter/PDFParseError/ResumeNotFound/LLMTimeout/LLMRateLimited/LLMError）
- `app/models/common.py`：`APIResponse` 通用泛型 + `Meta` 模型；`APIResponse.ok()` / `APIResponse.error()` 工厂方法返回 dict（绕开 FastAPI Generic 序列化边界）
- `app/core/response.py`：`RequestContext` dataclass（request_id/started_at/tokens_used/cache_hit）+ `new_request_context()` / `get_request_context()` / `make_meta()`；提供 `add_tokens()` / `mark_cache_hit()` 让 LLM/缓存层主动上报
- `app/core/logging.py`：`JsonFormatter` 单行 JSON（ts/level/logger/message + extra 透传）；`setup_logging()` 幂等接管 root + uvicorn 日志；不打印 prompt 内容（隐私）
- `app/core/handlers.py`：`request_context_middleware` 注入 ctx + 透传 X-Request-ID + 兜底访问日志；4 个 exception handler（AppException / StarletteHTTPException / RequestValidationError / Exception 兜底）；统一构造 `_build_error_response`
- 改造 `app/main.py`：startup setup_logging → 装 CORS（从 settings 读）→ 注册中间件 → 注册 handlers
- 改造 `app/api/health.py`：用 `APIResponse.ok` + `make_meta(ctx)` 替换 dict 拼装；补 OpenAPI 200 example
- 修 ruff：N818 在 exceptions.py 关掉（基类沿用 FastAPI/Starlette 命名惯例 `*Exception`）；ruff format 全过

验收覆盖：
1. GET /health 返回统一信封 + meta（elapsed_ms/tokens_used/cache_hit/request_id）✓
2. GET /api/notexist 返回 NOT_FOUND 中文 message 而非 starlette 默认 "Not Found"（修了 code_map 优先级 bug）✓
3. 客户端传 X-Request-ID 被透传并写入 meta.request_id 与响应头 ✓
4. CORS preflight OPTIONS /health 返回 200 ✓
5. 服务端日志全部为单行 JSON，含 scope=http/error 分类，request_id 跨日志关联 ✓
6. ruff check + format 全过

修改的代码文件：
- `app/core/{config,exceptions,response,logging,handlers}.py`（新建）
- `app/models/common.py`（新建）
- `app/main.py`、`app/api/health.py`（重写以接入新基础设施）
- `pyproject.toml`（追加 N818 ignore）
- `problem.md`（追加本条记录）
应当达成的效果：B-F 阶段路由层只需 `raise SomeException()` 或 `return APIResponse.ok(data, meta=make_meta(ctx))`，不再手写 dict、不再关心 status code / 日志 / request_id；所有错误统一走 handlers 包装为标准 JSON 信封；FC 部署后日志可直接被阿里云 SLS 索引；token 用量与缓存命中通过 ctx.add_tokens() / ctx.mark_cache_hit() 在调用链中自动累加。


问题5：开干 B（PDF 解析 + 上传接口）
解决方案：B1+B2 合并实现，第一个用户可见的业务闭环跑通。
- `app/utils/text.py`：清洗管线（NFKC 归一化 + 控制字符去除 + 行内空白合并 + 多换行→双换行 + 行首尾空白去除 + 中英空格规范化），`truncate()` 支持头尾保留 + 中间标记
- `app/utils/hash.py`：sha256_bytes / sha256_str / short_hash（12 位 hex 给 resume_id）
- `app/services/pdf_service.py`：pdfplumber 多页抽取，magic bytes 校验，加密/损坏统一抛 PDFParseError，单页异常不中断（warn + 续跑），扫描件阈值 50 字符/页，页数上限 50
- `app/models/resume.py`：ParseResult 领域对象 + UploadResponse 接口响应
- `app/services/resume_store.py`：临时存储（module-level dict + asyncio.Lock + TTL），异步接口，C1 阶段无缝替换为 Cache 抽象
- `app/api/resume.py`：POST /api/resume/upload 三层校验（扩展名 + MIME + magic bytes）+ 大小校验 + 计算 sha256 → 命中复用 resume_id（幂等 + cache_hit=true）→ 解析后落 store → 返回 UploadResponse；OpenAPI 200/400/422 响应示例齐全
- `app/main.py`：注册 resume.router
- `samples/gen_sample.py`：reportlab 生成 3 份测试 PDF（multi-page 简历 / 极简 / 损坏）；samples/ 走 .gitignore
- `tests/test_pdf_service.py`：4 个单测（正常 / 极简 + 扫描件 / 损坏 / 空字节）全过

验收覆盖：
- 单测 4/4 全过
- 端到端：正常上传 200 + 2 页 1430 字符 + resume_id 12 位 ✓
- 同 PDF 重复上传命中缓存（cache_hit=true，resume_id 一致）✓ 幂等性
- 损坏伪 PDF（content 非法）→ 400 INVALID_FILE_TYPE 文件头不是 PDF ✓
- 非 PDF 扩展名 → 400 INVALID_FILE_TYPE 类型不支持 ✓
- 极简 PDF（"hi"）→ 200 + is_scanned_suspect=true ✓
- ruff format + check 全过

踩坑修正：
1. uvicorn 启动时缺 python-multipart → 装上后服务起来；A1 安装策略是「按需装」，B 阶段补上 multipart
2. reportlab 默认字体不支持中文 → sample 改纯英文，避免 text_preview 出现 nnnnn 误导
3. JSON Patch：refs 字段在规划 step 上不存在，必须用 `add` 而非 `replace`，第一次 patch 失败后修正

codesee sync（增量 patch 模式 A）：
- f-upload-pdf：planned → 移除，confidence 0.5 → 0.92，6 个 step + 新增 check-magic step + 2 条 flow，refs 全部补到 app/api/resume.py / resume_store.py / handlers.py
- f-pdf-parse：planned → 移除，confidence 0.45 → 0.9，5 个 step refs 补到 pdf_service.py
- f-text-clean：planned → 移除，confidence 0.4 → 0.9，5 个 step refs 补到 utils/text.py
- 应用 33 个 patch op，validate 通过（exit 0，仅遗留的 error 分支覆盖率警告，规划阶段允许）

修改的代码文件：
- `app/utils/{text,hash}.py`、`app/services/{pdf_service,resume_store}.py`、`app/models/resume.py`、`app/api/resume.py`（新建）
- `app/main.py`（注册新路由）
- `tests/test_pdf_service.py`（新建）
- `samples/gen_sample.py`（新建，gitignore）
- `pyproject.toml`（asyncio_default_fixture_loop_scope）
- `.codesee/features.json`（patch 应用，3 个 feature 升级到 implemented）
- `.codesee/cache/sync-patch.json`（生成）
- `problem.md`（追加本条记录）
应当达成的效果：第一个用户可见的业务闭环跑通——POST /api/resume/upload 接收 PDF → 校验 → 解析 → 清洗 → 落临时 store → 返回 resume_id；幂等保证；扫描件检测；统一信封 + cache_hit + request_id 全链路到位。后续 D（LLM 客户端）+ E（信息抽取）可直接消费 resume_store.get(resume_id) 获取 ParseResult。


问题6：开干 C（缓存抽象 + Memory + Redis 双实现）
解决方案：C1+C2 一并完成，避免 D/E/F 阶段二次重构。所有缓存读写从此走 Cache 协议，调用方零依赖具体实现。
- `app/cache/base.py`：`Cache` Protocol（get/set/delete/exists 全 async，dict in/out）+ `CacheKeys` 工厂集中管理 key 命名（resume/pdf_parse/extract/jd/match）+ `DEFAULT_TTL_SECONDS=86400`
- `app/cache/memory.py`：MemoryCache 协程安全（asyncio.Lock）+ 惰性 TTL 过期 + clear/size 调试方法
- `app/cache/redis_impl.py`：RedisCache 异步实现 + JSON 序列化（dict ↔ str）+ **失败安全**（连接异常/序列化错误统一 warn + 静默降级到未命中路径，不抛业务）
- `app/services/cache_service.py`：工厂 + lru_cache 单例；按 settings.cache_backend 选实现；redis 后端 + 空 URL / 装包失败 → 自动降级 memory（warn 不崩）；redis 客户端 lazy import 避免无 redis 时启动失败
- 重构 `app/services/resume_store.py`：从 module-level dict 升级到 Cache 抽象，对外接口（save/get/exists/clear）零改动；调用方（B 阶段 upload 路由）零修改
- `dev-requirements.txt`：fakeredis + reportlab 分离到开发依赖，不进生产部署

测试覆盖：
- `tests/test_cache.py`：10 个用例（set/get/exists/delete/TTL 过期/非法 TTL/覆写/Protocol 契约/并发写入安全/CacheKeys 命名快照）
- `tests/test_cache_redis.py`：9 个用例（fakeredis 模拟，含连接失败安全降级、JSON 解析失败、TTL）
- `tests/test_cache_service.py`：3 个用例（默认 memory / redis 空 URL 降级 / redis 有 URL 注入 RedisCache）
- `tests/test_resume_store.py`：4 个用例回归（save/get/exists/overwrite），重构后行为不变

验收：
- pytest 全套 30/30 通过 ✓
- 端到端：上传同份 PDF 两次 → cache_hit=true，resume_id 一致（C1 重构无回归）✓
- ruff format + check 全过 ✓

设计要点：
1. **失败安全**是 RedisCache 的核心策略——网络抖动/Redis 宕机时业务降级到未命中路径继续跑，不让加分项变成单点故障
2. **CacheKeys 工厂**集中命名规则，未来改前缀（如 K8s 多租户）一处生效
3. **fakeredis** 让 Redis 契约测试在 Windows / FC 沙箱环境也能跑，不需要真实 Redis 服务
4. **调用方零依赖具体实现**：D/E/F 阶段写抽取/评分时直接 `cache_service.get_cache()` 拿 Cache 对象用即可

codesee sync（增量 patch）：
- f-cache：planned → 移除（保留 v1-bonus 标识加分项），confidence 0.3 → 0.92
- 5 个 step refs 全补：base.py / memory.py / redis_impl.py / cache_service.py
- summary 更新反映双实现
- 应用 11 op，validate exit 0

修改的代码文件：
- `app/cache/{base,memory,redis_impl}.py`（新建）
- `app/services/cache_service.py`（新建）
- `app/services/resume_store.py`（重构，对外接口零改动）
- `tests/test_cache.py`、`tests/test_cache_redis.py`、`tests/test_cache_service.py`（新建）
- `dev-requirements.txt`（新建）
- `.codesee/features.json`（patch 应用，f-cache 升级到 implemented）
- `.codesee/cache/sync-patch.json`（生成）
- `problem.md`（追加本条记录）
应当达成的效果：本期 MVP 默认 memory 后端（零依赖），FC 部署一条命令直接跑；如要跨实例共享或加缓存层，只需改 CACHE_BACKEND=redis + REDIS_URL，应用代码零改动；后续 D/E/F 阶段所有 LLM 抽取与评分结果都可以走同一个 Cache 实例，按 CacheKeys 命名空间区分。
