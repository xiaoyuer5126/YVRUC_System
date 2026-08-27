# 后端镜像：FastAPI + 一键分配
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

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]


# 测试镜像：多带一份开发依赖与用例
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY pytest.ini ./
COPY tests/ ./tests/
CMD ["python", "-m", "pytest"]
