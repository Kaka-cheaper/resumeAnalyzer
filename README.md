# 智能简历分析系统

> AI 赋能的招聘助手 — 上传 PDF 简历，自动抽取关键信息并对岗位匹配度进行评分。
>
> 阿里云函数计算 + FastAPI + 小米 MiMo · 24h 笔试题答卷

## 在线访问

```
| 入口      | 地址                                                            |
|-----------|-----------------------------------------------------------------|
| 前端页面  | https://kaka-cheaper.github.io/resumeAnalyzer/                  |
| 后端 API  | https://resume-analyzer-lqemqaynnr.cn-beijing.fcapp.run         |
| 健康检查  | https://resume-analyzer-lqemqaynnr.cn-beijing.fcapp.run/health  |
```

> 注：阿里云 FC 主域名（`fcapp.run`）受平台合规策略限制，浏览器**直接打开**会触发下载行为；前端通过 fetch 调用 API 完全正常（已部署上线验证）。如需在浏览器查看 Swagger UI，本地启动后端访问 `/docs` 即可。

## 项目亮点

```
| 维度         | 落地                                                                              |
|--------------|-----------------------------------------------------------------------------------|
| 必选模块     | 上传/解析/三段抽取/JD 关键词/启发式评分/缓存/前端 全部实现                         |
| 加分项       | LLM 精准评分 · 求职/背景信息抽取 · Redis 抽象 · Docker + FC Custom Container 部署 |
| 工程化       | 9 次 Conventional Commits · Spec 三件套（requirements/design/tasks）· 98 单测全过 |
| LLM 工程化   | 重试 + 超时 + JSON 模式 + 三层 JSON 解析兜底 + 用量记录 + 隐私（不日志 prompt 内容）|
| 性能         | 三段抽取 asyncio.gather 并发 · 全链路缓存 · 重复请求毫秒级响应                     |
```

## 架构

```
┌─────────────────────────────────────┐
│ 前端（GitHub Pages 静态托管）        │
│ 单文件 HTML + Tailwind + 原生 fetch  │
└─────────────────┬───────────────────┘
                  │ HTTPS + CORS
                  ▼
┌─────────────────────────────────────┐
│ 阿里云函数计算 FC（Custom Container）│
│ ┌─────────────────────────────────┐ │
│ │ FastAPI App                     │ │
│ │ ├─ /api/resume/upload  上传解析 │ │
│ │ ├─ /api/resume/{id}    三段抽取 │ │
│ │ ├─ /api/jd/keywords    JD 提词  │ │
│ │ └─ /api/match          匹配评分 │ │
│ ├─────────────────────────────────┤ │
│ │ Cache 抽象（memory / redis）    │ │
│ │ MiMo 客户端（重试/超时/JSON）   │ │
│ └─────────────────────────────────┘ │
└─────────────────┬───────────────────┘
                  │ OpenAI 兼容协议
                  ▼
┌─────────────────────────────────────┐
│ 小米 MiMo（mimo-v2-flash）           │
└─────────────────────────────────────┘
```

详见 `docs/spec/design.md`。

## 技术选型

```
| 组件      | 选型                        | 原因                                              |
|-----------|-----------------------------|---------------------------------------------------|
| 后端框架  | FastAPI 0.115               | 自带 OpenAPI / Pydantic 校验 / async              |
| PDF 解析  | pdfplumber 0.11             | 多页支持好、纯 Python、无 C 扩展头疼问题          |
| LLM       | 小米 MiMo（mimo-v2-flash）   | 256K 上下文够单份简历、推理强、成本低             |
| LLM SDK   | openai 1.51                 | OpenAI 兼容协议，换 base_url 即可                 |
| 重试      | tenacity 9.0                | 指数退避 + 按异常类型筛选可重试                   |
| 缓存      | dict + asyncio.Lock / Redis | Cache 抽象层切换；Redis 是加分项                  |
| 部署      | 阿里云 FC Custom Container  | 自带 Python 3.10 + 完整依赖，规避内置 runtime 限制 |
| 镜像仓库  | 阿里云 ACR 个人版（免费）    | 与 FC 同 region，避免跨地域拉镜像                 |
```

## 接口契约

所有接口返回统一信封：

```json
{
  "code": "OK",
  "message": "success",
  "data": { "..." : "业务数据" },
  "meta": {
    "elapsed_ms": 1234,
    "tokens_used": 567,
    "cache_hit": false,
    "request_id": "req-abc123"
  }
}
```

错误响应额外带 `suggestion` 字段。错误码表见 `docs/spec/design.md` §4.6。

