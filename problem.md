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


问题7：开干 D（MiMo LLM 客户端封装）+ 真实 LLM 连通性验证
解决方案：
连通性确认（先做）：
- `scripts/check_llm.py`：3 段连通性自检脚本（基础对话 / JSON 模式 / 异步客户端），不打印 API key（mask 显示 prefix...suffix），可任意时间手动跑
- `scripts/probe_models.py`：探测代理服务支持的 model 列表（一次性，已删除）
- 发现用户 .env 配的 base_url 是第三方代理 `mimo2api-trt7.onrender.com/v1`（不是 Novita），可用模型 id 不带 `xiaomimimo/` 前缀
- 修正 `.env` 的 MIMO_MODEL：`xiaomimimo/mimo-v2-flash` → `mimo-v2-flash`（不读 API_KEY，按 safety 规则）
- 复跑 check_llm.py：3/3 全过，基础对话 1714ms / JSON 模式 2226ms / 异步 1341ms

D 阶段实现：
- `app/llm/schemas.py`：TokenUsage（prompt/completion/total tokens + latency_ms + retried + model）+ PingResponse（连通性测试用）
- `app/llm/prompts.py`：JSON_OUTPUT_INSTRUCTION 通用 JSON 输出约束 + with_json_instruction() 拼接工具，E 阶段会在此填业务 prompt
- `app/llm/client.py` MiMoClient 主体（约 280 行）：
  - 构造：从 settings 读默认值；api_key="" 显式被尊重（用于无 key 路径测试，bug 修正：原 fallback 逻辑会从 settings 二次读取）
  - chat()：普通对话，可配 max_tokens / temperature
  - chat_json()：三层 JSON 解析兜底（直接 json.loads → markdown ```json``` 剥壳 → 正则抓 {...}），可选 schema 参数做 Pydantic 校验
  - 重试：tenacity AsyncRetrying，指数退避 1s/2s/4s（max=8s），retry_if_exception_type 仅在 APITimeoutError/APIConnectionError/httpx.TimeoutException/httpx.ConnectError 上重试，max_retries 从 settings 读
  - 异常映射：APITimeoutError → LLMTimeoutError；RateLimitError → LLMRateLimitedError；AuthenticationError/BadRequestError/APIConnectionError/Unknown → LLMError；BadRequest 不重试（业务参数错）
  - 用量记录：prompt_tokens / completion_tokens / total_tokens / latency_ms / model / retried 全部回传到 TokenUsage，便于路由层 ctx.add_tokens() 累加
  - 隐私：日志只记 prompt_hash（sha256 前 12 位）+ prompt_len，**绝不打印 prompt 内容或 API key**；失败时只记 err_type / err_msg[:200]
  - 单例：get_llm_client() / reset_llm_client()（测试用）

测试覆盖：
- `tests/test_llm_client.py`：20 个单元测试全过（不依赖真实 LLM，全部 mock chat.completions.create）
  - 配置：has key / no key / chat without key raises
  - 基础调用：chat 返回 + 用量 + 参数透传 + JSON 模式不开 response_format
  - JSON 解析：clean / markdown 剥壳 / 自由文本兜底 / 无法解析 raise / 空响应 raise / response_format 透传
  - Schema：通过 / 校验失败 raise
  - 异常映射：timeout / rate_limit / auth → 各自异常
  - 重试：可重试异常重试一次后成功 / 重试耗尽 raise / 非可重试异常不重试（BadRequest 仅调一次）
  - 边界：empty content raise

- `tests/test_llm_integration.py`：3 个集成测试（默认 skip，RUN_INTEGRATION=1 启用），用真实 MiMo 验证：
  - test_real_chat：基础对话 + 用量 > 0
  - test_real_chat_json：JSON 模式抽取
  - test_real_chat_json_with_schema：Pydantic schema 校验通过（修一次：原 prompt 没显式指定字段名，模型用了中文 key 导致 schema fail；改 prompt 显式说"字段名必须是英文 name 和 phone"后稳定通过）

收尾：
- pytest 单元 50/50 全过（含 D 新增 20 个，加上 C 阶段 30 个）
- ruff format（3 文件 reformat）+ check 全过
- features.json 不动：LLM 客户端是基础设施层，不直接对应业务 feature；E/F 阶段升级 f-extract-* / f-match-score 时会通过 step.refs 引用 app/llm/client.py
- 删除一次性脚本 scripts/probe_models.py；保留 scripts/check_llm.py 作为部署后随时可跑的连通性自检

