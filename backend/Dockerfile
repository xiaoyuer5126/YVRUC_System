# 后端镜像。三个 stage，构建时必须显式指定 target——
# 不指定会拿到"最后一个 stage"，那是个会静默跑错东西的坑。
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 默认走国内镜像；海外机器（CI）构建时传 --build-arg PIP_INDEX=https://pypi.org/simple
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# 先装依赖再拷代码，充分利用层缓存
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
        -i $PIP_INDEX

COPY backend/ ./backend/


# 测试镜像：多带一份开发依赖与用例
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt \
        -i $PIP_INDEX
COPY pytest.ini ./
COPY tests/ ./tests/
# 备份容器的保留策略也在这套用例里测
COPY backup/ ./backup/
CMD ["python", "-m", "pytest"]


# 运行镜像：compose 的 backend 与 seed 都用它
FROM base AS runtime
EXPOSE 8000
# --proxy-headers：让 request.base_url 认 X-Forwarded-Proto，否则永远是 http，
# 二维码兜底会给 https 站点拼出 http:// 的网址。只信任内网反代。
#
# --workers：一个进程只吃一个核（压测结论见 tests/perf_probe.py），多开几个
# 才是加并发。进程数写进环境变量是为了不改镜像就能调：compose 从 .env 的
# YVRUC_WORKERS 传进来，没给就按 4（本机 8 核，另留一半给数据库与反代）。
# 定时任务因此变成每个进程各一份，靠 Redis 选主只留一个——见 locks.py。
#
# 包一层 sh -c 是为了能读环境变量，**exec 不能省**：少了它，docker stop 发的
# SIGTERM 会停在 sh 上，uvicorn 收不到，容器要空等满 10 秒才被强杀。
CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 \
     --port 8000 --proxy-headers --forwarded-allow-ips '*' \
     --workers ${YVRUC_WORKERS:-4}"]
