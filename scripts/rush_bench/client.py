"""压测客户端：N 个学生同一瞬间抢同一个岗位，另有 20 个人每 0.2 秒刷一次活动详情页。

读 setup.py 写下的 /tmp/b.json，打到 server.py 起的 127.0.0.1:8111。
"""
import json, time, threading
from concurrent.futures import ThreadPoolExecutor
import httpx
cfg = json.load(open("/tmp/b.json")); B = "http://127.0.0.1:8111"
c = httpx.Client(timeout=60)
tok = {}
for s in cfg["sids"]:
    r = c.post(B + "/api/auth/demo-login", json={"student_id": s, "password": "test-pass"})
    tok[s] = r.json()["token"]
bar = threading.Barrier(len(cfg["sids"]) + 20)
def go(s):
    cl = httpx.Client(timeout=60)
    bar.wait()
    t = time.perf_counter()
    r = cl.post(f"{B}/api/activities/{cfg['act']}/shifts/{cfg['g']}/enroll",
                json={"position_id": cfg["p"]}, headers={"Authorization": "Bearer " + tok[s]})
    return r.status_code, (r.json().get("status") if r.status_code == 200 else r.text[:20]), time.perf_counter() - t
read_lat = []; read_err = [0]; stop = [False]
def reader(s):
    cl = httpx.Client(timeout=60)
    h = {"Authorization": "Bearer " + tok[s]}
    bar.wait()
    while not stop[0]:
        t = time.perf_counter()
        try:
            r = cl.get(f"{B}/api/activities/{cfg['act']}", headers=h)
            if r.status_code != 200: read_err[0] += 1
        except Exception:
            read_err[0] += 1
        read_lat.append(time.perf_counter() - t)
        time.sleep(0.2)
READERS = cfg["sids"][:20]
rts = [threading.Thread(target=reader, args=(s,), daemon=True) for s in READERS]
for t in rts: t.start()
t0 = time.perf_counter()
with ThreadPoolExecutor(len(cfg["sids"])) as ex:
    res = list(ex.map(go, cfg["sids"]))
wall = time.perf_counter() - t0
stop[0] = True
from collections import Counter
print("结果分布", Counter((r[0], r[1]) for r in res))
ok = sorted(r[2] for r in res if r[0] == 200); bad = sorted(r[2] for r in res if r[0] != 200)
q = lambda a, p: a[min(len(a)-1, int(len(a)*p))] if a else 0
print(f"总耗时 {wall:.2f}s；报上/候补的请求 p50 {q(ok,.5):.2f}s p99 {q(ok,.99):.2f}s 最慢 {ok[-1] if ok else 0:.2f}s")
print(f"被拒请求 p50 {q(bad,.5):.3f}s 最慢 {bad[-1] if bad else 0:.3f}s")
st = c.get(B + "/api/_bench_stats").json()
hold = sorted(x[1]*1000 for x in st); wait = sorted(x[0] for x in st)
print(f"进锁 {len(st)} 次；持锁 p50 {q(hold,.5):.1f}ms p90 {q(hold,.9):.1f}ms 最大 {hold[-1]:.1f}ms；等锁最长 {wait[-1]:.2f}s")

rl = sorted(read_lat)
print(f"同时刷详情页 {len(rl)} 次：p50 {q(rl,.5)*1000:.0f}ms p99 {q(rl,.99)*1000:.0f}ms 最慢 {rl[-1]*1000:.0f}ms 失败 {read_err[0]}")

# 持锁期间按步骤拆开：每一项的中位数与平均（毫秒），以及平均每次持锁执行几次
pf = c.get(B + "/api/_bench_profile").json()
if pf:
    keys = sorted({k for r in pf for k in r if not k.endswith(" ·次数")},
                  key=lambda k: -sum(r.get(k, 0) for r in pf))
    print(f"\n持锁期间各步骤耗时（{len(pf)} 次持锁；有嵌套，各项不要相加）：")
    print(f"{'步骤':52} {'p50':>7} {'平均':>7} {'次/锁':>6}")
    for k in keys:
        vals = sorted(r.get(k, 0) * 1000 for r in pf)
        n = sum(r.get(k + " ·次数", 0) for r in pf) / len(pf)
        print(f"{k[:52]:52} {q(vals,.5):6.1f}ms {sum(vals)/len(vals):6.1f}ms {n:6.1f}")
