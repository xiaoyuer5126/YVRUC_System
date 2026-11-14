#!/usr/bin/env bash
# 生产部署：记录回滚点 → 备份（备份容器：学校库 + 文件卷）→ 拉代码 → 构建 → 启动 → 验证。
#
# 备份只调备份容器的 `backup now`：库和文件卷一份，导出后校验可读，按它的保留策略
# 轮换（手动备份留最近 10 份，见 README「备份与恢复」）。脚本自己不 pg_dump、不清理。
#
# 在服务器上从仓库任意目录跑（脚本会自己定位仓库根）：
#   bash ops/deploy.sh                 # 完整流程（先让你确认）
#   bash ops/deploy.sh --yes           # 不确认，直接跑（无人值守）
#   bash ops/deploy.sh --backup-only   # 只备份，不动代码
#   bash ops/deploy.sh --no-backup     # 跳过备份（只在库确实为空时用）
#   bash ops/deploy.sh --dry-run       # 只打印将要做的事
#
# 可覆盖的环境变量（一般不用改）：
#   APP_DIR        仓库目录，默认脚本所在目录的上一级（/opt/yvruc）
#   BACKUP_DIR     备份目录，默认 /data/backups（数据盘）
#   LOG_DIR        部署日志与回滚点，默认 ~/yvruc-deploy-logs
#   DOMAIN         平台域名，默认 volunteer.ruc.edu.cn
#   HEALTH_TIMEOUT 等后端健康的上限秒数，默认 120
#
# 失败时**不自动回滚**：根因不明时把代码和数据库一起动，比原地停下更难收拾。
# 脚本退出前会打印这次的回滚命令。
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP_DIR=${APP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}
BACKUP_DIR=${BACKUP_DIR:-/data/backups}
LOG_DIR=${LOG_DIR:-$HOME/yvruc-deploy-logs}
DOMAIN=${DOMAIN:-volunteer.ruc.edu.cn}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-120}

COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.school-db.yml -f docker-compose.prod.yml)

DRY_RUN=0
DO_BACKUP=1
BACKUP_ONLY=0
ASSUME_YES=0
NOTHING_TO_DO=0
BACKUP_NAME=""

for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN=1 ;;
        --no-backup)   DO_BACKUP=0 ;;
        --backup-only) BACKUP_ONLY=1 ;;
        --yes|-y)      ASSUME_YES=1 ;;
        -h|--help)     sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数：$arg（用 --help 看用法）" >&2; exit 2 ;;
    esac
done

NOW=$(date +%F_%H%M%S)
LOG_FILE="$LOG_DIR/deploy-$NOW.log"
mkdir -p "$LOG_DIR"

# 输出同时进日志文件，出事后能翻
exec > >(tee -a "$LOG_FILE") 2>&1

log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '[%s] 注意：%s\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\n[%s] 失败：%s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

compose() { ( cd "$APP_DIR" && docker compose "${COMPOSE_FILES[@]}" "$@" ); }

# 备份目录是 root 750，宿主机上的 ruc 读不了；对它的操作都走一次性容器（容器里是
# root），这样脚本不需要 sudo。命令里的变量在执行时才展开。
in_backup_dir() {
    docker run --rm -v "$BACKUP_DIR:/b" alpine:3.20 sh -c "$*"
}

# ---------- 前置检查 ----------

preflight() {
    log "检查环境"
    [ -d "$APP_DIR" ] || die "找不到仓库目录：$APP_DIR"
    [ -f "$APP_DIR/.env" ] || die "找不到 $APP_DIR/.env"
    [ -f "$APP_DIR/web/nginx-https.conf.template" ] || die "仓库内容不完整（缺少 nginx 生产模板）"
    [ -f "$APP_DIR/.certs/fullchain.pem" ] || die "找不到证书：$APP_DIR/.certs/fullchain.pem"
    [ -f "$APP_DIR/.certs/privkey.pem" ] || die "找不到私钥：$APP_DIR/.certs/privkey.pem"
    docker info >/dev/null 2>&1 || die "docker 不可用（当前用户不在 docker 组？）"

    local dirty
    dirty=$(cd "$APP_DIR" && git status --porcelain --untracked-files=no)
    [ -z "$dirty" ] || die "工作区有未提交的改动，先处理再部署：
$dirty"

    if [ "$DRY_RUN" = 0 ]; then
        docker run --rm -v "$(dirname "$BACKUP_DIR"):/d" alpine:3.20 \
            mkdir -p "/d/$(basename "$BACKUP_DIR")" || die "备份目录不可写：$BACKUP_DIR"
    fi
}

# ---------- 数据库连接信息 ----------

