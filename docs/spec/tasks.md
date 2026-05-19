# 实施任务清单 · AI 赋能的智能简历分析系统

> 配套：`requirements.md` / `design.md`
> 24h 时限内的施工顺序，按优先级排列。
> 每个任务标注：预估耗时 / 优先级 / 验收命令 / 关联需求。
> 完成一个任务即 commit 一次（Conventional Commits）+ 触发 codesee sync。

---

## 优先级图例

```
P0 = 必选模块（功能完整性 30%、代码质量 25%）
P1 = 加分项 / 工程化质量项
P2 = 锦上添花，时间富裕再做
```

---

## 阶段 A：项目骨架（预估 2h，P0）

### A1 · 初始化项目结构与依赖

- [ ] 创建 `app/` 目录及子模块（按 design.md §3）
- [ ] 写 `pyproject.toml` / `requirements.txt`
  - 依赖：fastapi、uvicorn、pdfplumber、openai、pydantic-settings、tenacity、python-multipart、httpx、ruff、pytest
- [ ] 写 `.env.example`（MIMO_API_KEY、MIMO_BASE_URL、MIMO_MODEL、CACHE_BACKEND、LOG_LEVEL）
- [ ] 写 `.gitignore`（venv、`__pycache__`、`.env`、samples、`*.pdf`）
- [ ] `app/main.py` 创建 FastAPI 实例 + CORS 中间件 + 注册健康检查路由
- [ ] `app/api/health.py` 实现 `GET /health`

**验收**

```bash
uvicorn app.main:app --reload
curl http://localhost:8000/health
# 期望：{"code":"OK","data":{"status":"alive","version":"0.1.0"}, "meta":{...}}
```

**关联需求**：FR-FE-01 / FR-FE-02 / NFR-05

**Commit**：`feat: 初始化项目骨架与健康检查`

---

### A2 · 基础设施（配置 / 异常 / 日志 / 响应封装）

- [ ] `app/core/config.py`：`Settings(BaseSettings)`，所有配置从环境变量读
- [ ] `app/core/exceptions.py`：`AppException` 基类 + 子类（按 §4.6 错误码表）
- [ ] `app/core/handlers.py`：FastAPI 全局 exception handler，统一包成 `APIResponse` 错误格式
- [ ] `app/core/response.py`：`APIResponse` 模型 + `Meta` 模型；`response_envelope()` 装饰器或依赖
- [ ] `app/core/logging.py`：结构化 JSON 日志（按 §6.8 字段）
- [ ] `app/main.py`：注册全局 handler、CORS、请求 ID 中间件

**验收**

```bash
curl http://localhost:8000/api/notexist
# 期望：404 + {"code":"INTERNAL_ERROR" or 自定义,"message":"...","meta":{"request_id":"..."}}
```

**关联需求**：FR-RS-01 / FR-RS-02 / NFR-04 / NFR-07

**Commit**：`feat: 异常体系/统一响应/结构化日志/配置加载`

---

## 阶段 B：上传与解析（预估 3h，P0）

### B1 · PDF 解析服务

- [ ] `app/services/pdf_service.py`
  - 函数 `parse_pdf(file_bytes: bytes) -> ParseResult`
  - 用 pdfplumber 逐页抽文本
  - 处理加密/损坏 PDF → 抛 `PDFParseError`
  - 检测扫描件（字符数 < pages * 50）→ `is_scanned_suspect=True`
- [ ] `app/utils/text.py`
  - `clean_text(raw: str) -> str`：按 §6.1 清洗规则
  - `truncate(text: str, max_len: int) -> str`
- [ ] `app/utils/hash.py`：`sha256_bytes` / `sha256_str`

**验收**

```bash
pytest tests/test_pdf.py -v
# 至少 3 个用例：正常 PDF / 多页 PDF / 损坏 PDF
```

**关联需求**：FR-UP-04 / FR-UP-05 / FR-UP-07

**Commit**：`feat(pdf): 多页解析+文本清洗+扫描件检测`

---

### B2 · 上传接口

- [ ] `app/api/resume.py`：`POST /api/resume/upload`
  - multipart 接收 file
  - 校验 MIME + 扩展名 + magic bytes（前 4 字节 `%PDF`）
  - 校验大小 ≤ 10MB
  - 调 `pdf_service.parse_pdf` → 落 memory 缓存（key=文件 hash）
  - 生成 `resume_id` = `rsm_<base32(hash[:8])>`
  - 返回 resume_id + 元信息（pages / char_count / is_scanned_suspect）
