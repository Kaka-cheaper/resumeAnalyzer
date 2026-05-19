# 系统设计 · AI 赋能的智能简历分析系统

> 配套：`requirements.md` / `tasks.md`
> 版本：v0.1（规划阶段）

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                       Client（前端，本期不实现）                      │
│                  浏览器 / curl / Swagger UI / 自动化脚本               │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │ HTTPS + JSON
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    阿里云函数计算 FC（Python 3.10）                   │
│                                                                       │
│   ┌──────────────────────── FastAPI App ───────────────────────────┐ │
│   │  Routers: /resume  /jd  /match  /health                        │ │
│   │  ──────────────────────────────────────────────────────────────│ │
│   │  ExceptionHandler   Logging   CORS   ResponseEnvelope          │ │
│   └────────┬───────────────────────────────────────┬───────────────┘ │
│            │                                       │                 │
│   ┌────────▼─────────┐  ┌────────────────┐  ┌─────▼──────────────┐  │
│   │  PDF Service     │  │ Extract Service│  │  Match Service     │  │
│   │  pdfplumber      │  │  (basic/job/bg)│  │  启发式 + LLM 融合  │  │
│   └────────┬─────────┘  └───────┬────────┘  └─────┬──────────────┘  │
│            │                    │                 │                  │
│            │                    ▼                 ▼                  │
│            │           ┌────────────────────────────────────┐       │
│            │           │     LLM Client（MiMo via Novita）   │       │
│            │           │     OpenAI 兼容 / 重试 / 超时 / JSON │       │
│            │           └────────────────────────────────────┘       │
│            │                                                         │
│   ┌────────▼─────────────────────────────────────────────────────┐  │
│   │             Cache Layer（抽象 + memory / redis）              │  │
│   └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
                │                                       │
                ▼                                       ▼
        Novita API（MiMo）                    阿里云 Redis（可选加分）
```

**核心数据流**

```
1. POST /api/resume/upload（PDF 文件）
       → 校验 → 计算文件哈希 → [缓存命中?]
                                  ├─ 命中：直接返回 resume_id + 已缓存的解析结果
                                  └─ 未命中：解析 PDF → 文本清洗 → 落缓存 → 返回 resume_id
2. GET /api/resume/{resume_id}
       → 触发并发抽取（基本/求职/背景）→ 各自查缓存 → 缺失项调 LLM
       → 聚合返回 + meta
3. POST /api/jd/keywords（JD 文本）
       → 文本哈希 → [缓存] → LLM 关键词抽取 → 返回
4. POST /api/match
       body: { resume_id, jd_text 或 jd_keywords }
       → 启发式评分 → 并发调 LLM 精准评分 → 加权融合 → 返回
       → 整体结果按 (resume_id, jd_hash) 缓存
