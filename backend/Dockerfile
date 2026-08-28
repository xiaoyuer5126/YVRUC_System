# 后端镜像。三个 stage，构建时必须显式指定 target——
# 不指定会拿到"最后一个 stage"，那是个会静默跑错东西的坑。
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖再拷代码，充分利用层缓存
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY backend/ ./backend/


# 测试镜像：多带一份开发依赖与用例
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY pytest.ini ./
COPY tests/ ./tests/
CMD ["python", "-m", "pytest"]


# 运行镜像：compose 的 backend 与 seed 都用它
FROM base AS runtime
EXPOSE 8000
# --proxy-headers：让 request.base_url 认 X-Forwarded-Proto，否则永远是 http，
# 二维码兜底会给 https 站点拼出 http:// 的网址。只信任内网反代。
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", \
     "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