### 主要接口

```
| Method | Path                       | 说明                                  |
|--------|----------------------------|---------------------------------------|
| GET    | /health                    | 服务存活探针                          |
| POST   | /api/resume/upload         | 上传 PDF（multipart）→ 返回 resume_id |
| GET    | /api/resume/{resume_id}    | 三段并发抽取（基本/求职/背景）        |
| POST   | /api/jd/keywords           | 从 JD 提取技能/职责/必备/加分项       |
| POST   | /api/match                 | 简历-JD 匹配评分（启发式 + LLM 融合） |
```

## 评分逻辑

```
启发式综合分 = 0.5 × 技能匹配 + 0.3 × 经验匹配 + 0.2 × 学历匹配
              （权重可在配置中调整）

技能匹配 = 0.8 × must_have 命中率 + 0.2 × nice_to_have 命中率
        （含同义词归一：K8s↔Kubernetes / py3↔python / postgres↔postgresql 等）

经验匹配 = max(0, 100 - |候选年限 - 要求年限| × 10)
        （满足或超出 = 100，每差 1 年扣 10 分）

学历匹配 = 等于 100 / 低 1 级 70 / 低 2 级 40 / 低 3+ 20
        （高中 < 大专 < 本科 < 硕士 < 博士）

最终分 = 0.6 × 启发式 + 0.4 × LLM 语义评分
       （use_llm_score=false 时仅用启发式）
```

## 本地运行

```bash
# 1. 装依赖
pip install -r requirements.txt

# 2. 配 .env
cp .env.example .env
#  编辑 .env 填入 MIMO_API_KEY / MIMO_BASE_URL / MIMO_MODEL

# 3. 启动
uvicorn app.main:app --reload

# 4. 访问
#    Swagger UI: http://localhost:8000/docs
#    健康检查:    http://localhost:8000/health
```

## 部署到阿里云 FC

```bash
# 1. 装 Serverless Devs CLI
npm install -g @serverless-devs/s

# 2. 配 AccessKey
s config add

# 3. Build & Push 镜像（需要先开通阿里云 ACR 个人版并登录）
docker build -t resume-analyzer:local .
docker tag resume-analyzer:local <your-acr-repo>:v0.1.0
docker push <your-acr-repo>:v0.1.0

# 4. 改 deploy/s.yaml 里的 image 字段为你的 ACR 地址

# 5. 部署（PowerShell）
cd deploy
. .\export_env.ps1     # 把 .env 加载到当前 shell
s deploy -y
```

## 项目结构

```
.
├── app/                    # 后端代码
│   ├── api/                #   路由层（health/resume/jd/match）
│   ├── core/               #   配置/异常/日志/响应封装
│   ├── services/           #   业务编排层
│   ├── llm/                #   MiMo 客户端 + prompts
│   ├── cache/              #   Cache 抽象 + memory/redis
│   ├── models/             #   Pydantic 模型
│   ├── utils/              #   text/hash 工具
│   └── main.py             #   FastAPI 实例
├── tests/                  # 单元 + 集成测试
├── frontend/               # GitHub Pages 单文件前端
│   └── index.html
├── deploy/                 # 阿里云 FC 部署
│   ├── s.yaml
│   ├── requirements-prod.txt
│   └── export_env.ps1
├── docs/spec/              # Spec 三件套
│   ├── requirements.md
│   ├── design.md
│   └── tasks.md
├── .github/workflows/      # GitHub Actions（GH Pages 自动部署）
├── scripts/check_llm.py    # LLM 连通性自检
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```

## 测试

```bash
# 单元测试（不依赖外部服务，98 用例）
pytest -q --ignore=tests/test_llm_integration.py

# 集成测试（需要真实 LLM）
RUN_INTEGRATION=1 pytest tests/test_llm_integration.py -v

# Lint + format
ruff check app tests
ruff format app tests
```

## 已知限制

- **Memory 缓存跨 FC 实例不共享**：本期默认 backend 是单实例内存缓存（FC 实例回收即丢）；接 Redis 把环境变量 `CACHE_BACKEND=redis` + `REDIS_URL=...` 改一下即可，应用代码零改动。
- **未绑定备案域名**：FC 主域名（`fcapp.run`）浏览器直接打开会触发下载（阿里云合规策略），通过前端 fetch 调用不受影响。生产场景应绑定已备案的自定义域名。
- **简历样本未入库**：`samples/` 已 gitignore（避免上传真实简历）；本地测试用 `samples/gen_sample.py`（reportlab 生成的英文样本）。

## License

MIT
