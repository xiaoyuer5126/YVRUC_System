# 人大校青协志愿服务管理平台

FastAPI + SQLAlchemy（PostgreSQL/SQLite）+ Vue3 / Element Plus / ECharts（Vite 构建）。
覆盖「活动发布 → 选课式报名 → 候补流转 → 智能调配 → 扫码签到签退 → 时长回传」全链路；
排班引擎复用 `../schedule` 求解器（深度2主段超时自动降级深度1）。

## 一、快速启动

### 方式 A · Docker 全栈（推荐）

```powershell
cd platform
docker compose up -d                          # web(nginx):8080 + backend:8000 + db:5433 + cloudflared
docker compose --profile seed run --rm seed   # 可选：重置演示数据
docker logs rucva-cloudflared 2>&1 | Select-String trycloudflare   # 取公网演示地址
```

- 入口 **http://127.0.0.1:8080**（nginx 托管前端并同源反代 `/api`，无跨域）；
  API 也可直连 http://127.0.0.1:8000（scripts/ 下测试脚本无需改动）。
- cloudflared 已配置**固定域名隧道**：token 存于 `platform/.env` 的 `CLOUDFLARE_TOKEN`（该文件勿提交/外传）；
  公网域名在 Cloudflare Zero Trust 控制台该隧道的 Public Hostname 中绑定，`docker logs rucva-cloudflared` 可看连接状态。
- 镜像：`platform/docker/backend.Dockerfile`（Python3.12 + 内置 `../schedule` 求解器）、
  `platform/docker/web.Dockerfile`（Node 构建 → nginx）。上传的活动二维码存命名卷 `rucva_uploads`。

### 方式 B · 本机直跑

```powershell
.\start.bat            # 等价 start.ps1；参数 -RebuildDB 重置演示数据、-BuildWeb 强制重建前端
```

或手动（Docker 只起数据库）：

```bash
docker compose up -d postgres   # PostgreSQL 16 @127.0.0.1:5433（rucva/rucva_dev，卷持久化）
cd web && npm install && npm run build   # 产物输出 web/dist，由 FastAPI 托管
python -m backend.seed # 清库重建演示数据（日期相对当天生成，任何一天跑都是"活的"）
python -m uvicorn backend.main:app --port 8000
```

- 引擎创建时探测 Postgres 不可达 → 自动降级 SQLite（`data/rucva.db`）；`RUCVA_DATABASE_URL` 可覆盖。
- 访问 http://127.0.0.1:8000 ；登录页可一键选择演示账号；`web/public/debug-login.html?sid=学号&next=路径`
  免交互自动登录（无头浏览器验证用）。进程内带候补转正清扫协程（60s/轮）。

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
- 名额判断含两层：场次总容量 +（fine 模式）所选工位名额；满员进候补队列（按报名时间排序）。
- 正常退选释放名额 → 队首转正 pending_confirm，开 **2 小时确认窗口**（清扫协程+惰性判定双保险），
  超时记一次"放弃"并顺延下一位；主动放弃同理。**连续 3 次放弃暂停候补资格**（约一学期），
  转正确认成功即清零计数。
- 开始前 **24h 内退选扣信用分 5 分**且名额不释放给候补；分数 **<60 自动冻结报名 30 天**。
- 限制准入（《志愿者服务规范》）：不得参与 A/B/C/D 中若干类活动 + 截止日；
  「招募在限内、活动已出限」的允许报名。

## 五、签到签退与时长

- 同一签到码：**第一次扫=签到、第二次扫=签退**、已签已退再扫拒绝；仅本场次 confirmed 志愿者有效。
- 任务两种：group 统一码 / station 分工位码，有效期默认随场次；二维码 SVG 直达落地页
  `/#/checkin/{token}`；管理端实时大屏（前端 5s 轮询）显示到岗/离岗率。
