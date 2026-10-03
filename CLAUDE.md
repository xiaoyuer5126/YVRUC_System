# 给在这个仓库里干活的 agent

中国人民大学青年志愿者协会志愿服务管理平台。怎么跑、目录结构、环境变量见 README。
下面是 README 里没有、但每次改代码都要守的几条。

## 写法

- **注释和文档只讲现在的做法，不讲历史。** 不写「从前 / 以前 / 原先 / 原来的 /
  不再 / 改成了」这类句子，代码要读起来像一开始就是这么写的。
  - 「为什么这样做」照样要写，但写成「要是不这样，会怎样」，而不是「以前那样出过事」。
  - 为什么改、改之前是什么样，写进 commit message 或 PR 说明。
- **从 0 开发，不留兼容。** 开发期数据可以扔：改了表结构就重新 seed，不写迁移，
  也不为「老数据里可能还有」写兼容分支。删功能就删干净，不留死代码和开关。
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

动了页面的，再按 `scripts/viewcheck.mjs` 文件头的说明，在手机（390×844）和桌面
（1440×900）两个视口各跑一遍。

## 提交

- 小修改合在一起提交，commit 少而完整。
- 不提交 `tmp/`、`.env`、上传件和构建产物。

## 这台机器

这是开发机，数据可弃。容器用完就停（`docker compose down`），不留后台常驻；
不需要公网访问时不起 `cloudflared`。