- [ ] 与 `cache_service` 集成（写解析结果）
- [ ] handler 中记录 elapsed_ms 写入 meta

**验收**

```bash
curl -F "file=@samples/sample.pdf" http://localhost:8000/api/resume/upload
# 期望：200 + resume_id

curl -F "file=@README.md" http://localhost:8000/api/resume/upload
# 期望：400 INVALID_FILE_TYPE
```

**关联需求**：FR-UP-01..06 / FR-RS-01

**Commit**：`feat(api): 简历上传接口+多层校验`

---

## 阶段 C：缓存抽象（预估 1.5h，P0+P1）

### C1 · 缓存抽象与内存实现（P0）

- [ ] `app/cache/base.py`：`Cache` Protocol
- [ ] `app/cache/memory.py`：`MemoryCache(dict + TTL)`，线程安全（用 asyncio.Lock）
- [ ] `app/services/cache_service.py`：根据 settings 选择实现，提供模块级单例
- [ ] 在 `app/main.py` 启动时初始化

**验收**

```bash
pytest tests/test_cache.py -v
# 用例：set→get命中 / TTL过期 / delete
```

**关联需求**：FR-RS-06 / NFR-08

**Commit**：`feat(cache): 缓存抽象与内存实现`

### C2 · Redis 实现（P1，时间富裕做）

- [ ] `app/cache/redis_impl.py`：`RedisCache`，使用 `redis.asyncio`
- [ ] `Settings.cache_backend = "redis"` 时自动启用
- [ ] README 写明环境变量与开通流程

**验收**

```bash
CACHE_BACKEND=redis REDIS_URL=redis://localhost:6379 uvicorn app.main:app
# 重复上传同一份 PDF，第二次 cache_hit=true
```

**关联需求**：FR-RS-03..05（加分）

**Commit**：`feat(cache): Redis 实现（加分项）`

---

## 阶段 D：LLM 客户端（预估 1.5h，P0）

### D1 · MiMo 客户端封装

- [ ] `app/llm/client.py`：`MiMoClient`
  - 基于 `openai.AsyncOpenAI`，base_url 指 Novita
  - `async chat_json(system, user, schema: type[BaseModel])` 主方法
  - tenacity 重试（指数退避，最多 3 次）
  - 30s 超时
  - JSON 模式优先；失败时手动解析 + 兜底
  - 记录 prompt_tokens / completion_tokens / latency_ms
  - 不打印 prompt 内容（隐私）
- [ ] `app/llm/prompts.py`：所有 prompt 集中（basic / job / background / jd_keywords / match_score）
- [ ] `app/llm/schemas.py`：LLM 输出的 Pydantic schema

**验收**

```bash
pytest tests/test_llm_client.py -v -m integration
# 跑一次真实调用（需要 MIMO_API_KEY）
```

**关联需求**：FR-EX-04 / FR-SC-05 / NFR-04 / NFR-05

**Commit**：`feat(llm): MiMo 客户端+重试+JSON模式+用量记录`

---

## 阶段 E：信息抽取（预估 2.5h，P0+P1）

### E1 · 基本信息抽取（P0）

- [ ] `app/services/extract_service.py`：`extract_basic(cleaned_text)`
  - 调 LLM JSON 模式
  - 失败 → 正则兜底（邮箱、手机号；姓名不兜底）
- [ ] 缓存：key = `extract:basic:{hash}`

**验收**

```bash
pytest tests/test_extract.py::test_basic -v
```

**关联需求**：FR-EX-01 / FR-EX-04 / FR-EX-05

**Commit**：`feat(extract): 基本信息抽取+正则兜底`

### E2 · 求职信息抽取（P1）

- [ ] `extract_job_intent(cleaned_text)`
- [ ] 缓存：key = `extract:job:{hash}`

**关联需求**：FR-EX-02

**Commit**：`feat(extract): 求职信息抽取`

### E3 · 背景信息抽取（P1）

- [ ] `extract_background(cleaned_text)`
- [ ] 含工作年限自动计算（基于经历起止日期）
- [ ] 缓存：key = `extract:background:{hash}`

**关联需求**：FR-EX-03

**Commit**：`feat(extract): 背景信息抽取+年限计算`

### E4 · 简历查询接口

- [ ] `GET /api/resume/{resume_id}`
- [ ] 三段抽取 `asyncio.gather` 并发
- [ ] 任一失败不影响其他段（gather + return_exceptions=True）
- [ ] meta 汇总 elapsed_ms / tokens_used / cache_hit