```

---

## 2. 技术选型与理由

```
| 组件         | 选型                       | 理由                                                     |
|--------------|----------------------------|----------------------------------------------------------|
| 后端框架     | FastAPI                    | 自带 OpenAPI / Pydantic 校验 / async / 部署 FC 包小       |
| 运行时       | Python 3.10                | FC 官方支持稳定版本                                      |
| 部署工具     | Serverless Devs (s.yaml)   | 阿里云官方推荐，一条命令部署                             |
| PDF 解析     | pdfplumber                 | 多页支持好、依赖纯 Python、表格抽取准确                  |
| LLM          | 小米 MiMo (mimo-v2-flash)  | 推理强、Agent 能力靠前、按 token 付费、成本低            |
| LLM 网关     | Novita 平台                | 提供 MiMo OpenAI 兼容端点，可直接用 openai SDK            |
| LLM SDK      | openai (>=1.30)            | OpenAI 兼容协议，换 base_url 即可                        |
| 重试策略     | tenacity                   | 行业标准，指数退避                                       |
| 校验/序列化  | Pydantic v2                | 与 FastAPI 原生集成                                      |
| 配置         | pydantic-settings          | `.env` + 环境变量 + 类型校验                             |
| 缓存（默认） | 内存 dict + TTL            | 零依赖，单实例可用                                       |
| 缓存（加分） | 阿里云 Redis（Tair）        | 跨实例共享、持久化                                       |
| 测试         | pytest + httpx             | 异步友好                                                 |
| Lint         | ruff                       | 快速 / 一站式                                            |
```

**为什么不直接调 MiMo HuggingFace 权重**：FC 启动加载 1T 参数模型不现实；走 Novita 云 API 是工程上唯一可行路径。

**为什么不用 LangChain**：本期场景简单（结构化抽取 + 评分），LangChain 抽象成本 > 收益；直接 openai SDK 更可控。

---

## 3. 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py                       # FastAPI 实例 + 中间件 + handler 入口
│   │
│   ├── api/                          # 路由层（薄）
│   │   ├── __init__.py
│   │   ├── resume.py                 # /api/resume/*
│   │   ├── jd.py                     # /api/jd/*
│   │   ├── match.py                  # /api/match
│   │   └── health.py                 # /health
│   │
│   ├── core/                         # 框架基础设施
│   │   ├── config.py                 # Settings（pydantic-settings）
│   │   ├── exceptions.py             # AppException 体系
│   │   ├── handlers.py               # 全局 exception_handler
│   │   ├── logging.py                # 结构化日志（JSON）
│   │   └── response.py               # 统一响应封装
│   │
│   ├── models/                       # Pydantic 模型（请求/响应/领域）
│   │   ├── resume.py                 # ResumeBasic / ResumeJob / ResumeBackground
│   │   ├── match.py                  # MatchResult / ScoreBreakdown
│   │   └── common.py                 # APIResponse / Meta
│   │
│   ├── services/                     # 业务编排层
│   │   ├── pdf_service.py            # 解析 + 清洗
│   │   ├── extract_service.py        # 三段并发抽取
│   │   ├── jd_service.py             # JD 关键词
│   │   ├── match_service.py          # 启发式 + LLM 融合
│   │   └── cache_service.py          # 缓存抽象使用方
│   │
│   ├── llm/                          # LLM 客户端封装
│   │   ├── client.py                 # MiMoClient（基于 openai）
│   │   ├── prompts.py                # 所有提示词集中管理
│   │   └── schemas.py                # LLM 输入/输出 schema
│   │
│   ├── cache/                        # 缓存实现
│   │   ├── base.py                   # Cache 抽象基类
│   │   ├── memory.py                 # MemoryCache（dict + TTL）
│   │   └── redis_impl.py             # RedisCache（加分项）
│   │
│   └── utils/                        # 工具函数
│       ├── hash.py                   # 内容哈希
│       └── text.py                   # 文本清洗 / 截断
│
├── tests/                            # pytest
│   ├── test_pdf.py
│   ├── test_extract.py
│   └── test_match.py
│
├── docs/
│   ├── spec/                         # 本目录
│   │   ├── requirements.md
│   │   ├── design.md
│   │   └── tasks.md
│   ├── api.md                        # 接口约定（前端用）
│   └── architecture.md               # 架构图导出
│
├── deploy/
│   ├── s.yaml                        # Serverless Devs 配置
│   └── README.md                     # 部署指引
│
├── samples/                          # 示例 PDF + JD（不进 git）
│
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
└── README.md
```

**分层原则**

- `api/` 只做请求绑定和响应封装，不写业务
- `services/` 编排业务，不写 LLM 调用细节
- `llm/` 只暴露语义化方法（如 `extract_basic_info`），内部隐藏 prompt + 重试 + JSON 解析
- `cache/` 与业务解耦，调用方用 `Cache` 抽象不依赖具体实现
- `models/` 与 `services` 单向依赖：services 用 models，反之不行

---

## 4. 接口契约（API Contract）