修改的代码文件：
- `app/llm/{client,schemas,prompts}.py`（新建）
- `tests/test_llm_client.py`、`tests/test_llm_integration.py`（新建）
- `scripts/check_llm.py`（新建）
- `.env`（用户配置，已被 .gitignore，本次未读取 API_KEY 内容，仅替换 MIMO_MODEL 行）
- `problem.md`（追加本条记录）
应当达成的效果：E/F 阶段写抽取与评分时直接 `from app.llm.client import get_llm_client; client.chat_json(system=..., user=..., schema=ResumeBasicSchema)` 即可拿到解析后的 Pydantic 实例 + 用量数据，所有重试/超时/降级/隐私已在客户端内置；调用方零关心底层细节。集成测试证明真实 MiMo 在 1-2s 延迟下稳定可用，JSON 模式 + schema 路径可工作（前提：业务 prompt 显式声明字段名）。


问题8：开干 E（信息抽取 E1+E2+E3+E4 全做）
解决方案：四个子任务一并实现，复用同一抽取模式（系统提示 + JSON schema + 缓存 + 降级）。
- `app/models/resume.py` 补全：ResumeBasic / ResumeJobIntent / Education / WorkExperience / Project / ResumeBackground / ResumeAggregate
- `app/llm/prompts.py`：三段业务 prompt（EXTRACT_BASIC_SYSTEM / EXTRACT_JOB_INTENT_SYSTEM / EXTRACT_BACKGROUND_SYSTEM）
  - 每段 system 显式给出 JSON schema 与字段名（防 D 阶段教训：模型用中文 key）
  - 含「字段缺失时输出 null」「字段名必须英文」「不要编造」三条强约束
  - 学历枚举严格用：博士/硕士/本科/大专/高中/其他
  - 时间格式规范化：YYYY-MM 或 YYYY；present/至今/Now 统一写 "present"
- `app/services/extract_service.py`：
  - `_cached_extract()` 通用流程：缓存 hash → schema 校验 → 缓存命中直接返回 → 未命中调 LLM → 写缓存
  - `extract_basic()`：LLM 失败降级到 `_regex_fallback_basic()`（手机号 / 邮箱 / 标签姓名）
  - `extract_job_intent()`：失败返回空 ResumeJobIntent
  - `extract_background()`：失败返回空 ResumeBackground；成功后用 `calc_years_of_experience()` 重算年限覆盖 LLM 结果（LLM 算的常常不准）
  - `_parse_month()` 兼容 YYYY / YYYY-MM / YYYY.MM / YYYY/MM / present / 至今 / Now
  - `calc_years_of_experience()`：累加月数 / 12，重叠不去重，留 1 位小数
  - 文本上限 6000 字 truncate（节省 token + 避免 LLM 上下文超限）
  - background 段 max_tokens 给 2048（教育/工作/项目通常较多）
- `app/api/resume.py` 补 `GET /api/resume/{resume_id}`：
  - resume_id 路径参数 pattern 校验：`^rsm_[a-f0-9]{12}$`
  - 三段抽取 `asyncio.gather(return_exceptions=True)` 并发，任一失败不影响其他
  - 失败的段记入 `extract_errors`，返回空对象（保证响应结构稳定）
  - meta 自动汇总：tokens_used 累加三段 / cache_hit 任一命中即 true
  - OpenAPI 200 example 完整给出（评审 Try it out 友好）

测试覆盖（23 个 extract 单元 + 73 全套全过）：
- 正则兜底 3 个：手机号+邮箱、标签姓名、空文本
- _parse_month 4 个：YYYY-MM / YYYY / present 同义词 / 非法
- calc_years 7 个：单段、多段、present、空、无效日期跳过、空列表、end<start 跳过
- extract_basic 3 个：LLM 成功+缓存命中+失败降级
- extract_job_intent 2 个：LLM 成功+失败兜空
- extract_background 3 个：年限计算覆盖 LLM、无日期保留 LLM 值、失败兜空
- 缓存 key 命名空间隔离（basic/job/background 互不干扰 + 与 CacheKeys 工厂一致）

端到端真实 LLM 验收：
- 上传 → 200, resume_id=rsm_f69a1006b576
- 第一次 GET 触发 LLM：4640ms, 2517 tokens, basic 4/4 命中（Zhang San / 138-0000-0000 / zhangsan@example.com / Haidian District, Beijing），target_role="Backend Engineer"，yoe=5.0（2020-2025 自动算），education 1 条（北大本科），experience 1 条（ACME Corp），projects 5 条（sample 数据问题，不是抽取 bug），extract_errors=[]
- 第二次 GET 命中缓存：4ms, cache_hit=true, tokens_used=0
- 不存在的 resume_id → 404 RESUME_NOT_FOUND（带 suggestion）
- 格式不对（wrong_format）→ 422 VALIDATION_ERROR（pydantic pattern 校验）

