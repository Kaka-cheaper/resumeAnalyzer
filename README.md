<div align="center">

# 智能简历分析系统

**AI 赋能的招聘助手 —— 上传 PDF 简历，自动抽取关键信息并对岗位匹配度进行评分。**

24h 笔试题答卷 · FastAPI + 小米 MiMo + 阿里云函数计算

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#license)
[![Demo](https://img.shields.io/badge/在线演示-▶-brightgreen.svg)](https://kaka-cheaper.github.io/resumeAnalyzer/)
[![Backend](https://img.shields.io/badge/后端-FC%20在线-0EA5E9.svg)](https://resume-analyzer-lqemqaynnr.cn-beijing.fcapp.run/health)
[![Last commit](https://img.shields.io/github/last-commit/Kaka-cheaper/resumeAnalyzer)](https://github.com/Kaka-cheaper/resumeAnalyzer/commits/master)

[![Python 3.10](https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![阿里云 FC](https://img.shields.io/badge/阿里云-FC%20Custom%20Container-FF6A00)](https://www.aliyun.com/product/fc)
[![小米 MiMo](https://img.shields.io/badge/LLM-小米%20MiMo-FF6700)](https://huggingface.co/XiaomiMiMo)

</div>

---

<div align="center">

### ▶ [30 秒试用 · 无需安装](https://kaka-cheaper.github.io/resumeAnalyzer/)

<sub>上传一份 PDF 简历 · 看 AI 抽取信息 · 粘贴 JD 看匹配评分</sub>

</div>

---

## 在线访问

```
| 入口     | 地址                                                            |
|----------|-----------------------------------------------------------------|
| 前端页面 | https://kaka-cheaper.github.io/resumeAnalyzer/                  |
| 后端 API | https://resume-analyzer-lqemqaynnr.cn-beijing.fcapp.run         |
| 健康检查 | https://resume-analyzer-lqemqaynnr.cn-beijing.fcapp.run/health  |
```

> 阿里云 FC 主域名（`fcapp.run`）受平台合规策略限制，浏览器**直接打开**会触发下载行为；前端通过 fetch 调用 API 完全正常（已上线验证）。

---

## 三步搞定

```
1. 上传 PDF 简历       → resume_id（同份简历再传命中缓存，毫秒级）
2. 自动结构化抽取      → 姓名/电话/邮箱/地址 + 求职意向 + 教育/工作/项目 + 工作年限
3. 粘贴岗位 JD         → 启发式 + LLM 双层评分 → 匹配度 / 优势 / 差距
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
│ 小米 MiMo（mimo-v2-flash · 256K 上下文） │
└─────────────────────────────────────┘
```

## 接口契约

所有响应统一信封：`{ code, message, data, meta: { elapsed_ms, tokens_used, cache_hit, request_id } }`

```
| Method | Path                       | 说明                                    |
|--------|----------------------------|----------------------------------------|
| GET    | /health                    | 健康探针                                |
| POST   | /api/resume/upload         | 上传 PDF（multipart）→ 返回 resume_id   |
| GET    | /api/resume/{resume_id}    | 三段并发抽取（基本/求职/背景）          |
| POST   | /api/jd/keywords           | 从 JD 提取技能/职责/必备/加分项         |
| POST   | /api/match                 | 简历-JD 匹配评分（启发式 + LLM 融合）   |
```

## 评分逻辑

```
最终分 = 0.6 × 启发式综合 + 0.4 × LLM 语义评分
       （use_llm_score=false 时仅用启发式）

启发式 = 0.5 × 技能匹配 + 0.3 × 经验年限 + 0.2 × 学历

技能：0.8 × must_have 命中率 + 0.2 × nice 命中率
      （含同义词归一：K8s↔Kubernetes / postgres↔postgresql 等）
经验：满足 = 100，每差 1 年扣 10
学历：等于 100 / 低 1 级 70 / 低 2 级 40 / 低 3+ 20
```

## 本地运行

```bash
pip install -r requirements.txt
cp .env.example .env       # 填 MIMO_API_KEY / MIMO_BASE_URL / MIMO_MODEL
uvicorn app.main:app --reload
# 访问 http://localhost:8000/docs
```

## 部署到阿里云 FC

```bash
docker build -t resume-analyzer:local .
docker tag resume-analyzer:local <your-acr-repo>:v0.1.0
docker push <your-acr-repo>:v0.1.0
# 改 deploy/s.yaml 的 image 字段，然后：
cd deploy && . .\export_env.ps1 && s deploy -y
```

## 项目结构

```
app/        后端代码（api / core / services / llm / cache / models / utils）
tests/      98 单元测试 + 3 集成测试
frontend/   GitHub Pages 单文件前端
deploy/     阿里云 FC 部署（s.yaml + Dockerfile）
docs/spec/  Spec 三件套（requirements / design / tasks）
```

## 已知限制

- Memory 缓存跨 FC 实例不共享（生产建议接 Redis：`CACHE_BACKEND=redis` + `REDIS_URL=...`）
- FC 主域名直接浏览器访问会触发下载（fetch 不受影响；生产应绑备案域名）

## License

MIT