> 所有接口返回统一信封：

```json
{
  "code": "OK",
  "message": "success",
  "data": { ... },
  "meta": {
    "elapsed_ms": 1234,
    "tokens_used": 567,
    "cache_hit": false,
    "request_id": "req-abc123"
  }
}
```

**错误响应**

```json
{
  "code": "PDF_PARSE_FAILED",
  "message": "PDF 似乎是扫描件，未能提取到文本",
  "suggestion": "请上传文字版 PDF",
  "data": null,
  "meta": { "elapsed_ms": 23, "request_id": "req-abc123" }
}
```

### 4.1 健康检查

```
GET /health
→ 200 { "code": "OK", "data": { "status": "alive", "version": "0.1.0" } }
```

### 4.2 简历上传

```
POST /api/resume/upload
Content-Type: multipart/form-data
Body: file=<binary PDF>

→ 200
{
  "code": "OK",
  "data": {
    "resume_id": "rsm_a1b2c3",
    "pages": 2,
    "char_count": 3120,
    "is_scanned_suspect": false
  },
  "meta": { "elapsed_ms": 850, "cache_hit": false }
}

→ 400 INVALID_FILE_TYPE / FILE_TOO_LARGE
→ 422 PDF_PARSE_FAILED
```

### 4.3 简历信息查询（懒抽取）

```
GET /api/resume/{resume_id}

→ 200
{
  "code": "OK",
  "data": {
    "basic": {
      "name": "张三",
      "phone": "13800000000",
      "email": "zhangsan@example.com",
      "address": "北京市海淀区"
    },
    "job_intent": {
      "target_role": "后端工程师",
      "expected_salary": "25-35k"
    },
    "background": {
      "years_of_experience": 5,
      "education": [
        { "degree": "本科", "school": "XX大学", "major": "计算机" }
      ],
      "projects": [
        { "name": "...", "role": "...", "summary": "..." }
      ]
    },
    "raw_text_preview": "前 200 字..."
  },
  "meta": { "elapsed_ms": 3200, "tokens_used": 1820, "cache_hit": false }
}

→ 404 RESUME_NOT_FOUND
```

> 设计选择：抽取是「按需懒加载」，首次 GET 才调 LLM；保证 upload 接口快返回。

### 4.4 JD 关键词提取

```
POST /api/jd/keywords
{ "jd_text": "我们正在招聘后端工程师，要求..." }

→ 200
{
  "code": "OK",
  "data": {
    "jd_hash": "jd_x9y8",
    "skills": ["Python", "FastAPI", "MySQL", "Redis"],
    "responsibilities": ["接口开发", "性能优化"],
    "requirements": {
      "min_years": 3,
      "education": "本科",
      "must_have": ["Python", "MySQL"],
      "nice_to_have": ["Kubernetes"]
    }
  },
  "meta": { "elapsed_ms": 1100, "cache_hit": false }
}
```

### 4.5 匹配评分

```
POST /api/match
{
  "resume_id": "rsm_a1b2c3",
  "jd_text": "我们正在招聘..."        // 二选一
  // "jd_hash": "jd_x9y8"             // 已缓存可直接传 hash
  "use_llm_score": true               // 默认 true，false 则只跑启发式
}

→ 200
{
  "code": "OK",
  "data": {
    "final_score": 82,
    "breakdown": {
      "skill_match": { "score": 85, "hit": ["Python", "MySQL"], "miss": ["K8s"] },
      "experience": { "score": 90, "candidate_years": 5, "required_years": 3 },
      "education":  { "score": 80, "candidate": "本科", "required": "本科" },
      "heuristic_total": 85,
      "llm_score": 78,
      "weights": { "heuristic": 0.6, "llm": 0.4 }
    },
    "summary": "候选人后端经验充足，K8s 经验欠缺...",
    "strengths": ["..."],
    "gaps": ["..."]
  },
  "meta": { "elapsed_ms": 4200, "tokens_used": 2300, "cache_hit": false }
}
```

