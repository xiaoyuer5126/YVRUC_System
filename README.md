# 人大校青协志愿服务管理平台

FastAPI + SQLAlchemy（PostgreSQL）+ Redis + Vue3 / Element Plus / ECharts（Vite 构建）。
覆盖「活动发布 → 选课式报名 → 候补流转 → 一键分配 → 扫码签到签退 → 时长回传」全链路。

## 一、快速启动

统一用 Docker 跑（后端依赖 PostgreSQL 与 Redis 两个服务，不再支持本机直跑）。

```bash
cp .env.example .env          # 填 POSTGRES_PASSWORD、RUCVA_DEMO_PASSWORD
docker compose up -d --build                    # postgres + redis + backend + web + cloudflared
docker compose --profile seed run --rm seed     # 重置演示数据
docker compose --profile test run --rm tests    # 跑回归测试（独立的 rucva_test 库）
```

- 入口 **http://127.0.0.1:8080**（nginx 托管前端并同源反代 `/api`，无跨域）。
- 数据库 5433 / Redis 6380 / 后端 8000 都只绑 `127.0.0.1`，调试用得上、公网碰不到；
  对外只有 web 的 8080 与 cloudflared 隧道。
- cloudflared 固定域名隧道：token 存 `.env` 的 `CLOUDFLARE_TOKEN`，公网域名在
  Cloudflare Zero Trust 控制台绑定，`docker logs rucva-cloudflared` 看连接状态。
- 上传的活动二维码存命名卷 `rucva_uploads`，数据库存 `rucva_pgdata`。

### 环境变量（.env）

| 变量 | 说明 |
|---|---|
| `POSTGRES_PASSWORD` | 数据库口令，必填；公网部署务必改掉示例值 |
| `RUCVA_DEMO_PASSWORD` | 内测登录共享口令。**留空则整个演示登录接口不存在**；SSO 接通后必须留空 |
| `RUCVA_EXPOSE_DOCS` | 设为 `1` 才暴露 `/docs` 与 `/openapi.json`，默认关闭 |
| `CLOUDFLARE_TOKEN` | 隧道令牌，勿提交/外传 |

### 登录方式

学校统一身份认证（微人大 OAuth2/CAS）是唯一正式入口，参数在「集成设置」页配置。
接通之前，开发小组用 `POST /api/auth/demo-login`（学号 + `RUCVA_DEMO_PASSWORD` 共享口令）
登录；口令连错 10 次锁该来源 IP 5 分钟。会话 24 小时过期，令牌只认 `Authorization` 头，
唯一的例外是 Excel 导出这一个直链下载路由。

## 二、权限体系

用户 = 类型（teacher/student，**二选一**）+ 身份（admin/teacher/qingxie/student，可多个，
顶栏切换视角；路由守卫在视角不够但持有对应身份时自动切换放行）。**老师类型不参与志愿报名**
（自主报名与管理员补录均拦截）。

| 页面 | 可见身份 | 说明 |
|---|---|---|
| 数据总览 dashboard | 仅管理员 | 统计卡片 + ECharts 趋势/分布 |
| 集成设置 integrations | 仅管理员 | 四个对接项在线开关 |
| 用户与信用 users | 管理员/青协 | 调分、限制准入，全程留痕 |
| 审计日志 audit | 工作台三身份 | 非管理员只看到自己管理的活动 |
| 活动管理 manage | 老师/青协/管理员 | 非管理员仅见自己参与共管的活动 |
| 个人四页（广场/我的报名/信用/时长） | 仅学生视角 | 工作台视角访问自动跳回活动管理 |

活动多人共管（`activity_managers`）：创建者自动成为管理者且**不可被移除**；管理者之间权限完全相同、
可互相添加/移除，但**不能移除自己**；被添加者必须持有老师/青协身份；管理员无需添加、天然可见全部。

## 三、岗位模型（场次三模式）

结构：Activity ─ PositionGroup（场次：起止时间+总容量+模式）─ Station（二级工位）。

- `single` 仅一级岗位：无二级工位，报名即锁定该时段（如开放日讲解场）。
- `assign` 二级待定：报名只锁时段，截止后由排班程序统一分工位（图书整理周）。
- `fine` 直接定二级：报名时直接选定工位（运动会）。
- 二级工位只有职责/地点/名额/条件要求/培训说明，**无独立时间——时间归场次**；
  同场次工位互斥（一人一场次只占一个工位）；fine 模式要求 Σ工位名额 ≤ 场次容量。

## 四、报名 / 候补 / 信用

- 报名粗粒度冲突拦截：与本人在所有活动的有效报名区间重叠即拒绝（409 附冲突明细）。
  **候补中的场次同样算已占用时段**——排了队就等于预留了这段时间。
- 名额判断含两层：场次总容量 +（fine 模式）所选工位名额；满员进候补队列（按报名时间排序）。
  容量判断与落库整段持 Redis 场次锁串行化，配合 `enrollments` 的部分唯一索引，
  杜绝抢报瞬间的超卖与重复报名（见 `backend/locks.py`）。
- 报名时可勾选**是否接受岗位调剂**（`allow_reassign`），随时可改。这只是给管理者的参考：
  名单、分配面板与导出表都会显示，管理者仍可调换工位，但违背本人意愿的调换会连同姓名写进留痕。
- 正常退选释放名额 → 队首转正 pending_confirm，开 **2 小时确认窗口**（清扫协程+惰性判定双保险），
  超时记一次"放弃"并顺延下一位；主动放弃同理。**连续 3 次放弃暂停候补资格**（约一学期），
  转正确认成功即清零计数。
