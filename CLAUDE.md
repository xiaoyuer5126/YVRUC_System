# 给在这个仓库里干活的 agent

中国人民大学青年志愿者协会志愿服务管理平台。怎么跑、目录结构、环境变量见 README。
下面是 README 里没有、但每次改代码都要守的几条。

## 在 cheese 上开发的 agent

下面两条只约束在 cheese 上开发的 agent；其他 agent 和人开发者不用遵守。

- **一处改动一次 commit。** 每处改动单独提交，不要攒成一批。几处小改动可以放在同一个
  PR 里，但改动本身要一次一处地 commit。机器随时可能被回收，没提交的改动、或提交了
  没推的改动都会丢；子 agent 手里的改动也一样，不推上去别人拿不到。
- **不配置本地测试环境。** 不装 yum/apt、pip/uv 依赖，不装 `node_modules`，不在本机
  起 Postgres/Redis。把代码提交推上去，测试交给 GitHub CI，看 CI 结果。

## 写法

- **注释和文档只讲现在的做法，不讲历史。** 不写「从前 / 以前 / 原先 / 原来的 /
  不再 / 改成了」这类句子，代码要读起来像一开始就是这么写的。
  - 「为什么这样做」照样要写，但写成「要是不这样，会怎样」，而不是「以前那样出过事」。
  - 为什么改、改之前是什么样，写进 commit message 或 PR 说明。
- **改模型必须同时写迁移。** 动了 `backend/models.py` 的表、列、索引、约束，同一个提交里
  带上 `backend/migrations/versions/` 下的迁移：用 autogenerate 生成，再逐行检查（改名、
  新加非空列、搬数据都要手工改），做法见 README「数据库迁移」。库里是真实数据，迁移要
  保住已有的行，不能靠清库重建。`tests/test_migrations.py` 比对迁移与模型，对不上就红。
- **不留兼容代码。** 代码里不为「老数据里可能还有」写兼容分支，老数据该改的在迁移里改掉。
  删功能就删干净，不留死代码和开关。
- 前端间距只走 4 / 8 / 12 / 16 / 24（`scripts/spacecheck.py` 会查）。
- 不把数据当 HTML 渲染：不用 `v-html`，不开 `dangerouslyUseHTMLString`
  （`scripts/vuecheck.py` 会查）。
- 时间一律是不带时区的本地时间（Asia/Shanghai）；客户端传来的时间串走
  `models.parse_local_dt`。

## 验证

改完至少跑这几样，结果如实写进提交说明：

```sh
docker compose --profile test run --rm --build tests python -m pytest -q  # 必须 --build，否则跑的是旧镜像
python3 scripts/vuecheck.py && python3 scripts/spacecheck.py
cd web && npx vite build
```

测试容器（compose 的 tests 服务）与 `tests/conftest.py` 都强制清空了学校对接参数，
conftest 还堵住了 urllib 出网，全量 pytest 不会触达学校接口。

页面检查（`scripts/viewcheck.mjs`，手机 390×844、桌面 1440×900 两个视口）在 CI 的
「页面自检」里跑：PR 改到 web/、backend/、scripts/ 时自动执行，出 ✗ 不合并。红了先看
那次运行的日志，再下载附件 viewcheck（出 ✗ 页面的截图、两遍输出、后端日志）。
本地要跑按 viewcheck.mjs 文件头的说明。

## 提交

- 小修改合在一起提交，commit 少而完整。
- 不提交 `tmp/`、`.env`、上传件和构建产物。

## 这台机器

这是开发机，数据可弃。容器用完就停（`docker compose down`），不留后台常驻；
不需要公网访问时不起 `cloudflared`。
