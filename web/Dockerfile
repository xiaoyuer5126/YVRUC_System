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

# 文件名带 .template：nginx 入口脚本启动时把 ${...} 换成环境变量再写到
# /etc/nginx/conf.d/default.conf（目前只有 YVRUC_TRUSTED_PROXY_CIDR，见 compose）
COPY web/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/web/dist /usr/share/nginx/html

# 只监听 80：TLS 在 caddy（内网）或 Cloudflare 边缘（公网）终止
EXPOSE 80
