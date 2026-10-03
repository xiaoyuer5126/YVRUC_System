"""身份证号的字段级加密。

平台只有一处真的需要身份证号原文：志愿北京的时长导出（见 exporter.py）。
其余一切出口都只给脱敏值。但它在库里是躺着的——数据库备份被复制走、误
导出、整库被拖，拿到的都是原文。所以写进去时加密，读出来时解密：业务代码
照旧把 user.id_card 当明文用，代价只是不能按它检索（本来也没有这种用法）。

密钥来自 YVRUC_ID_CARD_KEY，必须与数据库分开保管，否则加密只是走个过场。
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from . import config

KEY_HINT = ("未配置 YVRUC_ID_CARD_KEY。生成一把："
            "python -c \"from cryptography.fernet import Fernet;"
            "print(Fernet.generate_key().decode())\"，写进 .env 后重启。")


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if not config.ID_CARD_KEY:
        raise RuntimeError(KEY_HINT)
    try:
        return Fernet(config.ID_CARD_KEY)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"YVRUC_ID_CARD_KEY 不是合法的密钥：{exc}") from exc


def check() -> None:
    """启动自检：密钥不对就别起来，免得等到有人填资料时才炸。"""
    _fernet().decrypt(_fernet().encrypt(b"self-check"))


class EncryptedString(TypeDecorator):
    """存密文、读明文的字符串列。空串与 None 原样放行（没填就是没填）。"""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not value:
            return value
        return _fernet().encrypt(str(value).encode()).decode()

    def process_result_value(self, value, dialect):
        if not value:
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            # 换过密钥、或者拿旧库配新密钥。这时候返回乱码比报错更糟：
            # 会被当成真号导出去、报上去。
            raise RuntimeError("身份证号解密失败：YVRUC_ID_CARD_KEY 与"
                               "库里的数据对不上") from exc