踩坑修正：
- 手机号正则原写 `1[3-9]\d{9}`（11 位连续）漏了 `138-0000-0000` 这种连字符格式 → 改 `1[3-9]\d[\s\-]?\d{4}[\s\-]?\d{4}`，复跑全过

codesee sync（增量 patch 模式 A）：
- f-extract-basic：planned → 移除，confidence 0.5 → 0.92，7 个 step refs 全补
- f-extract-job：planned → 移除（保留 v1-bonus），0.3 → 0.88
- f-extract-background：planned → 移除（保留 v1-bonus），0.3 → 0.9
- f-result-json：planned → 移除，0.45 → 0.92，summary 更新为「三段并发抽取后聚合」，触发 detail 改为 `GET /api/resume/{resume_id}`
- 应用 42 op，validate exit 0

修改的代码文件：
- `app/models/resume.py`（补抽取模型）
- `app/llm/prompts.py`（业务 prompt）
- `app/services/extract_service.py`（新建，三段抽取 + 年限计算 + 正则兜底）
- `app/api/resume.py`（GET 接口 + 三段并发）
- `tests/test_extract_service.py`（新建 23 个单测）
- `.codesee/features.json`（patch 应用，4 个 feature 升级到 implemented）
- `.codesee/cache/sync-patch.json`（生成）
- `problem.md`（追加本条记录）
应当达成的效果：题目「模块二：关键信息提取」必选 + 加分项全部实现；GET /api/resume/{id} 端到端首次 4.6s + 缓存命中 4ms；任一段失败不影响其他段；正则兜底保证 LLM 全失败时基本信息（邮箱/手机号）仍可拿到；工作年限自动计算覆盖 LLM 不准的值；为 F 阶段（JD + 匹配评分）提供齐全的 ResumeAggregate 数据源。


问题9：开干 F（JD 关键词 + 启发式评分 + LLM 精准评分 + 融合接口）
解决方案：F1+F2+F3+F4 一并实现，所有题目必选模块至此齐全（11/14 codesee feature 已 implemented）。
- `app/models/match.py`：JDKeywords / JDRequirements / SkillBreakdown / ExperienceBreakdown / EducationBreakdown / ScoreBreakdown / MatchResult / JDKeywordsRequest / MatchRequest（含 score_strategy: fusion/heuristic_only/llm_only Literal）
- `app/llm/prompts.py` 追加：EXTRACT_JD_KEYWORDS_SYSTEM / LLM_SCORE_SYSTEM 业务 prompt
  - JD：提取技能去重 + 同义词归一 + must/nice 二分判定（明确强约束词）
  - 评分：HR 视角 0-100 评分准则（60-69 边缘 / 70-79 基本 / 80-89 推荐 / 90+ 高度）+ 中文 summary/strengths/gaps
- `app/services/jd_service.py`：extract_jd_keywords + get_jd_keywords_by_hash；按 jd_text hash 缓存（前 16 位作 jd_hash）；LLM 失败返回空关键词对象但保 jd_hash 便于上层匹配评分
- `app/services/match_service.py`：
  - 学历归一映射表（高中/大专/本科/硕士/博士 + 等价别名 学士/研究生/Bachelor/PhD）+ _edu_level 数值化 + _highest_edu 取最高
  - 技能归一表（K8s↔Kubernetes / Postgres↔PostgreSQL / Node↔Node.js / Py3↔Python 等 11 条同义词）
  - _is_skill_hit 双路径：归一集合相交 + 简历全文 substring 兜底（处理多词技能如 "machine learning"）
  - _score_skills：must 命中率占 80% + nice 命中率占 20%；无 must 时只看 nice；都无返回中性 50
  - _score_experience：候选 ≥ 要求 = 100；每差 1 年扣 10；无要求 = 70；无候选年限 = 40
  - _score_education：≥ 要求 = 100；低 1 级 = 70；低 2 级 = 40；低 3+ = 20；无要求 = 70；无候选 = 40
  - heuristic_score：按 settings.skill/experience/education_weight 归一加权（0.5/0.3/0.2 默认）
  - llm_score：构造简历摘要（去重避免 token 浪费）+ JSON schema 校验；未配置 / 失败返回 None 自动降级
  - match_resume：fusion 用 0.6/0.4 加权；heuristic_only 跳过 LLM；llm_only 用 LLM 分；LLM 不可用时融合自动 = 启发式
  - _heuristic_summary 兜底：LLM 不可用时自动生成 summary/strengths/gaps（含 must_hit/must_miss 解读）
  - 匹配缓存按 (resume_id, jd_hash, flags_hash) 命中
