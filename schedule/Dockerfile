# syntax=docker/dockerfile:1
#
# 志愿者排班工具。独立容器，理由和 bv2008 不一样：
# 那个是怕 Chromium 把内存吃穿，这个是怕**求解器把 CPU 占满**——
# 一次重排是纯计算，最多能跑满 30 秒一个核。放在社区 API 里的话，
# 有人排一次班，全站就卡一下。
#
# 镜像本身很小：求解器零依赖，只有「变成网页服务」的那几个包。没有浏览器。
FROM python:3.13-slim

# 依赖源。默认清华：服务器在教育网里，走 CERNET 直连很快。
# 换源：docker compose build --build-arg APT_MIRROR=mirrors.aliyun.com tool-schedule
ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_DISABLE_PIP_VERSION_CHECK=1

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app/apps/tools/schedule

# Debian 12 起官方镜像改用 deb822 格式的 debian.sources，老格式的 sources.list
# 可能压根不存在，两个都试。
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        if [ -f "$f" ]; then \
            sed -i "s|https\?://deb.debian.org|https://${APT_MIRROR}|g; \
                    s|https\?://security.debian.org|https://${APT_MIRROR}|g" "$f"; \
        fi; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends gcc libpq-dev; \
    rm -rf /var/lib/apt/lists/*

# ——— 依赖层：只拷依赖清单，不拷源码 ———
# 改代码不会让这一层失效。装 -e . 需要包目录存在，先放个空壳，源码随后覆盖上去。
COPY apps/tools/schedule/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p app && touch app/__init__.py && \
    pip install --upgrade pip && \
    pip install -e .

# ——— 源码层：改代码只会重建这里 ———
COPY apps/tools/schedule ./

EXPOSE 9372

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 9372"]