**验收**

```bash
curl http://localhost:8000/api/resume/rsm_xxxxx
# 期望：basic/job_intent/background 三段齐全；无简历返回 404
```

**关联需求**：FR-EX-01..05 / NFR-01

**Commit**：`feat(api): 简历查询接口（三段并发抽取）`

---

## 阶段 F：JD 与匹配评分（预估 2.5h，P0+P1）

### F1 · JD 关键词提取

- [ ] `app/services/jd_service.py`：`extract_jd_keywords(jd_text)`
- [ ] 缓存：key = `jd:{hash(jd_text)}`
- [ ] `app/api/jd.py`：`POST /api/jd/keywords`

**验收**

```bash
curl -X POST http://localhost:8000/api/jd/keywords \
  -H "Content-Type: application/json" \
  -d '{"jd_text":"招聘后端工程师..."}'
```

**关联需求**：FR-SC-01 / FR-SC-02

**Commit**：`feat(jd): 关键词提取接口`

### F2 · 启发式评分

- [ ] `app/services/match_service.py`：`heuristic_score(resume, jd_keywords)`
  - skill_match：必备命中率 + 加分项命中率
  - experience：年限差线性扣分
  - education：枚举映射
  - 加权综合（权重写 config）
- [ ] 输出 `ScoreBreakdown`

**验收**

```bash
pytest tests/test_match.py::test_heuristic -v
# 用例：全命中 / 部分命中 / 完全不命中 / 年限不足 / 学历低
```

**关联需求**：FR-SC-03 / FR-SC-04

**Commit**：`feat(match): 启发式评分（技能/经验/学历）`

### F3 · LLM 精准评分（P1）

- [ ] `llm_score(resume, jd_text)` 调 LLM 输出 `{score, summary, strengths, gaps}`
- [ ] 失败 → 跳过 LLM 部分，只用启发式分

**关联需求**：FR-SC-05

**Commit**：`feat(match): LLM 语义级评分（加分项）`

### F4 · 匹配接口（融合）

- [ ] `POST /api/match`
- [ ] 启发式 + LLM 并发执行（启发式立即返回，LLM 抢跑 then 融合）
- [ ] `final = 0.6 * heuristic + 0.4 * llm`（权重可配）
- [ ] `use_llm_score=false` 时跳过 LLM
- [ ] 缓存 key = `match:{resume_id}:{jd_hash}:{flags}`

**验收**

```bash
curl -X POST http://localhost:8000/api/match \
  -H "Content-Type: application/json" \
  -d '{"resume_id":"rsm_xxx","jd_text":"...","use_llm_score":true}'
# 期望：final_score + breakdown + summary + strengths + gaps
```

**关联需求**：FR-SC-04 / FR-SC-06

**Commit**：`feat(api): 匹配评分接口（启发式+LLM融合）`

---

## 阶段 G：测试与质量（预估 1.5h，P0）

### G1 · 关键路径测试

- [ ] `tests/test_pdf.py`：解析 / 清洗
- [ ] `tests/test_extract.py`：基本信息正则兜底（不依赖 LLM）
- [ ] `tests/test_match.py`：启发式评分各分支
- [ ] `tests/test_cache.py`：内存缓存 TTL
- [ ] `tests/test_api.py`：上传 / 查询 / 匹配端到端（mock LLM）

**验收**

```bash
pytest -v
# 期望：全部通过；覆盖率不强求，关键路径都覆到
```

**关联需求**：所有 P0

**Commit**：`test: 关键路径单元测试`

### G2 · Lint 与格式

- [ ] `ruff check app tests`
- [ ] `ruff format app tests`
- [ ] 修掉所有警告

**Commit**：`chore: ruff 修复+格式化`

---

## 阶段 H：部署与文档（预估 3h，P0+P1）

### H1 · Serverless Devs 部署

- [ ] `deploy/s.yaml`（按 design.md §7）
- [ ] FC Web Function 模式 + uvicorn 启动
- [ ] 环境变量配置（FC 控制台 / s.yaml）
- [ ] 本地 `s deploy` 一次跑通
- [ ] 公网域名验证（curl 健康检查）

**验收**

```bash
curl https://<your-fc-domain>/health
# 期望：200 + alive
```

**关联需求**：NFR-09

**Commit**：`feat(deploy): 阿里云 FC 部署配置`

### H2 · README

