# 后端镜像：FastAPI + schedule 求解器
# 目录布局与源码一致（/app/schedule、/app/platform），
# config.SCHEDULE_DIR = platform.parent/"schedule" 在容器内同样成立。
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app/platform

# 先装依赖再拷代码，充分利用层缓存
COPY platform/backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY schedule/ /app/schedule/
COPY platform/backend/ ./backend/

# 回归测试用（docker compose --profile test run --rm tests）
FROM base AS test
COPY platform/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY platform/pytest.ini ./
COPY platform/tests/ ./tests/
CMD ["python", "-m", "pytest", "-q"]

FROM base AS runtime
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