- `app/api/jd.py`：POST /api/jd/keywords + OpenAPI example
- `app/api/match.py`：POST /api/match
  - JD 解析三分支：jd_hash 命中缓存 / jd_hash 未命中但有 jd_text 兜底 / 仅传 jd_text
  - 无 jd_text 又开 LLM 评分 → 自动降级 heuristic_only（避免 LLM 没原文可看）
  - 命中 (resume_id, jd_hash, flags) 评分缓存直接返回
  - 三段抽取并发 → 构造 ResumeAggregate → 调 match_resume → 写缓存
  - 缺参数 raise MissingParameterError → 全局 handler 包装为 400
- `app/main.py`：注册 jd / match 路由（4 条业务路由全在）

测试覆盖（25 个 match 单元 + 98 全套全过）：
- 学历归一 4 个：变体识别 / 等级排序 / 取最高 / 空列表
- 技能归一 1 个：6 种别名归一
- 技能评分 4 个：全命中 80（无 nice）/ 部分命中 73 / 无 JD 关键词中性 50 / K8s 同义词命中
- 经验评分 5 个：满足 100 / 差 1 年 90 / 差 3 年 70 / 无要求 70 / 无候选 40
- 学历评分 5 个：相等 100 / 高于 100 / 低 1 级 70 / 低 2 级 40 / 无候选 40
- 启发式综合 2 个：边界检查 + 完美场景
- 融合策略 4 个：fusion 组合 / heuristic_only 跳 LLM / LLM 失败降级 / use_llm_score=False 跳 LLM

端到端真实 LLM 验收（用 5 年北大 CS 本科 vs Python 后端 JD）：
- 上传 cache_hit ✓（同 PDF 复用）
- JD 关键词提取 1917ms / 615 tokens：抽出 7 个 skills（Python/FastAPI/Django/MySQL/K8s/Redis/消息队列），4 个 must_have，3 个 nice_to_have，min_years=3，education=本科 ✓
- JD 二次提取 5ms cache_hit=true ✓
- match fusion 4921ms / 3254 tokens：final=82（heuristic 86 与 LLM 75 按 0.6:0.4 融合 → 81.6 → 82 ✓ 数学正确）
  - skill_match 73：必备 3/4（Python/FastAPI/MySQL）miss Django，nice 2/3（K8s/Redis）
  - experience 100（5 ≥ 3）
  - education 100（本科 = 本科）
  - LLM strengths 5 条 + gaps 5 条（中文，HR 视角）
- match 二次 5ms cache_hit=true（评分缓存命中，零 LLM 成本）✓
- match heuristic_only 6ms：跳过 LLM，final=86=heuristic_total，summary 兜底 "高度匹配（必备命中 3/4）"
- 缺 jd_text 与 jd_hash → 400 MISSING_PARAMETER ✓

收尾：
- 4 ruff lint 错误自动修（unused import + import sort），全过
- pytest 单元 98/98（含 F 新增 25），集成 3/3（D 阶段已验）
- features.json：3 个 feature 升级（f-jd-keywords/f-match-score/f-ai-score），共 34 op，validate exit 0
- codesee 整体进度 **11/14 implemented**，剩 3 个 planned：
  - f-frontend-upload / f-frontend-result（deferred，本期不做）
  - f-deploy（H1 阶段做）

修改的代码文件：
- `app/models/match.py`（新建）
- `app/llm/prompts.py`（追加 JD/评分 prompt）
- `app/services/{jd_service,match_service}.py`（新建）
- `app/api/{jd,match}.py`（新建）
- `app/main.py`（注册路由）
- `tests/test_match_service.py`（新建 25 个单测）
- `scripts/codesee_status.py`（新建工具）
- `.codesee/features.json`（patch 应用，3 个 feature 升级）
- `.codesee/cache/sync-patch.json`（生成）
- `problem.md`（追加本条记录）
应当达成的效果：题目「模块三：简历评分与匹配」必选 + 加分项（LLM 精准评分）全部实现；启发式（73.3% 用 Python 通用决策树）+ LLM（语义级 HR 评估）双层评分按权重融合；缓存层全链路命中（PDF 解析 / 信息抽取三段 / JD 关键词 / 匹配评分）；后续 G/H 阶段可专注于测试收口和部署。
