"""开放平台接口签名。

签名算错的症状是远端一句「签名校验失败」，真机上根本无从下手——所以这里
把拼串规则钉死。基准值按学校 Java SDK（SudySignUtil.buildSign）的规则推出，
改动拼接顺序或大小写都会立刻失败。
"""
from __future__ import annotations

import re

import pytest

from backend import sudy_sign

APPID = "xgxt"
SECRET = "test-secret"
NONCE = "ibuaiVcKdpRxkhJA"
TS = "1608626070885"


def test_文档样例的签名值():
    assert sudy_sign.build_signature(APPID, SECRET, NONCE, TS) == \
        "5801B6A8F543F619DB608B06D3D65C11958B2C23"


def test_大写十六进制():
    """SDK 用 Hex.encodeHexString(digest, false)，false 即**大写**。

    小写看着只差一点，远端只会回一句校验失败。真机上查这个要查很久。
    """
    sig = sudy_sign.build_signature(APPID, SECRET, NONCE, TS)
    assert sig == sig.upper()
    assert re.fullmatch(r"[0-9A-F]{40}", sig)


def test_summary_插在_nonce_与_timestamp_之间():
    """整串按参数名字典序，summary 不是「拼到末尾」——拼错了签名就不同。"""
    assert sudy_sign.build_signature(APPID, SECRET, NONCE, TS, "abc123") == \
        "C76F2B240D446BE2E0E1DF592A629ECAB6878D19"


def test_不传_summary_时整段不出现():
    """空 summary 与「传了个空串」必须等价，都不能在串里留一个空的 sd-summary。"""
    assert sudy_sign.build_signature(APPID, SECRET, NONCE, TS) == \
        sudy_sign.build_signature(APPID, SECRET, NONCE, TS, "")
    assert sudy_sign.build_signature(APPID, SECRET, NONCE, TS) != \
        sudy_sign.build_signature(APPID, SECRET, NONCE, TS, "x")


def test_换密钥结果就变():
    """密钥确实参与了运算，不是被悄悄忽略掉了。"""
    assert sudy_sign.build_signature(APPID, SECRET, NONCE, TS) != \
        sudy_sign.build_signature(APPID, "other", NONCE, TS)


def test_请求头齐全且每次_nonce_不同():
    a = sudy_sign.signed_headers(APPID, SECRET)
    b = sudy_sign.signed_headers(APPID, SECRET)
    assert set(a) == {"sd-appid", "sd-algorithm", "sd-timestamp",
                      "sd-nonce", "sd-signature"}
    assert a["sd-algorithm"] == "SHA1"
    # nonce 重复会让签名可被重放
    assert a["sd-nonce"] != b["sd-nonce"]
    assert a["sd-signature"] != b["sd-signature"]


def test_测试环境没有密钥不误打真实接口():
    assert not sudy_sign.configured()
    with pytest.raises(RuntimeError):
        sudy_sign.post_json("any/path.rst", {})


def test_返回体里的失败不能当成成功():
    """HTTP 200 但业务码非 0，必须抛出来。

    只看 HTTP 状态的话，「应用没被授权」「收件人不存在」都会被记成发送成功。
    """
    with pytest.raises(RuntimeError):
        sudy_sign._unwrap({"resultCode": 0,
                           "result": {"data": {"code": 1, "successed": False,
                                               "errorInfo": "无权限"}}}, "{}")
    with pytest.raises(RuntimeError):
        sudy_sign._unwrap({"resultCode": 0,
                           "result": {"data": {"code": 0,
                                               "successed": False}}}, "{}")


def test_正常返回剥出_data():
    data = sudy_sign._unwrap(
        {"errorMsg": "", "resultCode": 0,
         "result": {"total": 1, "data": {"code": 0, "successed": True,
                                         "messageId": "m1"}}}, "{}")
    assert data["messageId"] == "m1"
