#!/usr/bin/env bash
# 生产部署：记录回滚点 → 备份（文件卷 + 学校库）→ 拉代码 → 构建 → 启动 → 验证。
#
# 在服务器上从仓库任意目录跑（脚本会自己定位仓库根）：
#   bash ops/deploy.sh                 # 完整流程（先让你确认）
#   bash ops/deploy.sh --yes           # 不确认，直接跑（无人值守）
#   bash ops/deploy.sh --backup-only   # 只备份，不动代码
#   bash ops/deploy.sh --no-backup     # 跳过备份（只在库确实为空时用）
#   bash ops/deploy.sh --dry-run       # 只打印将要做的事
#
# 路径、域名、端口都从仓库根的 .env 读，和 docker compose 用的是同一份：
#   YVRUC_BACKUP_DIR       备份目录；留空时与 compose 的 backup 服务一样，是仓库下 ./backups
#   YVRUC_PUBLIC_BASE_URL  平台网址，取其中的主机名做验证
#   HTTPS_HOST_PORT        nginx 在宿主机上听的 HTTPS 端口，留空为 443
# 和 compose 一样，shell 里同名的环境变量优先于 .env。
#
# 也可以在命令行上用下面的变量直接覆盖（一般不用）：
#   APP_DIR        仓库目录，默认脚本所在目录的上一级
#   BACKUP_DIR     备份目录
#   DOMAIN         验证用的域名
#   HTTPS_PORT     验证用的 HTTPS 端口
#   LOG_DIR        部署日志与回滚点，默认 ~/yvruc-deploy-logs
#   HEALTH_TIMEOUT 等后端健康的上限秒数，默认 120
#   KEEP_DB_DUMPS  数据库备份保留份数，默认 14
#
# 失败时**不自动回滚**：根因不明时把代码和数据库一起动，比原地停下更难收拾。
# 记下回滚点之后，不论成功还是中途失败，退出前都会打印这次的回滚步骤。
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP_DIR=${APP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}
ENV_FILE="$APP_DIR/.env"