load_db_env() {
    local tmp
    tmp=$(mktemp); chmod 600 "$tmp"
    python3 - "$APP_DIR/.env" "$tmp" <<'PY'
"""把 .env 里的连接串拆成 psql 要的几项（查部署前的迁移版本）；口令单独走 PGPASSWORD，不进命令行。"""
import re, shlex, sys, urllib.parse

env_path, out_path = sys.argv[1], sys.argv[2]
text = open(env_path, encoding="utf-8").read()
m = re.search(r"(?m)^YVRUC_DATABASE_URL=(.*)$", text)
raw = (m.group(1).strip() if m else "")
vals = {"DB_EMPTY": "1"}
if raw:
    u = urllib.parse.urlparse(raw.replace("postgresql+psycopg://", "postgresql://", 1))
    vals = {
        "DB_EMPTY": "0",
        "DB_HOST": u.hostname or "",
        "DB_PORT": str(u.port or 5432),
        "DB_USER": urllib.parse.unquote(u.username or ""),
        "DB_NAME": (u.path or "/").lstrip("/"),
        "DB_PASSWORD": urllib.parse.unquote(u.password or ""),
    }
with open(out_path, "w", encoding="utf-8") as f:
    for k, v in vals.items():
        f.write(f"{k}={shlex.quote(v)}\n")
PY
    # shellcheck disable=SC1090
    . "$tmp"
    rm -f "$tmp"
}

psql_remote() {
    docker run --rm -e PGPASSWORD="$DB_PASSWORD" postgres:16-alpine \
        psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "$1"
}

# ---------- 备份 ----------

run_backup() {
    log "备份（学校库 + 上传件 + 操作日志，走备份容器）"
    if [ "$DRY_RUN" = 1 ]; then log "[dry-run] compose run --rm backup now"; return; fi
    compose run --rm backup now || die "备份失败（看上面备份容器的输出）"
    BACKUP_NAME=$(in_backup_dir 'ls -1 /b | grep -E "^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}_manual$" | sort | tail -1' || true)
    [ -n "$BACKUP_NAME" ] || die "备份容器说成功了，但 $BACKUP_DIR 里找不到这份备份：确认 .env 的 YVRUC_BACKUP_DIR 与 BACKUP_DIR 是同一个目录"
    if in_backup_dir "test -f /b/$BACKUP_NAME/db.dump"; then
        log "备份完成：$BACKUP_NAME（含数据库，已校验可读）"
    else
        warn "备份 $BACKUP_NAME 不含数据库：.env 里 YVRUC_BACKUP_DB 是不是设成了 0？"
    fi
}

# ---------- 回滚点 ----------

record_rollback() {
    OLD_SHA=$(cd "$APP_DIR" && git rev-parse HEAD)
    OLD_SHA_SHORT=$(cd "$APP_DIR" && git rev-parse --short HEAD)
    OLD_BACKEND_IMAGE=$(docker inspect -f '{{.Image}}' yvruc-backend 2>/dev/null || echo 'n/a')
    OLD_WEB_IMAGE=$(docker inspect -f '{{.Image}}' yvruc-web 2>/dev/null || echo 'n/a')
    if [ "$DRY_RUN" = 1 ]; then
        OLD_MIGRATION=$(echo '（dry-run 不查）')
    else
        OLD_MIGRATION=$(psql_remote 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]' || true)
    fi

    local f="$LOG_DIR/rollback-$NOW.txt"
    cat > "$f" <<EOF
部署时间：$NOW
回滚点（部署前的代码）：$OLD_SHA
数据库迁移版本（部署前）：${OLD_MIGRATION:-未知}
backend 镜像：$OLD_BACKEND_IMAGE
web 镜像：$OLD_WEB_IMAGE
部署前备份：${BACKUP_NAME:-未做}（备份容器，库 + 文件卷）
数据库：$DB_USER@$DB_HOST:$DB_PORT/$DB_NAME

回滚命令（在 $APP_DIR 下执行）：
  git checkout $OLD_SHA
  docker compose ${COMPOSE_FILES[*]} build
  docker compose ${COMPOSE_FILES[*]} up -d
EOF
    log "回滚点：$f"
    log "  代码 ${OLD_SHA_SHORT}　迁移 ${OLD_MIGRATION:-未知}　备份 ${BACKUP_NAME:-未做}"
}

# ---------- 部署 ----------

pull_and_report() {
    local before after migs envdiff
    before=$(cd "$APP_DIR" && git rev-parse HEAD)

    log "拉取代码（--ff-only）"
    if [ "$DRY_RUN" = 1 ]; then log "[dry-run] git pull --ff-only origin main"; return; fi
    ( cd "$APP_DIR" && git pull --ff-only origin main ) || die "git pull 失败（本地有分叉或未提交改动）"

    after=$(cd "$APP_DIR" && git rev-parse HEAD)
    if [ "$before" = "$after" ]; then
        log "代码已是最新（${after:0:7}），跳过构建与重启，只做验证"
        NOTHING_TO_DO=1
        return
    fi
    log "新代码：${before:0:7} → ${after:0:7}"

    # 跨了几个迁移、有没有新增环境变量：两样都容易在部署时被忽略
    migs=$(cd "$APP_DIR" && git diff --name-only "$before" "$after" -- backend/migrations/versions | wc -l)
    if [ "$migs" -gt 0 ]; then
        log "  含 $migs 个数据库迁移（启动时会自动升级，回滚要另外处理数据库）"
    else
        log "  没有数据库迁移"
    fi
    envdiff=$(cd "$APP_DIR" && git diff "$before" "$after" -- .env.example | grep -E '^\+[A-Z]' | sed 's/^+//' || true)
    if [ -n "$envdiff" ]; then
        warn "  .env.example 有新增变量，确认 .env 里补齐：
$envdiff"
    fi
}

