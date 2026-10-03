#!/usr/bin/env bash
# 抢报压测：N 个学生同一瞬间抢一个 Q 名额 + W 候补的岗位，另有 20 个人一直刷详情页。
# 单进程后端（server.py），量报上/候补/503 的分布、总耗时、后端 CPU 秒、
# 刷页面的延迟、场次锁的等锁与持锁时长。
#
# 在仓库根目录跑（测试镜像 + compose 里的 postgres/redis，内部网络不出网，
# 独立项目名 rushbench，不碰正在跑的那一套）：
#
#   docker compose -p rushbench -f docker-compose.yml -f scripts/rush_bench/compose.bench.yml \
#     --profile test run --rm --build -v "$PWD/scripts/rush_bench:/bench:ro" \
#     -e N=300 -e Q=200 -e W=50 tests bash /bench/run.sh
#
#   用完：docker compose -p rushbench -f docker-compose.yml \
#           -f scripts/rush_bench/compose.bench.yml --profile test down -v
#
# ⚠️ 数字只用来做**同一台机器上的前后对比**：小机器（2 核）上压测客户端、后端、
# 数据库挤在一起抢 CPU，绝对值说明不了线上能扛多少。
#
# 用的是 yvruc_bench 库（每次重建）和 Redis 的 3 号库（每次清空），
# 不碰 tests 用的 yvruc_test / 1 号库。
set -euo pipefail
: "${N:=300}" "${Q:=200}" "${W:=50}"
BENCH=$(cd "$(dirname "$0")" && pwd)
# 口令从 tests 服务的测试库地址里借，换一个库名
export YVRUC_DATABASE_URL=${YVRUC_TEST_DATABASE_URL%/*}/yvruc_bench
export YVRUC_REDIS_URL=${YVRUC_TEST_REDIS_URL%/*}/3
export YVRUC_DEMO_PASSWORD=test-pass
export YVRUC_ID_CARD_KEY=OaIvZ3wSxT9mQ1rKcB7hLdN5fGyU2pVe4XjA6snMtE0=
export YVRUC_PUBLIC_BASE_URL=https://v.ruc.edu.cn

python - <<'PY'
import os
import redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
url = make_url(os.environ["YVRUC_DATABASE_URL"])
e = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with e.connect() as c:
    c.execute(text("DROP DATABASE IF EXISTS yvruc_bench"))
    c.execute(text("CREATE DATABASE yvruc_bench"))
redis.Redis.from_url(os.environ["YVRUC_REDIS_URL"]).flushdb()
PY

cd /app && python "$BENCH/setup.py" "$N" "$Q" "$W" | tail -1
python "$BENCH/server.py" & SP=$!
trap 'kill $SP 2>/dev/null || true' EXIT
for _ in $(seq 50); do
  python -c "import httpx;httpx.get('http://127.0.0.1:8111/api/health')" 2>/dev/null && break
  sleep 0.5
done
python "$BENCH/client.py"
python -c "import os;f=open('/proc/$SP/stat').read().split();print('后端进程 CPU 秒', (int(f[13])+int(f[14]))/os.sysconf('SC_CLK_TCK'))"

python - <<'PY'
import os
from sqlalchemy import create_engine, text
e = create_engine(os.environ["YVRUC_DATABASE_URL"])
q = ("select count(*) from enrollments e join positions p on p.id=e.position_id "
     "where p.name='岗' and e.status=:s")
with e.connect() as c:
    print("库里 enrolled", c.execute(text(q), {"s": "enrolled"}).scalar(),
          "waitlisted", c.execute(text(q), {"s": "waitlisted"}).scalar())
PY