# 取 .env 里一项的值。按行解析，不 source：口令里有 $、引号、空格这类字符，
# 交给 shell 解释会被改写甚至执行。规则与 compose 读 .env 一致的部分：
# 同名取最后一行；整值带成对引号时去掉引号；不带引号时「空白 + #」之后是注释。
env_get() {
    local key=$1 line val
    [ -f "$ENV_FILE" ] || return 0
    line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$ENV_FILE" | tail -n 1 || true)
    [ -n "$line" ] || return 0
    val=${line#*=}
    val=${val%$'\r'}
    val=${val#"${val%%[![:space:]]*}"}
    if [[ $val =~ ^\"(.*)\"[[:space:]]*$ ]] || [[ $val =~ ^\'(.*)\'[[:space:]]*$ ]]; then
        val=${BASH_REMATCH[1]}
    else
        val=${val%%[[:space:]]#*}
        val=${val%"${val##*[![:space:]]}"}
    fi
    printf '%s' "$val"
}

# shell 环境变量优先，其次 .env，都没有用默认值（compose 插值的顺序）
setting() {
    local key=$1 default=${2:-} val
    val=${!key:-}
    [ -n "$val" ] || val=$(env_get "$key")
    printf '%s' "${val:-$default}"
}

# 备份目录：compose 把相对路径按仓库根解析，这里也一样，docker -v 要绝对路径
if [ -z "${BACKUP_DIR:-}" ]; then
    BACKUP_DIR=$(setting YVRUC_BACKUP_DIR ./backups)
fi
case "$BACKUP_DIR" in
    /*) ;;
    *) BACKUP_DIR="$APP_DIR/${BACKUP_DIR#./}" ;;
esac
BACKUP_DIR=${BACKUP_DIR%/}

# 域名：从平台网址取主机名（去掉协议、路径、端口）
if [ -z "${DOMAIN:-}" ]; then
    DOMAIN=$(setting YVRUC_PUBLIC_BASE_URL)
    DOMAIN=${DOMAIN#*://}
    DOMAIN=${DOMAIN%%/*}
    DOMAIN=${DOMAIN%%:*}
fi
HTTPS_PORT=${HTTPS_PORT:-$(setting HTTPS_HOST_PORT 443)}

LOG_DIR=${LOG_DIR:-$HOME/yvruc-deploy-logs}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-120}
KEEP_DB_DUMPS=${KEEP_DB_DUMPS:-14}

COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.school-db.yml -f docker-compose.prod.yml)

DRY_RUN=0
DO_BACKUP=1
BACKUP_ONLY=0
ASSUME_YES=0
NOTHING_TO_DO=0
FILE_BACKUP=""
DB_DUMP=""
ROLLBACK_READY=0

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
    if [ "$BACKUP_ONLY" = 0 ] && [ -z "$DOMAIN" ]; then
        die ".env 里的 YVRUC_PUBLIC_BASE_URL 是空的，取不到验证用的域名（也可以用 DOMAIN=… 指定）"
    fi
    case "$HTTPS_PORT" in
        ''|*[!0-9]*) die "HTTPS 端口不是数字：$HTTPS_PORT（看 .env 里的 HTTPS_HOST_PORT）" ;;
    esac
    log "  备份目录 $BACKUP_DIR　域名 ${DOMAIN:-（未配置）}　HTTPS 端口 $HTTPS_PORT"

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
"""把 .env 里的连接串拆成 pg_dump 要的几项；口令单独走 PGPASSWORD，不进命令行。"""
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

backup_files() {
    log "备份文件卷与操作日志（上传件、学习材料、操作日志）"
    if [ "$DRY_RUN" = 1 ]; then log "[dry-run] compose run --rm backup now"; return; fi
    compose run --rm backup now || die "文件卷备份失败"
    FILE_BACKUP=$(in_backup_dir 'ls -1t /b | grep -E "^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}_" | head -1' || true)
    log "文件卷备份：${FILE_BACKUP:-（没找到，去 $BACKUP_DIR 看看）}"
}

backup_db() {
    if [ "${DB_EMPTY:-1}" = "1" ]; then
        warn "YVRUC_DATABASE_URL 未配置，跳过数据库备份"
        return
    fi
    local name="db-$NOW.dump"
    log "备份数据库 → $BACKUP_DIR/$name（库 $DB_NAME）"

    if [ "$DRY_RUN" = 1 ]; then
        log "[dry-run] pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME"
        return
    fi

    # pg_dump 的大版本不能低于服务器：版本不匹配时 dump 出来也还原不了，
    # 与其等到救火时才发现，不如现在就报错。
    local server_major
    # 这一句失败（连不上、账户口令错）时 set -e 会让脚本无声退出，所以单独接住
    server_major=$(psql_remote 'show server_version_num' | cut -c1-2) \
        || die "连不上学校库，取不到版本号（$DB_USER@$DB_HOST:$DB_PORT/$DB_NAME）：核对 .env 里的 YVRUC_DATABASE_URL，以及本机到库的网络"
    log "学校库大版本：${server_major:-未知}（本机 pg_dump 16）"
    if [ "${server_major:-0}" -gt 16 ]; then
        die "学校库是 PostgreSQL $server_major，本机 pg_dump 16 备份不了；把脚本里的镜像换成 postgres:$server_major"
    fi

    docker run --rm -e PGPASSWORD="$DB_PASSWORD" -v "$BACKUP_DIR:/b" postgres:16-alpine \
        pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
                -Fc -f "/b/$name" || die "数据库备份失败"

    # 校验：dump 能被 pg_restore 读出目录才算可用。空文件或半截文件这里就拦住。
    docker run --rm -v "$BACKUP_DIR:/b" postgres:16-alpine \
        pg_restore --list "/b/$name" >/dev/null || die "数据库备份无法读取（$name）"

    DB_DUMP="$name"
    log "数据库备份完成：$name（$(in_backup_dir "du -h /b/$name | cut -f1")，已校验可读）"

    in_backup_dir "ls -1t /b/db-*.dump 2>/dev/null | tail -n +$((KEEP_DB_DUMPS + 1)) | while read -r f; do rm -f \"/b/\$f\"; echo \"  清理旧数据库备份 \$f\"; done"
}

# ---------- 回滚点 ----------

record_rollback() {
    OLD_SHA=$(cd "$APP_DIR" && git rev-parse HEAD)
    OLD_SHA_SHORT=$(cd "$APP_DIR" && git rev-parse --short HEAD)
    OLD_BACKEND_IMAGE=$(docker inspect -f '{{.Image}}' yvruc-backend 2>/dev/null || echo 'n/a')
    OLD_WEB_IMAGE=$(docker inspect -f '{{.Image}}' yvruc-web 2>/dev/null || echo 'n/a')
    OLD_MIGRATION=""
    if [ "$DRY_RUN" = 0 ] && [ "${DB_EMPTY:-1}" = 0 ]; then
        OLD_MIGRATION=$(current_migration)
    fi

    local f="$LOG_DIR/rollback-$NOW.txt"
    {
        echo "部署时间：$NOW"
        echo "回滚点（部署前的代码）：$OLD_SHA"
        echo "数据库迁移版本（部署前）：${OLD_MIGRATION:-未知}"
        echo "backend 镜像：$OLD_BACKEND_IMAGE"
        echo "web 镜像：$OLD_WEB_IMAGE"
        echo "文件卷备份：${FILE_BACKUP:-未做}"
        echo "数据库备份：${DB_DUMP:-未做}"
        echo "数据库：${DB_USER:-}@${DB_HOST:-}:${DB_PORT:-}/${DB_NAME:-}"
        echo
        # 写这份文件时还没部署，不知道库会不会升级，两种情况都写上
        rollback_guide ""
    } > "$f"
    ROLLBACK_READY=1
    log "回滚点：$f"
    log "  代码 ${OLD_SHA_SHORT}　迁移 ${OLD_MIGRATION:-未知}　数据库备份 ${DB_DUMP:-未做}"
}

# 库现在的迁移版本号；查不到时为空
current_migration() {
    psql_remote 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]' || true
}

# 回滚步骤。参数是现在库里的迁移版本：和部署前一样就只切代码；不一样或不知道
# （参数为空）时先处理数据库。
#
# 为什么数据库要先处理：后端启动时要求库版本恰好等于代码里最新的迁移
# （backend/database.py 的 check_schema），而 migrate 服务只会升级。库已经升到
# 新版本时只把代码切回去，migrate 在旧代码里找不到库里的版本号，直接失败，后端
# 起不来。所以要先用**现在这份新代码**把库降回部署前的版本（降级脚本只在新代码
# 里有），或者用部署前的库备份整库恢复，然后再切代码。
rollback_guide() {
    local cur=$1 old=${OLD_MIGRATION:-}
    local dc="docker compose ${COMPOSE_FILES[*]}"
    local code_steps="   cd $APP_DIR
   git checkout ${OLD_SHA:-<部署前的提交>}
   $dc build
   $dc up -d
   # 回滚后下次再部署前，先 git checkout main，不然 git pull 会失败"

    echo "回滚命令（都在 $APP_DIR 下执行）："
    echo
    if [ -n "$cur" ] && [ -n "$old" ] && [ "$cur" = "$old" ]; then
        echo "数据库版本没变（$old），只回滚代码："
        echo "$code_steps"
    else
        if [ -n "$cur" ]; then
            echo "数据库已从 ${old:-（部署前版本未记下）} 升到 $cur，要先回滚数据库，再回滚代码。"
        else
            echo "先查库现在的版本（用现在这份代码查）："
            echo "   $dc run --rm --no-deps --entrypoint alembic migrate -c backend/alembic.ini current"
            echo "等于 ${old:-部署前的版本} 就跳过第 1、2 步，只做第 3 步；不等于就照顺序做。"
        fi
        cat <<EOF

1) 先把现在的库和文件卷再备一份（回滚做错了还能回来）：
   bash ops/deploy.sh --backup-only

2) 回滚数据库，二选一，都必须在切代码之前做：
   a. 降级到部署前的版本（保留部署后新写入的数据；新迁移加的表和列里的数据会丢，
      各迁移的 downgrade 写了丢什么）。降级脚本只在新代码里有，所以趁代码还没切回去：
   $dc stop backend
   $dc run --rm --no-deps --entrypoint alembic migrate -c backend/alembic.ini downgrade ${old:-<部署前的版本号>}
EOF
        if [ -z "$old" ]; then
            echo "      （部署前的版本号没记下，这一条用不了，走 b）"
        fi
        cat <<EOF
   b. 用部署前的库备份整库恢复（部署之后写入的数据全部丢失）：
   $dc stop backend
   docker run --rm -e PGPASSWORD='<口令>' -v $BACKUP_DIR:/b postgres:16-alpine \\
     pg_restore --clean --if-exists -h ${DB_HOST:-<主机>} -p ${DB_PORT:-<端口>} -U ${DB_USER:-<用户>} -d ${DB_NAME:-<库名>} \\
                /b/${DB_DUMP:-<部署前的 dump 文件>}
   （--clean 需要建表与删除权限）

3) 回滚代码：
$code_steps
EOF
    fi
    cat <<EOF

文件卷回滚（只在上传件、学习材料、操作日志也要退回时做，会整卷覆盖）：
   $dc run --rm backup list
   $dc run --rm backup restore ${FILE_BACKUP:-<备份名>} --yes
EOF
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
    log "验证（https://$DOMAIN:$HTTPS_PORT，解析到本机）"
    local base="https://$DOMAIN:$HTTPS_PORT" resolve="$DOMAIN:$HTTPS_PORT:127.0.0.1"
    local err health page tls

    # 先单独探连接和 TLS 握手：这一步不通时，后面的状态码只会是 000，看不出原因。
    # -k 只跳过证书校验，握手本身失败照样报错；证书问题在下面单独看。
    if ! err=$(curl -sS -k -o /dev/null --max-time 15 --resolve "$resolve" "$base/" 2>&1); then
        die "连不上本机 $HTTPS_PORT 端口，或 TLS 握手失败：$err
  先看 nginx：docker logs yvruc-web --tail 50"
    fi

    tls=$(curl -s -k -o /dev/null --max-time 15 -w '%{ssl_verify_result}' --resolve "$resolve" "$base/" || echo 99)
    health=$(curl -s -k -o /dev/null --max-time 15 -w '%{http_code}' --resolve "$resolve" "$base/api/health" || echo 000)
    page=$(curl -s -k -o /dev/null --max-time 15 -w '%{http_code}' --resolve "$resolve" "$base/" || echo 000)
    log "  TLS 证书校验 → $tls　/api/health → $health　首页 → $page"
    [ "$health" = "200" ] || die "健康检查不通过（/api/health 返回 $health），看日志：docker logs yvruc-backend --tail 50"
    [ "$page" = "200" ] || die "首页不通过（返回 $page）"
    if [ "$tls" != "0" ]; then
        warn "证书校验结果不是 0（$tls）：证书链不全或证书与域名 $DOMAIN 不符"
    fi
}

print_rollback() {
    local cur=""
    if [ "$DRY_RUN" = 0 ] && [ "${DB_EMPTY:-1}" = 0 ]; then
        cur=$(current_migration)
    fi
    echo
    echo "================ 出问题时照这里回滚 ================"
    echo "回滚点文件：$LOG_DIR/rollback-$NOW.txt"
    echo "部署日志：  $LOG_FILE"
    echo
    rollback_guide "$cur"
    echo "===================================================="
}

# 中途失败也打印回滚指引。只在记下回滚点之后才有意义，之前什么都还没动。
on_exit() {
    local rc=$?
    if [ "$rc" != 0 ] && [ "$ROLLBACK_READY" = 1 ]; then
        print_rollback
    fi
}

# ---------- 主流程 ----------

main() {
    log "部署开始（$NOW）　仓库 $APP_DIR　日志 $LOG_FILE"
    if [ "$DRY_RUN" = 1 ]; then warn "dry-run：只打印步骤，不改任何东西"; fi

    preflight
    trap on_exit EXIT

    if [ "$ASSUME_YES" = 0 ] && [ "$DRY_RUN" = 0 ]; then
        if [ -t 0 ]; then
            echo
            echo "将要：备份（文件卷 + 学校库）→ git pull → 构建 → 启动 → 验证"
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
        backup_files
        backup_db
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
