# 错误监控服务（GlitchTip）的运维

平台后端没接住的异常报到这里：按「同一个错误」归并，页面上能看堆栈、第一次与最近
一次、出现了多少回。`ERROR` 及以上的日志也成为事件（`logger.exception` 写在哪里，
监控上就看得到哪里）。

上报、脱敏、刷屏通知在平台后端那边（`backend/monitoring.py`），见 README 的「错误
监控」一节。这份文档只管监控服务自己的起停、访问、升级和排障。

## 为什么是单独一份编排

编排是 `ops/docker-compose.monitoring.yml`，不并进平台那套：重启平台不会把监控
一起停，监控挂了也不影响报名。它自带一个 PostgreSQL 容器——生产形态的平台连的是
学校统一数据库，那个账户没有导出权限、建表还要另外对齐，而监控的事件表是纯运维
流水，不该进学校的库。

两边靠网络相连：监控容器挂进平台的 compose 网络，后端按服务名 `glitchtip` 把事件
报进来。所以**先起平台，再起监控**。

## 起服务

在仓库根目录：

```bash
cp ops/monitoring.env.example ops/monitoring.env   # 填两个随机串：库口令与 SECRET_KEY
docker network ls | grep default                   # 确认平台网络名（默认 yvruc_system_default）
alias m='docker compose --env-file ops/monitoring.env -f ops/docker-compose.monitoring.yml'
m up -d
m logs -f glitchtip                                # 迁移自己会跑，起来约半分钟
```

`--env-file` 必须带上：不带的话 compose 读的是仓库根的 `.env`（平台那一份），下面
几个必填变量取不到值，它会直接报错而不是悄悄用空口令起来。

## 打开页面

页面绑在服务器本机的 8001 端口，生产环境再从平台的 443 入口反代到 `/monitor/`。
两条路都能用，反代是日常那条，隧道是反代出问题时的退路。

| 环境 | 打开地址 | 怎么进 |
|---|---|---|
| 生产 | `https://<平台域名>/monitor/` | 浏览器直接开；反代写在 `web/nginx-https.conf.template` |
| 内测、本机 | `http://127.0.0.1:8001` | 先开隧道：`ssh -L 8001:127.0.0.1:8001 <用户>@<服务器>` |

生产反代这几项设置是对应的，改一处要一起看：

- 监控服务设 `BASE_PATH=/monitor`（`ops/monitoring.env` 的 `GLITCHTIP_BASE_PATH`）。
  它转成 Django 的脚本前缀：页面里的链接、静态资源、登录表单都带上这个前缀，静态
  资源也按带前缀的路径登记。
- nginx 那一层把路径**原样**转过去，不剥前缀。先剥掉的话它认不出这些请求，页面
  样式和脚本全丢。
- `GLITCHTIP_DOMAIN` 填 `https://<平台域名>/monitor`，`CSRF_TRUSTED_ORIGINS` 里
  同时留着隧道那条来源。填错了的症状是登录时「表单提交被拒」（CSRF 校验）。

内测环境不设 `GLITCHTIP_BASE_PATH`，按根路径走。

## 第一次进去要做的

1. 打开页面先注册——**自助注册在第一个人注册后自动关闭**（`ENABLE_USER_REGISTRATION=False`
   就是这个语义），所以装好就去注册，别留着给别人注册。
2. 建一个组织和一个项目（项目平台类型选 Python / FastAPI 都可以，它只影响页面上的提示）。
3. 在项目设置里复制 DSN，把主机名换成容器里的地址，**路径前缀跟着环境走**：
   - 生产：`http://<公钥>@glitchtip:8000/monitor/<项目号>`
   - 内测 / 本机：`http://<公钥>@glitchtip:8000/<项目号>`

   主机名必须是 `glitchtip` 而不是 `127.0.0.1`——后端走 compose 网络找它，写回环地址
   只会连到后端自己。sentry-sdk 会把 DSN 路径原样当成接口前缀，所以前缀写在这里，
   不用另外配。
4. 填进仓库根 `.env` 的 `YVRUC_SENTRY_DSN`，`docker compose up -d` 重启后端。
   启动日志里会有一行「错误监控已启用」；DSN 填得不成样子时后端直接拒绝启动。

## 升级与留存

```bash
m pull && m up -d
```

镜像钉的是大版本标签，大版本一年一次、可能有破坏性变更，升级前看一眼
<https://glitchtip.com/blog>。事件默认留 90 天（`GLITCHTIP_RETENTION_DAYS`）。监控
的数据不进平台的备份，丢了就丢了——它是排查线索，不是业务数据。

## 备份

数据在两个卷里：`glitchtip_pgdata`（事件与账号）、`glitchtip_uploads`（上传件）。
要备就 `m exec glitchtip-postgres pg_dump -U glitchtip glitchtip > 备份.sql`。这一步不
在平台的每日备份里，也不会有人提醒你做。

## 排障

| 现象 | 多半是 |
|---|---|
| `/monitor/` 返回 502 | 监控没起，或没挂进平台的网络。`m ps` 看容器，`m logs glitchtip` 看日志 |
| 页面能开、样式和脚本全丢 | `BASE_PATH` 没生效，或 nginx 把那层前缀剥掉了。`m up -d` 重建容器后仍不行，检查 nginx 那段的 `proxy_pass` 有没有改写路径 |
| 登录时「表单提交被拒」 | `GLITCHTIP_DOMAIN` 或 `CSRF_TRUSTED_ORIGINS` 与实际打开的地址不一致 |
| 后端启动就报 DSN 不对 | `YVRUC_SENTRY_DSN` 格式或前缀不对，见上一步的两种写法 |
| 平台整个打不开、web 起不来 | 反代那段的目标地址被写成了字面量主机名。改成变量后 nginx 才不会在启动时解析它 |

平台本身的报名、签到这些功能不受反代影响：反代只是多了一个路径，出问题最多是
这个路径打不开。

## 资源

GlitchTip 约 512MB（一体化模式、不用 Valkey、关掉日志接收与拨测），加上它的库
总共 0.6GB 上下。磁盘按我们的量级几 GB 就够。官方文档：<https://glitchtip.com/documentation/install/>。
