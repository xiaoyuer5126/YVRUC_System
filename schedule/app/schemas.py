from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ProjectSummary(BaseModel):
    """列表页用。**不含 data**——那里面有全员的联系方式，列一次表没必要全带出来。"""

    id: uuid.UUID
    name: str
    version: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    expiring_soon: bool
    # 概览数字，从 data 里数出来的，省得前端为了显示"12 个岗位"去拉整份状态。
    positions: int
    volunteers: int
    assignments: int
    # 三种身份要分开：**协作者和「dev 在看别人的项目」不是一回事**。
    # 只有一个 mine 布尔的话，界面会对协作者说「你以开发者身份打开」，那是句假话。
    role: str = "owner"
    # 删除只有创建者能做。前端据此决定要不要画那个按钮，服务端照样再拦一次。
    can_delete: bool = True
    # 是不是当前用户自己的。只有 dev 开了「全部使用者」时才会出现 false 的行；
    # 前端据此决定要不要显示归属人，别让人误以为那是自己的项目。
    mine: bool = True
    # 归属人昵称。**只在 mine=False 时下发**——自己的项目上显示自己的名字是噪声，
    # 而且那等于给每一行都带上一份用户身份信息。
    owner_nickname: str | None = None


class ProjectDetail(ProjectSummary):
    data: dict
    # 派生视图（岗位统计 / 排班表 / 候补队列 / 报名记录）。由服务端算，
    # 前端只负责画——候补队列的落选原因和求解器共用一套判据，抄一遍会漂。
    views: dict


class CreateProject(BaseModel):
    name: str = Field(max_length=128)
    # 用示例数据建：新用户点进来就有东西可看，不用先去下模板。
    with_sample: bool = False


class RenameProject(BaseModel):
    name: str = Field(max_length=128)


class SaveProject(BaseModel):
    data: dict
    # 乐观并发。前端把读到的版本号带回来，对不上说明别处改过了。
    version: int
    # 这次改动叫什么，进历史版本用。**空 = 手动微调**，会按 3 分钟窗口合并；
    # 非空 = 重要节点（清空岗位、保存规则……），单独存一版。
    # 由前端给：只有它知道这次 PUT 到底是什么操作，服务端拿两份 data 反推
    # 既脆弱又猜不准。
    action: str = Field(default="", max_length=32)


class ProjectVersion(BaseModel):
    """历史版本的一行。**不含 data**——列表要轻，内容按需单取。"""

    id: uuid.UUID
    version: int
    action: str
    actor_id: uuid.UUID
    actor_nickname: str
    created_at: datetime
    saved_at: datetime
    # 空 action 的那些是攒出来的，界面上要说清是「一段时间的连续微调」
    merged: bool


class PreviewRules(BaseModel):
    """试算一份规则配置的后果，不落库。见 routes.preview_rules。"""

    rules: dict


class SolveRequest(BaseModel):
    """预览时不带版本；应用时必须带，确保应用的就是预览过的那一版。"""

    apply: bool = False
    version: int | None = None


class DiffRow(BaseModel):
    type: str
    name: str
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class SolveResult(BaseModel):
    """排班结果。

    **预览只回变更,不回整份状态。** 从前这里带着 `data` + `views`,一份六千条
    报名的项目就是 3.5MB——而预览界面只用得上 diff 那几个字段,前端拿到手第一件事
    是把它整个丢掉。白传一遍还要在浏览器里反序列化一遍。
    """

    version: int
    # 与当前排班相比的变动，就是桌面版那个"变更预览"。
    diff: list[DiffRow]
    summary: dict[str, int]
    # 这一次是不是退到浅层算的（见 solver.py）。是的话要在界面上说清楚。
    degraded: bool
    seconds: float
    # 保底重试补上了几个人；重试自己有没有超时。
    rescued: int = 0
    rescue_timed_out: bool = False
    # **只有 apply=true 才带**：省掉前端应用完再拉一次整份状态。
    project: ProjectDetail | None = None
