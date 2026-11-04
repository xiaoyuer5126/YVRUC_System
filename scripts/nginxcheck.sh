#!/bin/sh
# 检查三份 nginx 站点配置的语法：内测/CI 用的 http 那份、本机调试那份、生产那份。
#
# 生产那份平时用不上：页面自检跑的是 http 那份，生产的配置只有真部署时才加载，
# 写错一个分号要等到重启 web 才发作，而那时候平台整个打不开。这里让 nginx 自己
# 读一遍，几十秒的事。
#
# 用与 web 镜像同款的 nginx 镜像跑，模板交给镜像自带的入口脚本展开 ${...}——
# 和线上走的是同一条路，不是另写一份替换规则。
#
# 用法（仓库根目录；要能起容器）：
#   sh scripts/nginxcheck.sh
#
# 生产那份要证书才能过语法检查，这里临时生成一份自签名的，只活在这条命令里。
set -eu

image=nginx:1.27-alpine
repo=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

openssl req -x509 -nodes -days 1 -subj /CN=localhost \
    -keyout "$tmp/privkey.pem" -out "$tmp/fullchain.pem" 2>/dev/null

check() {
    template=$1
    printf '\n== %s\n' "$template"
    # 不给 --entrypoint：镜像的入口脚本先展开模板、再执行 nginx -t，与线上同一条路。
    # 共用片段里 /api/ 的 proxy_pass 直接写了主机名 backend，nginx -t 也要能解析它，
    # 所以给两个服务名各挂一条主机记录指向本机——这里只查语法，不转发请求。
    docker run --rm \
        -e YVRUC_TRUSTED_PROXY_CIDR=172.30.0.0/16 \
        --add-host backend:127.0.0.1 \
        --add-host glitchtip:127.0.0.1 \
        -v "$repo/$template:/etc/nginx/templates/default.conf.template:ro" \
        -v "$repo/web/nginx-shared.conf:/etc/nginx/snippets/yvruc-shared.conf:ro" \
        -v "$tmp:/etc/nginx/certs:ro" \
        "$image" nginx -t
}

check web/nginx-https.conf.template
check web/nginx.conf.template
check web/nginx-local.conf.template

printf '\n三份配置都通过了\n'
