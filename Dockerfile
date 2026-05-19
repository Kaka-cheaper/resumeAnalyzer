# 智能简历分析系统 - 阿里云 FC Custom Container 镜像
# 基础镜像：Python 3.10 slim（约 130MB）
FROM python:3.10-slim

# 时区设置为北京
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /code

# 装系统依赖（pdfplumber 间接依赖 pdfminer 用到 cffi/cryptography 的 C 库）
# slim 镜像默认很裸，需要补 ca-certificates 用于 https
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先 copy requirements 利用 Docker 层缓存（依赖不变就不重装）
COPY deploy/requirements-prod.txt /code/requirements-prod.txt
RUN pip install --no-cache-dir -r /code/requirements-prod.txt

# 再 copy 代码
COPY app /code/app

# 默认监听端口（FC Custom Container 标准 9000）
ENV PORT=9000
EXPOSE 9000

# 阿里云 FC Custom Container 不要求特殊 entrypoint，监听 PORT 即可
# 用 uvicorn 启动 FastAPI；workers=1（FC 实例并发由平台层处理）
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
