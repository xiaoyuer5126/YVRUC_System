# 备份容器：postgres 官方镜像自带 pg_dump / pg_restore / psql，加一个 python3 跑调度。
# 主版本要与数据库对齐：pg_dump 比服务器旧就拒绝导出。数据库升级时这里一起改。
FROM postgres:16-alpine

# 默认走国内镜像；海外机器构建时传 --build-arg APK_MIRROR=https://dl-cdn.alpinelinux.org/alpine，
# 留空也用官方源
ARG APK_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/alpine
RUN if [ -n "$APK_MIRROR" ]; then \
        sed -i -E "s#https?://dl-cdn\.alpinelinux\.org/alpine#${APK_MIRROR%/}#" /etc/apk/repositories; \
    fi \
    && apk add --no-cache python3 tzdata

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app
COPY backup/ ./backup/

ENTRYPOINT ["python3", "-m", "backup.job"]
CMD ["run"]
