# 前端镜像：Node 构建 Vite 产物 → nginx 托管并反代 API
FROM node:20-alpine AS build

# 默认走国内镜像；海外机器（CI）构建时传 --build-arg NPM_REGISTRY=https://registry.npmjs.org
ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --registry=$NPM_REGISTRY --no-audit --no-fund

COPY web/ ./
RUN npm run build


FROM nginx:1.27-alpine

# 站点主体：两份形态配置都 include /etc/nginx/snippets/yvruc-shared.conf
COPY web/nginx-shared.conf /etc/nginx/snippets/yvruc-shared.conf
# 默认进镜像的是 https 那份（内测与生产用）；本机调试形态由
# docker-compose.local.yml 把 nginx-local.conf.template 挂到
# /etc/nginx/templates/default.conf.template 顶掉它。
# 文件名带 .template：nginx 入口脚本启动时把 ${...} 换成环境变量再写到
# /etc/nginx/conf.d/default.conf（YVRUC_TRUSTED_PROXY_CIDR 见 compose）
COPY web/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/web/dist /usr/share/nginx/html

# 443 是两种 https 形态的监听口：内测由 caddy 转到 80，生产直接对校园网开 443；
# 本机调试形态监听 80。两个都声明，避免用哪个形态都得改镜像。
EXPOSE 80 443
