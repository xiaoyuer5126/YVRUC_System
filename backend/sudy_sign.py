"""学校开放平台的接口签名。

apifile 文档只转述了「排序、拼接、取哈希」，几处真机上会致命的细节（大小写、
可选参数插在哪）以学校提供的 Java SDK（`SudySignUtil.buildSign`）为准：

    stringSignTemp = "sd-algorithm=SHA1"
                   + "&sd-appid="     + appid
                   + "&sd-nonce="     + nonce
                   + ("&sd-summary="  + summary  if summary else "")
                   + "&sd-timestamp=" + timestamp
                   + "&key="          + secret
    sd-signature = toHex(SHA1(stringSignTemp)) 的**大写**十六进制

`sd-summary` 是可选的，且必须插在 nonce 与 timestamp **之间**（整串按参数名
字典序，不是「拼到末尾」）。时间戳是 Unix 毫秒，有效期 20 分钟。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import urllib.request

from . import config

ALGORITHM = "SHA1"
TIMEOUT = 8


def build_signature(appid: str, secret: str, nonce: str, timestamp: str,
                    summary: str = "") -> str:
    """算出 sd-signature。

    纯函数：不碰网络、不读全局状态。签名算错的症状是远端一句「签名校验失败」，
    在真机上根本无从下手，所以这段必须能单测。
    """
    temp = f"sd-algorithm={ALGORITHM}&sd-appid={appid}&sd-nonce={nonce}"
    if summary:
        temp += f"&sd-summary={summary}"
    temp += f"&sd-timestamp={timestamp}&key={secret}"
    return hashlib.sha1(temp.encode("utf-8")).hexdigest().upper()


def signed_headers(appid: str, secret: str, *, summary: str = "") -> dict[str, str]:
    """生成一组签名请求头。nonce 与 timestamp 每次现取，不可复用。"""
    nonce = secrets.token_hex(16)
    timestamp = str(int(time.time() * 1000))
    return {
        "sd-appid": appid,
        "sd-algorithm": ALGORITHM,
        "sd-timestamp": timestamp,
        "sd-nonce": nonce,
        "sd-signature": build_signature(appid, secret, nonce, timestamp, summary),
    }


def configured() -> bool:
    return bool(config.OPENPLATFORM_APPID and config.OPENPLATFORM_SECRET)


def post_json(path: str, payload: dict, *, summary: str = "") -> dict:
    """向开放网关 POST 一个业务接口，返回 `result.data`。

    path 可以是不带域名的接口路径（文档里多数是这种），也可以直接给完整 URL。
    """
    if not configured():
        raise RuntimeError("开放平台 appid/secret 未配置")
    if path.startswith(("http://", "https://")):
        url = path
    else:
        gateway = config.OPENPLATFORM_GATEWAY
        if not gateway:
            raise RuntimeError("开放网关地址未配置")
        url = f"{gateway.rstrip('/')}/{path.lstrip('/')}"

    headers = signed_headers(config.OPENPLATFORM_APPID,
                             config.OPENPLATFORM_SECRET, summary=summary)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(  # noqa: S310
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"开放平台返回的不是 JSON：{raw[:200]}") from exc
    return _unwrap(body, raw)


def _unwrap(body: dict, raw: str) -> dict:
    """剥掉 `{"errorMsg","result":{"data":...},"resultCode"}` 这层壳。

    失败判定必须看 `code` / `successed`，**不能只看 HTTP 状态**——签名不对、
    应用没被授权接口、收件人不存在，全是 200 返回的。只判 HTTP 的话，这些
    情况都会被当成「发送成功」，而消息谁也没收到。
    """
    if not isinstance(body, dict):
        raise RuntimeError(f"开放平台返回结构异常：{raw[:200]}")

    if body.get("resultCode") not in (0, None):
        raise RuntimeError(f"开放平台调用失败（resultCode={body['resultCode']}）："
                           f"{body.get('errorMsg') or raw[:200]}")

    result = body.get("result")
    data = result.get("data") if isinstance(result, dict) else None
    if data is None:
        data = result if isinstance(result, dict) else body

    if isinstance(data, dict):
        if data.get("code") not in (0, None):
            raise RuntimeError(f"开放平台业务失败（code={data['code']}）："
                               f"{data.get('errorInfo') or raw[:200]}")
        if data.get("successed") is False:
            raise RuntimeError(f"开放平台业务失败："
                               f"{data.get('errorInfo') or raw[:200]}")
        return data
    return {}
