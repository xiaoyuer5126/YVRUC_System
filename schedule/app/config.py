from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/tools/schedule/app/config.py -> 仓库根
ROOT_DIR = Path(__file__).resolve().parents[4]

# 工具服务的 schema。文件夹名 = URL 段 = compose 服务名 = 这个 schema 名。
DB_SCHEMA = "tool_schedule"


class Settings(BaseSettings):
    project_name: str = "RUCommunity Tool · 排班"

    # —— 数据库 ——
    # 与社区共用一个 Postgres 实例，但用**独立角色 + 独立 schema**：
    # 这个连接串里的角色只被授权 tool_schedule 这一个 schema，读不到社区的
    # users 表，也读不到隔壁 bv2008 的任务记录。
    database_url: str = Field(alias="SCHEDULE_DATABASE_URL")

    # —— 身份 ——
    # 社区 API 的内网地址。这个服务**不持有 AUTH_SECRET_KEY**：
    # 它把浏览器带来的 cookie 原样转给社区换一个 user_id，只能问「这是谁」，
    # 永远不能签发身份。
    community_api_url: str = Field(default="http://api:9367", alias="COMMUNITY_API_URL")
    auth_cookie_name: str = Field(default="rucommunity_session", alias="AUTH_COOKIE_NAME")
    identity_cache_seconds: int = Field(default=60, alias="SCHEDULE_IDENTITY_CACHE_SECONDS")

    # —— 求解 ——
    # 排班分两段跑，各有各的上限（见 solver.py）：
    #   主段   深度 2 → 正常情况走这一段
    #   降级段 深度 1 → 主段超时了才走，多项式，保证有结果可交
    #
    # 求解器不会真的死循环——三个循环都有硬上限——但 `find_slot` 的回溯增广
    # 是指数级的：每层要试「岗位里的每个人 × 他报过的每个其他岗位」，深度一大
    # 就能跑上几个小时，表现和卡死一样。这两个数是**保护服务器**的闸门：
    # 最坏情况一次排班占一个核 60+60=120 秒，到点必停。
    # 同一个人同时只能有一次在算，见 solve_guard.py。
    #
    # 深度 2 之下这两道闸门正常项目根本碰不到，留着是为了兜住病态的岗位表。
    solve_main_seconds: float = Field(default=60.0, alias="SCHEDULE_SOLVE_MAIN_SECONDS")
    solve_fallback_seconds: float = Field(default=60.0, alias="SCHEDULE_SOLVE_FALLBACK_SECONDS")
    # 保底重试（只对一个岗位都没拿到的人用大深度再试一次）的**独立**预算。
    # 它是锦上添花的一趟，烧不动就停，不占上面两段的时间；超时也不算失败。
    solve_rescue_seconds: float = Field(default=20.0, alias="SCHEDULE_SOLVE_RESCUE_SECONDS")

    @property
    def solve_max_seconds(self) -> float:
        """最坏情况的总时长。nginx 的读超时要比它宽，否则用户看到的是 504。

        最坏是「主段烧满超时 → 降级段跑完 → 降级段的保底重试也烧满」：
        60 + 60 + 20 = 140 秒。主段超时时它自己那趟保底重试压根没开始
        （超时在 sweep 里就抛了），所以 rescue 只算一份。
        """
        return (self.solve_main_seconds + self.solve_fallback_seconds
                + self.solve_rescue_seconds)

    # —— 规模上限 ——
    # 超出就当场拒掉，而不是让人等满 30 秒才知道表传错了。校内活动离这些数
    # 差着一个量级，够用。
    max_positions: int = Field(default=300, alias="SCHEDULE_MAX_POSITIONS")
    max_volunteers: int = Field(default=3000, alias="SCHEDULE_MAX_VOLUNTEERS")
    max_signups: int = Field(default=30000, alias="SCHEDULE_MAX_SIGNUPS")
    # 上传的表格大小上限。几千行的报名表也就几百 KB。
    max_upload_bytes: int = Field(default=8 * 1024 * 1024, alias="SCHEDULE_MAX_UPLOAD_BYTES")

    # —— 保留期 ——
    # 项目里存着全员的姓名、身份证号、联系方式、微信。活动结束就没人再管它了，
    # 所以给一个到期日：过期自动删除，到期前在列表里提示，可以一键续期。
    retention_days: int = Field(default=180, alias="SCHEDULE_RETENTION_DAYS")
    # 剩这么多天开始提示。
    expiring_soon_days: int = Field(default=14, alias="SCHEDULE_EXPIRING_SOON_DAYS")

    api_host: str = "0.0.0.0"
    api_port: int = 9372

    # 允许发起写操作的来源。这个服务吃的是社区那张 cookie，所以必须自己也拦一道。
    #
    # **必须复用社区那份 `CORS_ORIGINS`，不能另起一个键。** 两份名单意味着上线时
    # 一定会漏配一份，而漏配的表现是「所有写操作 403」，很难往这上面联想——
    # bv2008 上线时就踩过一次。
    allowed_origins: list[str] = Field(
        default=["http://127.0.0.1:9365", "http://localhost:9365"],
        alias="CORS_ORIGINS",
    )

    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", ROOT_DIR / "apps" / "tools" / "schedule" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def sqlalchemy_database_url(self) -> str:
        # 与社区 API 保持同样的归一化：psycopg3 要 postgresql+psycopg 前缀。
        if self.database_url.startswith("postgresql+"):
            return self.database_url
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