### 4.6 错误码全集

```
| Code                | HTTP | 含义                          |
|---------------------|------|-------------------------------|
| OK                  | 200  | 成功                          |
| INVALID_FILE_TYPE   | 400  | 非 PDF                        |
| FILE_TOO_LARGE      | 400  | 超过 10MB                     |
| MISSING_PARAMETER   | 400  | 缺少必填参数                  |
| PDF_PARSE_FAILED    | 422  | PDF 损坏 / 扫描件 / 加密       |
| RESUME_NOT_FOUND    | 404  | resume_id 不存在               |
| LLM_TIMEOUT         | 504  | LLM 调用超时（非降级路径）    |
| LLM_RATE_LIMITED    | 429  | LLM 限流                      |
| INTERNAL_ERROR      | 500  | 兜底                          |
```

---

## 5. 数据模型（Pydantic）

```python
class APIResponse(BaseModel, Generic[T]):
    code: str = "OK"
    message: str = "success"
    data: T | None = None
    meta: Meta = Field(default_factory=Meta)

class Meta(BaseModel):
    elapsed_ms: int = 0
    tokens_used: int = 0
    cache_hit: bool = False
    request_id: str

class ResumeBasic(BaseModel):
    name: str | None
    phone: str | None
    email: str | None
    address: str | None

class ResumeJobIntent(BaseModel):
    target_role: str | None
    expected_salary: str | None

class ResumeBackground(BaseModel):
    years_of_experience: int | None
    education: list[Education]
    projects: list[Project]

class JDKeywords(BaseModel):
    skills: list[str]
    responsibilities: list[str]
    requirements: JDRequirements

class MatchResult(BaseModel):
    final_score: int           # 0-100
    breakdown: ScoreBreakdown
    summary: str
    strengths: list[str]
    gaps: list[str]
```

---

## 6. 关键设计决策

### 6.1 PDF 解析策略

**主路径**：`pdfplumber` 逐页抽取 → 合并 → 清洗。

**判定扫描件**：抽出文本字符数 < `len(pages) * 50` 判为疑似扫描件，在响应里给 `is_scanned_suspect=true`，但不阻断流程（让用户决定是否换文件）。

**清洗规则**：

```
1. 去除控制字符（除 \n \t 外的 < 0x20）
2. 多空白合并为单空格
3. 多换行合并为双换行（保段）
4. 中英文混排间空格规范化
5. 截断头尾空行
```

### 6.2 信息抽取策略

**两层降级**：

```
LLM JSON 模式（首选）
    ↓ 失败
LLM 文本模式 + 手动 JSON 解析
    ↓ 失败
正则兜底（仅基本信息：邮箱、手机号、姓名简单模式）
```

**并发**：基本 / 求职 / 背景三段抽取相互独立 → `asyncio.gather` 并发，三段任一失败不影响其他。

**Prompt 设计原则**：

- 输出强制 JSON 格式
- 字段缺失明确返回 `null`
- 给 1-2 个 few-shot 示例
- 限制输入文本长度（截断到 6000 字符）

### 6.3 评分算法

**启发式部分**（confidence 高、可解释）：

```
skill_match_score = (命中关键词数 / 必备关键词数) * 100
                    + (命中加分项数 / 加分项总数) * 20  上限 100

experience_score  = max(0, 100 - |候选年限 - 要求年限| * 10)

education_score   = 学历枚举映射
                    （博士≥硕士≥本科≥大专≥高中）
                    候选 ≥ 要求 → 100；低一级 → 70；低两级 → 40

heuristic_total = 0.5 * skill + 0.3 * experience + 0.2 * education
```

**LLM 评分部分**（语义级）：

```python
prompt = """
你是资深招聘 HR。给定简历结构化信息和岗位 JD，
给出 0-100 的匹配分数，并说明理由和短板。
输出 JSON：
{
  "score": 0-100,
  "summary": "一句话总结",
  "strengths": ["..."],
  "gaps": ["..."]
}
"""
```