- 签退宽限：场次结束 **+2 小时**内可自行扫码签退；超时未签退由系统**按场次结束时间兜底**（auto 标记）。
- 时长 = 签退 − 签到，**向上取整至 0.5 小时**；宽限期内未签退标 pending（暂按估算展示）。
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
platform/
├── start.bat / start.ps1       一键启动（-RebuildDB / -BuildWeb）
├── docker-compose.yml          全栈编排：postgres + backend + web + cloudflared（+seed 任务）
├── docker/                     容器化：backend.Dockerfile / web.Dockerfile / nginx.conf
├── scripts/
│   ├── e2e_test.py             后端端到端自测（TestClient，不启服务）
│   ├── perm_test.py            权限矩阵/共管边界断言
│   ├── checkout_test.py        签退/时长闭环断言
│   ├── self_removal_test.py    自移/移除创建者拦截断言
│   ├── patch_guards.py / patch_seed.py   历史补丁脚本
│   └── shot.mjs                CDP 直连 Edge 的截图/点击工具（Node≥22，无第三方依赖）
├── backend/
│   ├── main.py                 FastAPI 入口：API + SPA 托管 + 候补清扫协程
│   ├── config.py / database.py 业务常量；PG 探测失败降级 SQLite
│   ├── models.py / serializers.py   全部表结构与出参组装
│   ├── deps.py                 会话鉴权（require_admin / require_users_page …）
│   ├── perm.py                 activity_managers 多人共管规则
│   ├── enroll_logic.py / credit.py  冲突检测、候补流转、信用规则
│   ├── scheduler_adapter.py    ★ 平台数据 ⇄ schedule 求解器适配（预览/应用/强排）
│   ├── exporter.py / qrcodes.py / audit.py
│   ├── seed.py                 演示数据种子（python -m backend.seed）
│   ├── routers/                auth / activities / me / checkin /
│   │                           staff_activities / staff_ops / staff_admin
│   └── integrations/           shuzhi / wecom / checkin_gateway / sso
└── web/                        Vue3 + Element Plus + ECharts（Vite）
    ├── public/debug-login.html 免交互自动登录调试页
    └── src/views/              学生端6页 + 工作台6页 + 登录/扫码落地页
        src/components/         AssignPanel（智能排班）/ CheckinPanel（签到大屏）
```

## 九、演示账号矩阵

| 学号 | 姓名 | 类型 · 身份 | 演示点 |
|---|---|---|---|
| 2025001 | 王砚秋 | 老师 · 管理员+老师 | 后台全功能，可见全部活动 |
| 2025002 | 李慕清 | 老师 · 老师 | 仅见自己管理的运动会/敬老院 |
| 2025003 | 张远航 | 学生 · 青协+学生 | 切换工作台/个人双视角；共管3个活动+用户与信用 |
| 202302126 | 严望舒 | 学生 · 学生 | 登录即有**候补转正确认倒计时**待处理 |
| 202401111 | 褚天翼 | 学生 · 学生 | 信用 55 分冻结中，报名被拦截 |

另有 49 名覆盖各学院的学生：张启铭（限制准入 C/D）、喻言（已连续弃候补 2 次）等为规则触发素材。

## 十、演示动线（15 分钟讲完五阶段）

| 阶段 | 页面 | 讲什么 |
|---|---|---|
| ①筹备期 | 活动管理→运动会→岗位树 | 场次三模式建组；工位无独立时间；Σ名额≤容量校验；操作全留痕 |
| ②报名期 | 活动广场→运动会详情 | **选课式表格**：展开场次选工位（互斥置灰）、满员一键候补、跨活动时间冲突拦截；用严望舒确认转正倒计时；用褚天翼看冻结拦截 |
| ③调配期 | 图书整理周→报名与调配 | assign 模式：现场建工位→选规则预设→**智能排班试算**→冲突红色高亮→强制分配留痕→发布专属通知 |
| ④执行期 | 校庆晚会→签到管理 | 统一码 GALA01 / 分工位码 BACK02、二维码大屏实时到岗率；手机扫码体验：第一次扫=签到、第二次扫=签退 |
| ⑤收尾期 | 敬老院→导出与时长 | 按一级岗位分 Sheet 的 Excel（可直接点名）；基于签到一键确认时长（0.5h 取整）→回传数智人大拿回执号 |

配套亮点页：审计日志中心（谁动了我的岗位）、用户与信用（限制准入 A/B/C/D）、集成设置（四系统在线切换）。

## 十一、已知边界（演示版）

- 高并发抢报为事务级乐观控制，未引入 Redis 分布式锁；正式上线前补充。
- Excel 加密导出依赖企业微信通道，当前明文下载 + 审计留痕（代码注释已预留说明）。
- 封面使用色相渐变色卡而非图片上传；答疑群二维码仅作图片链接展示容器。
- 签到系统真实对接的 create_task/verify_scan 为 NotImplementedError 占位，等待学校接口文档。