build_and_up() {
    if [ "$NOTHING_TO_DO" = 1 ]; then return; fi
    log "构建镜像"
    if [ "$DRY_RUN" = 1 ]; then log "[dry-run] compose build"; else compose build || die "构建失败"; fi

    log "启动服务（先跑迁移，再起后端）"
    if [ "$DRY_RUN" = 1 ]; then log "[dry-run] compose up -d"; else compose up -d || die "启动失败"; fi
}

wait_healthy() {
    if [ "$NOTHING_TO_DO" = 1 ] || [ "$DRY_RUN" = 1 ]; then return; fi
    log "等后端健康（最多 ${HEALTH_TIMEOUT}s）"
    local deadline=$((SECONDS + HEALTH_TIMEOUT)) state
    while [ $SECONDS -lt $deadline ]; do
        state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' yvruc-backend 2>/dev/null || echo missing)
        if [ "$state" = "healthy" ]; then
            log "后端健康"
            return
        fi
        sleep 3
    done
    die "后端在 ${HEALTH_TIMEOUT}s 内没有变健康，看日志：docker logs yvruc-backend --tail 50"
}

verify() {
    log "验证"
    local health page tls
    health=$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/health" || echo 000)
    page=$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" || echo 000)
    tls=$(curl -sS -o /dev/null -w '%{ssl_verify_result}' --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" || echo 99)
    log "  /api/health → $health　首页 → $page　TLS 校验 → $tls"
    [ "$health" = "200" ] || die "健康检查不通过（$health）"
    [ "$page" = "200" ] || die "首页不通过（$page）"
    if [ "$tls" != "0" ]; then
        warn "TLS 校验结果不是 0（$tls）：证书链或域名有问题"
    fi
}

print_rollback() {
    cat <<EOF

================ 出问题时照这里回滚 ================
回滚点文件：$LOG_DIR/rollback-$NOW.txt
部署日志：  $LOG_FILE

1) 代码回滚：
   cd $APP_DIR
   git checkout ${OLD_SHA_SHORT:-<部署前的提交>}
   docker compose ${COMPOSE_FILES[*]} build
   docker compose ${COMPOSE_FILES[*]} up -d

2) 数据回滚（学校库 + 上传件 + 操作日志一起回到部署前；部署后的改动全部丢失）。
   要做就放在 1) 的 up -d 之前：库还是新版本时旧代码起不来；新代码 up -d 又会把库升回去。
   docker compose ${COMPOSE_FILES[*]} run --rm backup now      # 先把现在的状态也备一份
   docker compose ${COMPOSE_FILES[*]} stop backend             # 恢复库时不能有别的连接
   docker compose ${COMPOSE_FILES[*]} run --rm backup restore ${BACKUP_NAME:-<备份名>} --yes
   docker compose ${COMPOSE_FILES[*]} up -d
   只恢复其中一样加 --only db 或 --only files；有哪些备份：run --rm backup list
====================================================
EOF
}

# ---------- 主流程 ----------

main() {
    log "部署开始（$NOW）　仓库 $APP_DIR　日志 $LOG_FILE"
    if [ "$DRY_RUN" = 1 ]; then warn "dry-run：只打印步骤，不改任何东西"; fi

    preflight

    if [ "$ASSUME_YES" = 0 ] && [ "$DRY_RUN" = 0 ]; then
        if [ -t 0 ]; then
            echo
            echo "将要：备份（学校库 + 文件卷）→ git pull → 构建 → 启动 → 验证"
            echo "仓库：$APP_DIR"
            read -r -p "继续？[y/N] " ans
            case "${ans:-}" in
                y|Y) ;;
                *) log "已取消"; exit 0 ;;
            esac
        else
            die "当前不是交互终端，请显式加 --yes"
        fi
    fi

    load_db_env

    if [ "$DO_BACKUP" = 1 ]; then
        run_backup
    else
        warn "按参数跳过备份"
    fi

    # 回滚点放在备份之后：这样回滚文件里能带上这次备份的名字
    record_rollback

    if [ "$BACKUP_ONLY" = 1 ]; then
        log "只备份，结束"
        exit 0
    fi

    pull_and_report
    build_and_up
    wait_healthy
    verify

    log "完成。回滚点：$LOG_DIR/rollback-$NOW.txt"
    print_rollback
}

main "$@"