**融合**：

```
final = 0.6 * heuristic_total + 0.4 * llm_score
```

权重写在 `core/config.py`，便于调优。

### 6.4 缓存设计

**Cache 抽象**

```python
class Cache(Protocol):
    async def get(self, key: str) -> dict | None: ...
    async def set(self, key: str, value: dict, ttl: int = 3600) -> None: ...
    async def delete(self, key: str) -> None: ...
```

**缓存键设计**

```
| 用途           | Key                                              | TTL    |
|----------------|--------------------------------------------------|--------|
| PDF 解析结果   | pdf:{sha256(file_bytes)}                         | 7 天   |
| 抽取结果       | extract:{sha256(cleaned_text)}:{section}         | 7 天   |
| JD 关键词      | jd:{sha256(jd_text)}                             | 7 天   |
| 匹配评分       | match:{resume_id}:{jd_hash}:{flags_hash}         | 1 天   |
```

**实现切换**

```python
# core/config.py
class Settings(BaseSettings):
    cache_backend: Literal["memory", "redis"] = "memory"
    redis_url: str | None = None

# 在 main.py 启动时根据配置注入
```

**MVP 限制声明**：FC 多实例之间内存缓存不共享。README 必须诚实说明：本期默认 memory，跨实例不共享；接 Redis（环境变量切换）即可解决。

### 6.5 LLM 客户端工程化

```
| 关注点      | 实现                                                              |
|-------------|-------------------------------------------------------------------|
| 重试        | tenacity，指数退避，最多 3 次                                     |
| 超时        | 单次 30s（NFR-02）                                                |
| 限流降级    | 429 → 触发降级（正则兜底/纯启发式评分）                           |
| JSON 输出   | 优先 `response_format={"type":"json_object"}`，不支持时手动解析   |
| 用量记录    | 每次调用记录 prompt_tokens / completion_tokens / latency_ms       |
| 日志        | 不打印 prompt 内容（含简历隐私），只记长度和 hash                  |
| 配置        | base_url / api_key / model 全部从环境变量                          |
```

### 6.6 性能优化

```
| 优化点                  | 收益                                |
|-------------------------|-------------------------------------|
| 三段抽取 asyncio.gather | LLM 调用 3*1.5s → 1.5s              |
| 文本截断（6000 字）     | 减 token 用量 30-50%                |
| 缓存命中率              | 重复请求 0 LLM 成本                  |
| 懒抽取（GET 才触发）    | 上传接口 RT 控制在 1s 内             |
| import 懒加载           | FC 冷启动 -200~500ms                 |
| 模型预热（option）      | FC 预留实例                         |
```

### 6.7 安全与合规

```
| 项                      | 措施                                            |
|-------------------------|-------------------------------------------------|
| API Key                 | 仅环境变量，不进 git，不进日志                  |
| 简历隐私                | 文件不持久化，处理完即删；日志只记 hash         |
| CORS                    | 默认 `allow_origins=["*"]`，便于前端接入         |
| 错误信息                | 不暴露内部栈；统一错误码 + 友好 message          |
| 上传校验                | 扩展名 + MIME + magic bytes 三层校验            |
```

### 6.8 可观测性

每个请求的日志结构：

```json
{
  "ts": "2026-05-19T10:00:00Z",
  "level": "info",
  "request_id": "req-abc",
  "path": "/api/match",
  "method": "POST",
  "elapsed_ms": 4200,
  "tokens_used": 2300,
  "cache_hit": false,
  "status": 200
}
```

LLM 调用单独打点：

```json
{
  "ts": "...",
  "scope": "llm",
  "model": "mimo-v2-flash",
  "prompt_len": 4200,
  "tokens": { "prompt": 1100, "completion": 320 },
  "latency_ms": 1820,
  "retried": 0
}
```

