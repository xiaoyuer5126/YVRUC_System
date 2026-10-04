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

COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/web/dist /usr/share/nginx/html

EXPOSE 80