- 开始前 **24h 内退选扣信用分 5 分**，且该名额被**扣留**（`seat_held`）：不释放给候补、
  不回到公共名额池，本人也无法再报同一场次。分数 **<60 自动冻结报名 30 天**。
- 「连续 3 次放弃候补」惩罚的只是排队资格：有空位时照常可以正常报名，只有满员需要排队时才拦。
- 限制准入（《志愿者服务规范》）：不得参与 A/B/C/D 中若干类活动 + 截止日；
  「招募在限内、活动已出限」的允许报名。

## 五、签到签退与时长

- 同一签到码：**第一次扫=签到、第二次扫=签退**、已签已退再扫拒绝；仅本场次 confirmed 志愿者有效。
- 任务两种：group 统一码 / station 分工位码，有效期默认随场次；二维码 SVG 直达落地页
  `/#/checkin/{token}`；管理端实时大屏（前端 5s 轮询）显示到岗/离岗率。
- 签退宽限：场次结束 **+2 小时**内可自行扫码签退；超时未签退由系统**按场次结束时间兜底**（auto 标记）。
- 时长 = 签退 − 签到，**向上取整至 0.5 小时**；宽限期内未签退标 pending（暂按估算展示）。
- 未签退（pending）与超时兜底（auto）的记录标为**待核**：到场扫个码就走的人会和全程服务的人
  算出一样的时长，所以「确认正常签退的」会跳过它们，需管理者逐条核对数值后单独确认。
- 管理端批量签到/签退/取消签到/取消签退（manual 操作全留痕）；支持手动添加时长，
  与签到统计的一键确认时长一并**回传数智人大**（保存回执号，已回传记录不可删除）。

## 六、导出与审计

- Excel 导出：Sheet1 活动概览，其后**按一级岗位（场次）分 Sheet**（姓名/学号/学院/手机号｜
  工位/时段/地点/签到状态），存在未分配人员时追加名单 Sheet；每次下载写入审计。
- 审计留痕中心：活动/场次/工位增删改、发布、分配与强制分配、签到任务、导出、调分、限制准入、
  集成配置变更全记录（字段级 diff）；部分日志对志愿者可见，在活动页公示摘要。

## 七、外部对接适配层（backend/integrations/）

四个适配器：`shuzhi_renda` 数智人大 / `wecom_work` 企业微信 / `checkin_gateway` 签到系统 /
`sso` 统一身份认证。参数存 `integration_configs` 表，「集成设置」页在线切换 enabled=true 即启用
真实 HTTP 调用，改完生效、无需重启；**开关已开但必填参数为空时自动回落演示模式**。
演示模式行为：数智人大读本地 users 表、回传返回模拟回执号；企业微信把模板消息落 notifications 表
（消息中心即收件箱，模板库 7 类场景）；签到 token 本地生成并本地核验；SSO 模拟"认证已通过"。
真实侧：wecom/shuzhi/sso 已备 HTTP 实现骨架；checkin_gateway 的 create_task/verify_scan
待学校提供接口后补全（表结构不变，token 字段可存对方任务号）。

## 八、目录结构

```
rucva_system/
├── docker-compose.yml          全栈编排：postgres + redis + backend + web + cloudflared
│                               （+ seed / tests 两个一次性任务 profile）
├── .env.example                环境变量模板（口令、演示登录、隧道令牌）
├── pytest.ini / requirements-dev.txt
├── tests/                      回归测试：登录会话、报名闸门与并发、管理端权限与留痕、签到闭环
├── scripts/shot.mjs            CDP 直连 Edge 的截图/点击工具（Node≥22，无第三方依赖）
├── backend/
│   ├── Dockerfile              base → test（跑 pytest）/ runtime（跑 uvicorn）两个 target
│   ├── main.py                 FastAPI 入口：API + SPA 托管 + 定时任务协程
│   ├── config.py / database.py 业务常量与环境变量；PostgreSQL 连接
│   ├── locks.py                ★ Redis 场次锁与登录失败计数
│   ├── models.py / serializers.py   全部表结构与出参组装
│   ├── deps.py                 会话鉴权（require_admin / require_users_page …）
│   ├── perm.py                 activity_managers 多人共管规则
│   ├── enroll_logic.py / credit.py  冲突检测、名额账本、候补流转、信用规则
│   ├── assign.py               ★ 一键分配：按报名先后做二分图匹配，可选同岗延续
│   ├── violations.py           违规登记 → 限制准入建议（《志愿者服务规范》阶梯）
│   ├── copying.py              复制活动 / 批量复制岗位
│   ├── scheduled.py / notify.py  定时任务（状态流转、签到提醒）与人群通知
│   ├── exporter.py / qrcodes.py / audit.py
│   ├── seed.py                 演示数据种子（python -m backend.seed）
│   ├── routers/                auth / activities / me / checkin /
│   │                           staff_activities / staff_ops / staff_admin
│   └── integrations/           shuzhi / wecom / checkin_gateway / sso
└── web/                        Vue3 + Element Plus + ECharts（Vite）
    ├── Dockerfile / nginx.conf Node 构建 → nginx 托管并反代 /api
    └── src/views/              学生端6页 + 工作台6页 + 登录/扫码落地页
        src/components/         AssignPanel（智能排班）/ CheckinPanel（签到大屏）/
                                ProfileDialog（完善个人信息）
```