- [ ] 顶部：项目截图 / 在线地址 / Swagger 链接（一眼能跑）
- [ ] 架构图（Mermaid 或 PNG）
- [ ] 技术选型理由（每项一句话）
- [ ] 接口列表（链 Swagger）
- [ ] 本地运行：3 步以内
- [ ] 部署到 FC：1 条命令
- [ ] 评分对齐说明（在哪个文件落了哪个评分点）
- [ ] 限制与已知问题（诚实声明 memory 缓存跨实例不共享等）

**关联需求**：工程化实践 20%

**Commit**：`docs: README + 架构图 + 选型说明`

### H3 · Swagger UI 美化（P1）

- [ ] FastAPI 实例 `title / description / version` 填全
- [ ] 每个路由加 `summary / description / responses`
- [ ] Pydantic 模型加 `Field(..., description=, example=)`
- [ ] 错误响应在 OpenAPI 中显式声明

**验收**

```bash
打开 http://localhost:8000/docs
# 每个接口都能直接 Try it out 跑通
```

**关联需求**：用户体验加分

**Commit**：`docs(swagger): UI 美化+示例补全`

---

## 阶段 I：加分项（时间富裕）

```
| 任务                               | 预估   | 说明                                |
|------------------------------------|--------|-------------------------------------|
| I1 · Redis 缓存接入                 | 1-2h   | 见 C2                               |
| I2 · 简历脱敏返回（mask_pii）       | 0.5h   | 响应里手机号/邮箱打码                |
| I3 · meta 增强（缓存命中详情）       | 0.5h   | 每段抽取分别记缓存命中               |
| I4 · 流式评分（SSE）                 | 1h     | 实时推 LLM 思考过程                  |
| I5 · 批量上传                       | 1h     | POST /api/resume/upload-batch       |
| I6 · OCR 兜底                        | 2h     | 扫描件场景，FC 加 layer              |
```

---

## 阶段 J：前端预留（不实现）

> 题目要求前端必须部署到 GH Pages，但本期 spec 不实现。
> 后端必须保证以下 4 件事，前端接入零返工：

- [x] 接口契约固定（design.md §4）
- [x] CORS 默认放开
- [x] 错误响应规范（code + message + suggestion）
- [x] Swagger UI 可用作临时演示

> 后续单独迭代时，前端只需：
> - 一个上传框（POST /api/resume/upload）
> - 一个 JD 文本框 + 提交按钮（POST /api/match）
> - 一个结果展示区

---

## 时间盘点

```
| 阶段 | 任务         | 预估 | 累计 |
|------|--------------|------|------|
| A    | 骨架         | 2h   | 2h   |
| B    | 上传+解析    | 3h   | 5h   |
| C1   | 缓存内存     | 1h   | 6h   |
| D    | LLM 客户端   | 1.5h | 7.5h |
| E    | 信息抽取     | 2.5h | 10h  |
| F    | JD+匹配评分  | 2.5h | 12.5h|
| G    | 测试+lint    | 1.5h | 14h  |
| H    | 部署+README  | 3h   | 17h  |
| I    | 加分项       | 2-4h | 19-21h|
| 缓冲 | bug + 联调   | 3h   | 22-24h|
```

**关键里程碑**

```
T+5h   上传与解析端到端可用
T+10h  全部抽取功能跑通
T+12h  匹配评分接口可用
T+17h  线上部署完成 + README 写完 → 已可提交
T+24h  加分项 + 缓冲 + 最终演示验证
```

---

## 提交策略

- 每个任务一个 commit，**Conventional Commits**
- 阶段完成可打 tag：`v0.1-skeleton`、`v0.1-mvp`、`v0.1-deploy`
- 完成阶段 H 后即视为「可提交版本」，剩余时间投入加分项
- 每个闭环（A / B / C1 / D / E / F1+F4 / H1）触发一次 codesee sync

---

## 完成定义（Definition of Done）

```
| 项                                              | 必达 |
|-------------------------------------------------|------|
| 所有 P0 任务完成                                | ✓    |
| 线上 FC 接口可访问，curl 健康检查 200            | ✓    |
| Swagger UI 可访问，每个接口能 Try it out         | ✓    |
| README 完整（架构图/选型/部署/限制）             | ✓    |
| 所有提交遵守 Conventional Commits                | ✓    |
| pytest 全过                                     | ✓    |
| ruff 无 error                                   | ✓    |
| 仓库公开 + 提交链接给面试官                      | ✓    |
```