---

## 7. 部署设计

**FC 配置要点**

```
| 项            | 值                                              |
|---------------|-------------------------------------------------|
| 运行时        | python3.10                                      |
| 入口          | app.main.handler （Mangum 适配 ASGI）            |
| 内存          | 512 MB（pdfplumber 够用）                        |
| 超时          | 60s（兜住 LLM + 重试）                           |
| 实例并发      | 默认 1（async 任务可拉到 5）                     |
| 环境变量      | MIMO_API_KEY / MIMO_BASE_URL / CACHE_BACKEND ...|
| 网络          | 公网出网（调 Novita）                            |
```

**ASGI 适配**：FastAPI（ASGI）→ FC 是基于 HTTP 触发的 Web 函数，**直接用 FC 的 Web Function 模式即可**，FastAPI app 通过 uvicorn 在容器内监听端口；无需 Mangum。

**Serverless Devs 配置（s.yaml 骨架）**：

```yaml
edition: 3.0.0
name: resume-analyzer
access: default

resources:
  resume-fc:
    component: fc3
    props:
      region: cn-hangzhou
      function:
        functionName: resume-analyzer
        runtime: python3.10
        handler: index.handler
        timeout: 60
        memorySize: 512
        environmentVariables:
          MIMO_API_KEY: ${env(MIMO_API_KEY)}
          MIMO_BASE_URL: https://api.novita.ai/v3/openai
          MIMO_MODEL: xiaomimimo/mimo-v2-flash
          CACHE_BACKEND: memory
      triggers:
        - triggerName: http
          triggerType: http
          triggerConfig:
            authType: anonymous
            methods: [GET, POST]
```

**部署命令**

```bash
s deploy
```

---

## 8. 风险与应对

```
| 风险                          | 影响               | 应对                                          |
|-------------------------------|--------------------|-----------------------------------------------|
| MiMo / Novita API 不稳定      | LLM 全模块失败     | 重试 + 降级到正则/启发式；告警日志            |
| FC 公网出网受限               | 调不到 Novita      | 部署前在本地用 FC Custom Runtime 验证         |
| FC 冷启动慢                   | 首次响应 >5s       | import 懒加载 + 文档说明；评审时先打一次预热  |
| 包体超 50MB                   | 部署失败           | 用 layer 装 pdfplumber/openai；或自定义运行时  |
| LLM 输出 JSON 不规范          | 抽取失败率高       | JSON 模式 + 手动解析 + 兜底正则               |
| 简历格式千差万别              | 抽取准确率波动     | Prompt 加 few-shot；测试集 5-10 份覆盖典型场景 |
| 24h 时限不够                  | 加分项做不完       | tasks.md 严格按优先级；P0 全过再做 P1         |
```

---

## 9. 与评分维度的对齐

```
| 评分维度        | 占比 | 在本设计中的落点                                   |
|-----------------|------|----------------------------------------------------|
| 功能完整性      | 30%  | §4 接口契约 + §6.1-6.3 解析/抽取/评分流程          |
| 代码质量        | 25%  | §3 项目结构 + §5 模型 + 命名规范                   |
| 工程化实践      | 20%  | §3 分层 + §6.7 安全 + §7 部署 + 错误码体系         |
| 技术深度        | 15%  | §6.5 LLM 工程化 + §6.4 缓存抽象 + §6.6 性能优化     |
| 加分项          | 10%  | §6.3 LLM 评分 + Redis 切换 + Swagger 美化 + meta    |
```

---

## 10. 演进路径

```
v0.1（本期 24h）→ 后端核心 + memory 缓存 + Swagger 演示
v0.2（前端期）  → 单页前端（HTML+原生 JS）+ GH Pages 部署
v0.3            → Redis 缓存 + 批量上传 + SSE 流式评分
v0.4            → 简历库管理 + 历史记录 + 招聘者账号体系
```
