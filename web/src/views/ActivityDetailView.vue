<template>
  <!-- 首次加载还没有内容可显示，放个占位；数据在时刷新走原来的刷新条，不闪 -->
  <div v-if="loading && !act" class="page" v-loading="loading" style="min-height:300px"></div>
  <div v-else-if="loadError" class="page">
    <el-result icon="error" :title="t('加载失败：{0}', [loadError])">
      <template #extra>
        <el-button type="primary" @click="load">{{ t('重试') }}</el-button>
        <el-button @click="$router.push('/square')">{{ t('回活动广场') }}</el-button>
      </template>
    </el-result>
  </div>
  <div class="page" v-else-if="act">
    <el-page-header @back="goBack" :title="t('返回')">
      <!-- 窄屏下标题与标签分行：三样东西挤一行会被折成三行 -->
      <template #content>
        <span class="hd-title">{{ act.title }}</span>
        <span class="hd-tags">
          <el-tag :color="modeColor(act.recruit_mode)" effect="dark" style="border:none">
            {{ t(act.recruit_label) }}
          </el-tag>
          <el-tag :type="statusType">{{ t(act.display_label) }}</el-tag>
        </span>
      </template>
    </el-page-header>

    <el-row :gutter="16" style="margin-top:16px">
      <el-col :span="17">
        <el-card ref="infoCardRef" shadow="never">
          <h3>{{ act.title }}</h3>
          <p style="white-space:pre-wrap;color:#606266;line-height:1.6">{{ act.intro }}</p>
          <el-descriptions :column="narrow ? 1 : 2" border size="small">
            <el-descriptions-item :label="t('组织单位')">{{ act.organizer_text || '—' }}</el-descriptions-item>
            <el-descriptions-item :label="t('活动时间')">
              {{ actTime.text }}<span v-if="actTime.more" class="dim">{{ t('（具体时段见下方岗位安排）') }}</span>
            </el-descriptions-item>
            <el-descriptions-item :label="t('活动地点')">
              {{ act.location || t('待定') }}
              <!-- 高德的网页版标点：手机上装了高德会问要不要打开 App，没装就在
                   网页里看路线。coordinate=wgs84 让高德自己换算——我们存的是
                   天地图/GPS 那一套坐标，直接当高德坐标用会偏几百米 -->
              <el-link v-if="act.location_point" :href="navUrl" target="_blank"
                       type="primary" style="margin-left:8px">{{ t('导航') }}</el-link>
            </el-descriptions-item>
            <el-descriptions-item :label="t('联系人')">
              <span v-for="(c, i) in act.contacts" :key="i" style="margin-right:12px">
                {{ c.name }} <span class="mono">{{ c.phone }}</span>
              </span>
              <span v-if="!act.contacts?.length">—</span>
            </el-descriptions-item>
            <el-descriptions-item v-if="!act.record_only" :label="t('报名时间')">
              {{ fmtDT(act.reg_start) }} ~ {{ fmtDT(act.reg_end) }}
            </el-descriptions-item>
            <!-- 招募范围写在明处：不在范围内的人不该报到一半才被拦下 -->
            <el-descriptions-item v-if="!act.record_only" :label="t('招募范围')">
              {{ tTags(act.recruit_scope_text) }}
            </el-descriptions-item>
          </el-descriptions>
          <div v-if="act.volunteer_requirement || act.precautions || act.volunteer_benefit"
               style="margin-top:12px">
            <div v-if="act.volunteer_requirement" class="side-note">
              <b>{{ t('志愿者要求') }}</b>
              <p>{{ act.volunteer_requirement }}</p>
            </div>
            <div v-if="act.precautions" class="side-note">
              <b>{{ t('注意事项') }}</b>
              <p>{{ act.precautions }}</p>
            </div>
            <div v-if="act.volunteer_benefit" class="side-note benefit">
              <b>{{ t('志愿者保障') }}</b>
              <p>{{ act.volunteer_benefit }}</p>
            </div>
          </div>
        </el-card>

        <!-- 补记录活动只有管理者和审批线上的人看得到这一页：没有岗位可报 -->
        <el-alert v-if="act.record_only" type="info" show-icon :closable="false"
                  :title="t('不公开招募（补记录）')"
                  :description="t('线下已组织过的活动，不在广场展示、不接受报名，没有岗位；审批通过后由管理者导入名单补记时长。')" />

        <!-- 审批人看的是同一个页面，只是活动还没公开；不另做一套只读页 -->
        <el-alert v-if="act.review_preview" type="info" show-icon :closable="false"
                  :title="t('审批预览 · 当前状态「{0}」', [t(act.status_label)])"
                  :description="t('当前作为审批人查看活动的完整安排。通过或驳回请回「活动审批」页操作。')" />

        <el-alert v-if="act.editing" type="warning" show-icon :closable="false"
                  :title="t('负责人正在调整本活动的安排，报名暂时关闭')"
                  :description="t('调整期间无法报名或退选，保存后会将变更通知已报名志愿者。')" />

        <el-alert v-if="isTeacher" type="info" show-icon :closable="false"
                  :title="t('老师不参与志愿报名')" />

      </el-col>

      <!-- 右：变更记录只留标题行，全文进弹窗；下面是时间冲突。
           两块的合计高度跟着左边活动信息卡片走，冲突多了在卡片里滚。 -->
      <el-col :span="7">
        <div class="side-col" :style="sideColStyle">
          <el-card v-if="act.change_logs?.length" shadow="never" class="logs-card">
            <div class="logs-head">
              <b>{{ t('变更记录') }}</b>
              <el-link type="primary" underline="never" @click="logDialog = true">{{ t('查看全部') }}</el-link>
            </div>
          </el-card>
          <!-- 时间冲突沿用原来的红色提示条，只是挪到变更记录下面；
               块高了在提示条里滚，不再把右栏撑长 -->
          <div v-if="act.my_time_conflicts?.length" class="conflict-wrap">
            <el-alert type="error" :closable="false">
              <p style="margin:0 0 4px"><b>{{ t('您有时间冲突的活动：') }}</b></p>
              <p v-for="c in act.my_time_conflicts" :key="c.enrollment_id"
                 style="margin:4px 0;font-size:13px">
                {{ conflictLine(c, act.id) }}
              </p>
            </el-alert>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- 还没到报名开始：倒计时归零时就地开闸（unlock），不等重拉。
         按钮在这之前是灰的（enroll_open 由后端算，含 reg_start）。 -->
    <div v-if="opensAt && !isTeacher" class="reg-countdown">
      <el-icon><Clock /></el-icon>
      <span>{{ t('报名于') }} <b>{{ fmtDT(act.reg_start) }}</b> {{ t('开始，还有') }}</span>
      <el-countdown :value="opensAt" :format="openFormat" @finish="unlock" />
      <span class="dim">{{ t('时间到自动开放，不用刷新页面') }}</span>
      <!-- 订阅报名开始提醒：离开始不到几分钟就收起，那时页面上的倒计时已经够用 -->
      <el-button v-if="!subClosed" class="sub-btn" :class="{ 'sub-on': act.subscribed }"
                 :type="act.subscribed ? '' : 'primary'" plain :loading="subBusy"
                 :icon="act.subscribed ? BellFilled : Bell"
                 @mouseenter="subHover = true" @mouseleave="subHover = false"
                 @click="toggleSubscribe">{{ subLabel }}</el-button>
    </div>

    <!-- 下面两段各占一整行：报名申请、岗位安排 -->
    <div v-if="screenedApplying && !isTeacher" class="section-title">{{ t('报名申请') }}<span class="dim">{{ t('勾选意向岗位并填写报名问卷，截止后由负责人筛选录取；岗位安排见下方') }}</span></div>
    <!-- 名额是抢的：刷新按钮就放在名额上方，别让人翻回页头去找 -->
    <div v-else class="section-title">{{ t('岗位安排') }}<span class="dim">{{ t('点击岗位名称可查看详细安排') }}</span>
      <RefreshBar :load="load" :at="loadedAt" size="small" style="margin-left:auto" /></div>

    <!-- 报名后筛选：先是申请入口，下面照旧列岗位安排——
         审批人要在同一页看完整安排，学生也要先知道自己在报什么 -->
    <ApplicationPanel v-if="screenedApplying && !isTeacher" :act="act" @changed="load"
                      style="margin-bottom:12px" />
    <div v-if="screenedApplying && !isTeacher" class="section-title">{{ t('岗位安排') }}<span class="dim">{{ t('仅供了解；录取后由负责人安排到具体岗位') }}</span>
      <RefreshBar :load="load" :at="loadedAt" size="small" style="margin-left:auto" /></div>

    <!-- 我报的岗位：组长、培训内容、材料这些报上之后才看得到的，在这里摊开。
         放在两种看法外面，切到哪种都在 -->
    <div v-if="myShifts.length" class="mine-list">
      <el-card v-for="g in myShifts" :key="g.id" shadow="never" class="mine-card">
        <div class="mine-title">
          <span>{{ t('您报的：') }}<b>{{ slotName(g, myPosition(g)) }}</b></span>
          <el-tag size="small" :type="MY_STATUS[g.my_enrollment.status].type">{{ myStatusText(g) }}</el-tag>
          <span class="dim">{{ tTime(g.time_text) }}</span>
        </div>
        <!-- 报了名却没有对应的岗位（补招时没指定岗位，或岗位被删）：表格里没有
             这一行的按钮，退选与确认转正放在这里 -->
        <div v-if="!myStationId(g) && !isTeacher" class="m-pos-act">
          <template v-if="g.my_enrollment.status === 'pending_confirm'">
            <el-countdown format="HH:mm:ss" :value="deadlineTs(g)" />
            <el-button type="primary" size="small" :disabled="paused"
                       @click="confirmPromo(g)">{{ t('确认转正') }}</el-button>
          </template>
          <el-button v-else-if="canCancel(g)" size="small" type="danger" plain
                     :disabled="paused" @click="cancelEnroll(g)">
            {{ t(g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选') }}
          </el-button>
        </div>
        <template v-if="myPosition(g)">
          <p class="mine-note"><b>{{ t('您的岗位：') }}</b>{{ myPositionName(g) }}
            <span class="dim">{{ myPosition(g).location || g.location || act.location || t('地点待定') }}</span></p>
          <p v-if="myPosition(g).leader_name" class="mine-note">
            <b>{{ t('组长：') }}</b>{{ myPosition(g).leader_name }}
            <template v-if="myPosition(g).leader_contact">{{ t('（手机 {0} · 微信 {1}）', [myPosition(g).leader_contact.phone || '—', myPosition(g).leader_contact.wechat || '—']) }}</template>
          </p>
          <p v-if="myPosition(g).training_note" class="mine-note">
            <b>{{ t('培训内容：') }}</b>{{ myPosition(g).training_note }}
          </p>
          <!-- 服务任务与岗位要求：窄屏也要列出，到岗前得知道去干什么 -->
          <p v-if="myPosition(g).service_task" class="mine-note">
            <b>{{ t('服务任务：') }}</b>{{ myPosition(g).service_task }}
          </p>
          <p v-if="myPosition(g).position_requirement" class="mine-note">
            <b>{{ t('岗位要求：') }}</b>{{ myPosition(g).position_requirement }}
          </p>
          <p v-if="myPosition(g).attire_text" class="mine-note">
            <b>{{ t('志愿服装：') }}</b>{{ attireText(myPosition(g)) }}
          </p>
          <p v-if="myPosition(g).pay_text" class="mine-note">
            <b>{{ t('报酬方式：') }}</b>{{ t('{0}（核准时长时再选）', [payText(myPosition(g))]) }}</p>
          <MaterialList v-if="myPosition(g).attachments?.length"
                        :items="myPosition(g).attachments"
                        :label="t('本岗位学习材料（请提前学习）')" />
        </template>
        <!-- 调剂意愿：状态用标签显示，切换走右边的开关 -->
        <div class="m-reassign">
          <span class="dim">{{ t('岗位调剂：') }}</span>
          <el-tooltip :content="t('仅在名额不足时可能进行调剂，负责人可看到你的意愿')"
                      placement="top">
            <el-tag size="small" effect="plain"
                    :type="g.my_enrollment.allow_reassign ? 'success' : 'info'">
              {{ t(g.my_enrollment.allow_reassign ? '接受调剂' : '不接受调剂') }}
            </el-tag>
          </el-tooltip>
          <el-switch :model-value="g.my_enrollment.allow_reassign" size="small"
                     @change="toggleReassign(g)" />
        </div>
      </el-card>
    </div>

    <!-- 两种看法只是同一批岗位换个排法，报名按钮是同一个（PositionAction） -->
    <div v-if="items.length" class="toolbar">
      <el-radio-group v-model="detailView" size="small">
        <el-radio-button value="time">{{ t('按时间看') }}</el-radio-button>
        <el-radio-button value="role">{{ t('按岗位看') }}</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 窄屏：竖排卡片。手机上选岗位是最要紧的动作，按钮直接铺开 -->
    <div v-if="narrow" class="m-shifts">
      <template v-for="row in cardRows" :key="row.key">
        <!-- 一张卡：按时间看是一条岗位，按岗位看是同名的几条；培训/彩排挂在叫到的岗位下面 -->
        <el-card shadow="never" class="m-shift">
          <div class="m-shift-head">
            <el-link class="m-shift-name" type="primary" underline="never"
                     @click="showDetail(row.slots[0])">{{ row.name }}</el-link>
            <el-tag v-for="x in nameTags(row.slots[0])" :key="x.text" size="small"
                    :type="x.type" effect="plain">{{ x.text }}</el-tag>
          </div>
          <div v-if="row.slots[0].assign" class="dim">{{ t('报名后统一安排具体岗位') }}</div>
          <div v-else-if="row.slots[0].p.service_task" class="dim pre">{{ row.slots[0].p.service_task }}</div>
          <div v-if="row.slots.length > 1" class="dim">{{ t('以下时段分别报名') }}</div>
          <template v-for="x in row.slots" :key="x.key">
            <div class="m-pos" :class="{ mine: isMine(x), blocked: x.p.eligible === false }">
              <div class="m-pos-head">
                <SlotTime :g="x.g" :reg-open="regOpen" />
                <span class="dim m-quota">{{ x.p.enrolled }}/{{ x.p.quota }}<template v-if="x.p.waitlist_count">{{ t('· 候补 {0}', [x.p.waitlist_count]) }}</template></span>
              </div>
              <div class="m-pos-meta dim">
                {{ slotPlace(x) }}
                <template v-if="x.p.requirement_desc !== '不限'"> · {{ tTags(x.p.requirement_desc) }}</template>
              </div>
              <div v-if="!isTeacher" class="m-pos-act-wrap">
                <PositionAction :g="x.g" :s="x.p" />
              </div>
            </div>
            <div v-if="x.trainings?.length" class="att-list">
              <TrainingItem v-for="tr in x.trainings" :key="tr.id" :s="tr"
                            :location="act.location" @excuse="toggleExcuse" />
            </div>
          </template>
          <div v-if="row.trainings?.length" class="att-list">
            <TrainingItem v-for="tr in row.trainings" :key="tr.id" :s="tr"
                          :location="act.location" @excuse="toggleExcuse" />
          </div>
        </el-card>
      </template>
      <div v-if="showPickRule" class="dim">{{ PICK_RULE }}</div>
    </div>

    <!-- 宽屏：一张长表，一条岗位一行。按岗位看时同名岗位的岗位格上下合并，
         培训/彩排整行插在它叫的那几行下面（合并格之内）；按时间看是平表，
         每条岗位下面挂它要参加的培训/彩排 -->
    <el-card v-else-if="items.length" shadow="never">
      <el-table :data="tableRows" row-key="key" :span-method="rowSpan"
                :row-class-name="rowClass" size="small" border>
        <el-table-column :label="t('岗位')" min-width="170">
          <template #default="{ row }">
            <div v-if="row.type === 'training'" class="att-list att-in-row">
              <TrainingItem :s="row.tr" full :location="act.location"
                            @excuse="toggleExcuse" />
            </div>
            <template v-else>
              <el-link type="primary" underline="never" style="font-weight:600"
                       @click="showDetail(row.it)">{{ row.name }}</el-link>
              <el-tag v-for="x in nameTags(row.it)" :key="x.text" size="small" :type="x.type"
                      effect="plain" style="margin-left:8px">{{ x.text }}</el-tag>
              <div v-if="row.it.assign" class="dim">{{ t('报名后统一安排具体岗位') }}</div>
              <div v-if="row.count > 1" class="dim">{{ t('以下时段分别报名') }}</div>
            </template>
          </template>
        </el-table-column>
        <el-table-column :label="t('时段')" min-width="200">
          <template #default="{ row }">
            <!-- 按岗位看：培训行从这一列起占满整行，左边是合并的岗位格 -->
            <div v-if="row.type === 'training'" class="att-list att-in-row">
              <TrainingItem :s="row.tr" full :location="act.location" @excuse="toggleExcuse" />
            </div>
            <SlotTime v-else-if="row.it" :g="row.it.g" :reg-open="regOpen" />
          </template>
        </el-table-column>
        <el-table-column :label="t('服务地点')" min-width="110">
          <template #default="{ row }">{{ row.it ? slotPlace(row.it) : '' }}</template>
        </el-table-column>
        <el-table-column :label="t('名额')" width="110">
          <template #default="{ row }">
            <template v-if="row.it">
              {{ row.it.p.enrolled }}/{{ row.it.p.quota }}
              <el-tag v-if="row.it.p.remaining <= 0" size="small" type="danger"
                      effect="plain">{{ t('满') }}</el-tag>
              <div v-if="row.it.p.waitlist_count" class="dim">{{ t('候补 {0}', [row.it.p.waitlist_count]) }}</div>
            </template>
          </template>
        </el-table-column>
        <el-table-column :label="t('要求')" min-width="130">
          <template #default="{ row }">
            <template v-if="row.it">
              <span :class="{ 'req-blocked': row.it.p.eligible === false }">
                {{ tTags(row.it.p.requirement_desc) }}
              </span>
              <el-tooltip v-if="row.it.p.eligible === false" placement="top"
                          :content="tTags(row.it.p.ineligible_reason)">
                <el-icon color="#e6a23c" style="vertical-align:-2px;margin-left:4px">
                  <Warning />
                </el-icon>
              </el-tooltip>
            </template>
          </template>
        </el-table-column>
        <el-table-column v-if="!isTeacher" :label="t('操作')" width="170" align="center">
          <template #default="{ row }">
            <PositionAction v-if="row.it" :g="row.it.g" :s="row.it.p" :show-why="false" />
          </template>
        </el-table-column>
      </el-table>
      <div v-if="showPickRule" class="dim pick-rule">{{ PICK_RULE }}</div>
    </el-card>

    <!-- 变更记录全文：卡片上只留标题行，字段改动都在这里看 -->
    <el-dialog v-model="logDialog" :title="t('变更记录')" width="560">
      <el-timeline v-if="act.change_logs?.length" style="padding-left:4px">
        <el-timeline-item v-for="l in act.change_logs" :key="l.id"
                          :timestamp="fmtDT(l.created_at)" placement="top"
                          :type="l.action === 'delete' ? 'danger' : 'primary'">
          <b style="font-size:13px">{{ l.actor_name }}</b>
          <span class="dim"> {{ l.summary }}</span>
          <div v-for="(ch, i) in l.changes" :key="i" class="dim" style="margin-top:4px">
            {{ t('{0}：{1} → {2}', [t(ch.label), ch.old || t('空'), '']) }}<b style="color:var(--el-color-primary)">{{ ch.new || t('空') }}</b>
          </div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else :description="t('暂无变更')" />
    </el-dialog>

    <!-- 统一分配详情：报名只锁时间，具体岗位截止后由负责人安排 -->
    <el-dialog v-model="groupDetail.visible" :title="t('{0} · 详情', [groupDetail.data ? slotName(groupDetail.data) : ''])" width="480">
      <el-descriptions v-if="groupDetail.data" :column="1" border>
        <el-descriptions-item :label="t('服务时段')"><SlotTime :g="groupDetail.data" /></el-descriptions-item>
        <el-descriptions-item :label="t('人数')">{{ t('已报名 {0} / {1}', [groupDetail.data.enrolled, groupDetail.data.capacity]) }}<span v-if="groupDetail.data.waitlist_count" class="dim">{{ t('（候补 {0} 人）', [groupDetail.data.waitlist_count]) }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('剩余名额')">{{ groupDetail.data.remaining }}</el-descriptions-item>
        <el-descriptions-item :label="t('条件要求')">{{ tTags(groupDetail.data.requirement_desc) }}</el-descriptions-item>
        <el-descriptions-item :label="t('说明')">{{ groupDetail.data.note || t('报名后统一安排具体岗位') }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 岗位详情 -->
    <el-dialog v-model="stationDetail.visible" :title="t('{0} · 岗位详情', [stationDetail.data?.name || ''])" width="480">
      <el-descriptions v-if="stationDetail.data" :column="1" border>
        <el-descriptions-item :label="t('服务时段')"><SlotTime :g="stationDetail.g" /></el-descriptions-item>
        <el-descriptions-item :label="t('服务地点')">{{ stationDetail.data.location || act.location || t('待定') }}</el-descriptions-item>
        <el-descriptions-item :label="t('名额')">
          {{ stationDetail.data.enrolled }}/{{ stationDetail.data.quota }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('条件要求')">{{ tTags(stationDetail.data.requirement_desc) }}</el-descriptions-item>
        <el-descriptions-item :label="t('服务任务')"><span class="pre">{{ stationDetail.data.service_task || '—' }}</span></el-descriptions-item>
        <el-descriptions-item :label="t('岗位要求')"><span class="pre">{{ stationDetail.data.position_requirement || '—' }}</span></el-descriptions-item>
        <el-descriptions-item :label="t('志愿服装')">{{ attireText(stationDetail.data) || t('不需要') }}</el-descriptions-item>
        <el-descriptions-item :label="t('报酬方式')">{{ payText(stationDetail.data) || t('志愿时长') }}</el-descriptions-item>
        <el-descriptions-item :label="t('培训内容')">
          <template v-if="stationDetail.data.training_visible">
            <span class="pre">{{ stationDetail.data.training_note || '—' }}</span>
          </template>
          <span v-else class="dim">{{ t('报名本岗位后可见') }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('候补名额')">
          {{ stationDetail.data.waitlist_count }}/{{ stationDetail.data.waitlist_quota }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('组长')">
          <template v-if="!stationDetail.data.needs_leader">
            <span class="dim">{{ t('本岗位不设组长') }}</span>
          </template>
          <template v-else-if="!stationDetail.data.training_visible">
            <span class="dim">{{ t('本岗位设组长，报名后可见组长信息') }}</span>
          </template>
          <template v-else>
            {{ stationDetail.data.leader_name || t('尚未任命') }}
            <div v-if="stationDetail.data.leader_contact" class="dim">{{ t('手机 {0} · 微信 {1}', [stationDetail.data.leader_contact.phone || '—', stationDetail.data.leader_contact.wechat || '—']) }}</div>
            <div v-else-if="stationDetail.data.leader_name" class="dim">{{ t('报名本岗位所在的组后可见联系方式') }}</div>
          </template>
        </el-descriptions-item>
      </el-descriptions>
      <MaterialList v-if="stationDetail.data?.attachments?.length"
                    :items="stationDetail.data.attachments"
                    :label="t('本岗位学习材料')" />
    </el-dialog>

    <!-- 报名确认：确认时段 + 表态是否接受岗位调剂 -->
    <el-dialog v-model="enrollDlg.visible" :title="t('确认报名')" width="480px"
               :before-close="guardEnroll" @opened="guardEnroll.snap">
      <template v-if="enrollDlg.shift">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item :label="t('岗位')">
            {{ slotName(enrollDlg.shift, enrollDlg.position) }}
            <div v-if="enrollDlg.shift.needs_assign" class="dim">{{ t('报名后统一安排具体岗位') }}</div>
          </el-descriptions-item>
          <el-descriptions-item :label="t('服务时段')">
            <SlotTime :g="enrollDlg.shift" />
          </el-descriptions-item>
          <el-descriptions-item v-if="enrollExtra.length" :label="t('还需参加')">
            <div v-for="s in enrollExtra" :key="s.id" style="line-height:1.6">
              {{ t('{0}：{1}', [t(s.kind_label), tTime(s.time_text)]) }}
              <span class="dim">{{ s.location || act.location || t('地点待定') }}</span>
              <div v-if="s.note" class="dim">{{ t('备注：{0}', [s.note]) }}</div>
            </div>
          </el-descriptions-item>
          <el-descriptions-item :label="t('结果')">
            <el-tag :type="willWaitlist ? 'warning' : 'success'" size="small">
              {{ t(willWaitlist ? '名额已满，将进入候补队列' : '名额充足，即选即得') }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <el-alert v-if="enrollExtra.length && !willWaitlist" type="info" show-icon
                  :closable="false"
                  :title="t('请确认这些时间你可到场，如时间冲突需向负责人请假。')" />

        <!-- 活动设了报名问卷：报名时一并答，答案跟着报名记录走。
             本活动别的报名答过的题不再问，只问这个岗位新增的 -->
        <el-form v-if="enrollQuestions.length" label-position="top" style="margin-top:12px">
          <QuestionFields ref="enrollFields" v-model="enrollDlg.answers"
                          :questions="enrollQuestions" :reused="act.my_answers || {}"
                          :upload-url="`/api/activities/${act.id}/apply/upload`" />
        </el-form>

        <div class="reassign-box">
          <el-checkbox v-model="enrollDlg.allowReassign">{{ t('接受岗位调剂') }}</el-checkbox>
          <div v-if="willWaitlist" class="dim" style="margin-top:4px">{{ t('候补可能被调到有空缺的岗位；负责人会看到你的意愿') }}</div>
          <div v-else class="dim" style="margin-top:4px">{{ t('仅在名额不足时可能进行调剂，负责人可看到你的意愿。') }}</div>
          <el-checkbox v-model="enrollDlg.leaderWilling" style="margin-top:8px">{{ t('愿意担任组长（如本岗位需要）') }}</el-checkbox>
          <div class="dim" style="margin-top:4px">{{ t('担任小组长的志愿者将根据实际工作额外记录志愿时数。') }}</div>
        </div>
      </template>
      <template #footer>
        <el-button @click="enrollDlg.visible = false">{{ t('再想想') }}</el-button>
        <el-button type="primary" @click="submitEnroll">{{ t(willWaitlist ? '加入候补' : '确认报名') }}</el-button>
      </template>
    </el-dialog>

    <ProfileDialog ref="profileDialog" />
    <GroupQrDialog ref="groupQr" />
  </div>
  <!-- 转发来的链接：不在招募范围内的人看到的不是 404，而是原因 -->
  <div v-else-if="blocked" class="page">
    <el-result icon="warning" :title="t('本活动不面向您招募')" :sub-title="blocked">
      <template #extra>
        <el-button type="primary" @click="$router.push('/')">{{ t('回活动广场') }}</el-button>
      </template>
    </el-result>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, conflictLine, fmtDT, withConflictConfirm } from '../api'
import { auth, MODE_COLORS } from '../store'
import ProfileDialog from '../components/ProfileDialog.vue'
import GroupQrDialog from '../components/GroupQrDialog.vue'
import ApplicationPanel from '../components/ApplicationPanel.vue'
import MaterialList from '../components/MaterialList.vue'
import QuestionFields from '../components/QuestionFields.vue'
import RefreshBar from '../components/RefreshBar.vue'
import PositionAction from '../components/PositionAction.vue'
import TrainingItem from '../components/TrainingItem.vue'
import SlotTime from '../components/SlotTime.vue'
import { useNarrow } from '../composables/useNarrow'
import { useCloseGuard } from '../composables/closeGuard'
import { Bell, BellFilled, Clock, Warning } from '@element-plus/icons-vue'
import { posName, t, tTags, tTime } from '../i18n'

const route = useRoute()
const router = useRouter()
const narrow = useNarrow()
const act = ref(null)
const blocked = ref('')
const loading = ref(false)
const loadError = ref('')
const groupDetail = ref({ visible: false, data: null })
const enrollDlg = ref({ visible: false, shift: null, position: null,
                        leaderWilling: false, allowReassign: true })
const guardEnroll = useCloseGuard(() => [enrollDlg.value.answers, enrollDlg.value.allowReassign, enrollDlg.value.leaderWilling])
/** 已提交、结果还没回来的报名：场次 id → 岗位 id（没有岗位记 ''）。
 *
 * 普通岗位一岗一场，场次 id 就是这条岗位；统一分配是那一批。
 *
 * 抢报时一个请求要按先后排十几秒。弹窗要是一直开着等结果，这段时间别的岗位
 * 也点不了；所以确认即关窗，按钮原地显示「排队中…」，可以接着报别的岗位，
 * 各自出结果各自提示。同一条在结果回来之前不能再点——后端也会按重复报名
 * 拒掉，但那是白排一轮队。
 */
const queued = ref({})       // vuecheck: script-only（模板经 queuedShift / queuedHere 读它）
const QUEUE_NOTE = computed(() => t('排队中，请勿刷新'))  // vuecheck: script-only（经 enrollCtx 给 PositionAction）
const queuedShift = g => g.id in queued.value
const queuedHere = (g, s) => queued.value[g.id] === (s ? s.id : '')
// 挂了适用岗位的题只问报这个岗位的人；适用校区、学段按个人信息里填的算，
// 和后端校验（FormQuestion.fits）同一口径
const enrollQuestions = computed(() => (act.value?.questions || []).filter(q => {
  const scope = q.applies_to_positions || []
  const campuses = q.applies_to_campuses || []
  const stages = q.applies_to_stages || []
  return (!scope.length || scope.includes(enrollDlg.value.position?.id))
    && (!campuses.length || campuses.includes(auth.user?.campus))
    && (!stages.length || stages.includes(auth.user?.stage))
}))
const willWaitlist = computed(() => {
  const { shift: g, position } = enrollDlg.value
  if (!g) return false
  return position ? position.remaining <= 0 : g.remaining <= 0
})
// 这次报名会连带哪几场培训/彩排——报之前就要看见，不能报完才发现撞车
const enrollExtra = computed(() => {
  const { shift: g, position } = enrollDlg.value
  if (!g?.attached?.length) return []
  return g.attached.filter(s => !s.applies_to_positions.length
    || (position && s.applies_to_positions.includes(position.id)))
})
const stationDetail = ref({ visible: false, data: null, g: null })

const modeColor = m => MODE_COLORS[m]?.color
// 与广场共用同一套：状态按时间算，颜色也要一致，否则两处对同一个活动说法不同
const STATUS_TYPES = {
  draft: 'warning', upcoming: 'primary', published: 'success',
  closed: 'warning', ongoing: 'danger', finished: 'info',
}
const statusType = computed(() => STATUS_TYPES[act.value?.display_status] || 'info')
// 负责人正在改安排：报名、退选、确认转正后端一律拦，按钮跟着灰掉
const paused = computed(() => !!act.value?.editing)
// 活动地点的导航链接（高德网页版标点，见模板里那段说明）
const navUrl = computed(() => {
  const p = act.value?.location_point
  if (!p) return ''
  // 照高德文档的写法拼「经度,纬度」，逗号不转义（URLSearchParams 会把它转成 %2C）
  const name = encodeURIComponent(act.value.location || act.value.title)
  return `https://uri.amap.com/marker?position=${p.lng},${p.lat}&name=${name}`
       + '&coordinate=wgs84&callnative=1&src=yvruc'
})
// 老师不参与志愿报名：安排照看，报名、申请入口都不给
const isTeacher = computed(() => auth.user?.user_type === 'teacher')
// 报名规则：全部岗位列完之后说一次
const PICK_RULE = computed(() => t('同一活动里时间重叠的岗位只能报一个。候补将在名额释放后转正，转正后需在确认窗口内确认（2 小时，夜间 23:00–8:00 不计时）。'))
const showPickRule = computed(() => !isTeacher.value && items.value.length > 0)

function deadlineTs(g) {
  return new Date(g.my_enrollment.confirm_deadline.replace(' ', 'T').slice(0, 19)).getTime()
}
// 培训/彩排请假：服务岗位不来是退选（名单锁定后退出会记一次），这里只管培训/彩排
async function toggleExcuse(s) {
  if (s.excused) {
    await api.post(`/api/activities/enrollments/${s.my_enrollment_id}/excuse`,
                   { excused: false })
    ElMessage.success(t('已销假'))
    return load()
  }
  let reason = ''
  try {
    const r = await ElMessageBox.prompt(
      t('「{0}」{1}。请说明无法参加的原因，负责人会看到。', [s.name, tTime(s.time_text)]),
      t('{0}请假', [t(s.kind_label)]),
      { inputPlaceholder: t('如：与专业课时间冲突'), inputPattern: /.{2,}/,
        inputErrorMessage: t('请简单写一下原因'),
        confirmButtonText: t('提交请假'), cancelButtonText: t('再想想') })
    reason = r.value
  } catch { return }
  await api.post(`/api/activities/enrollments/${s.my_enrollment_id}/excuse`,
                 { excused: true, reason })
  ElMessage.success(t('已请假，负责人会看到'))
  load()
}

/** 活动时间：说法统一由后端拼（Activity.headline_time，只有一个时段时带钟点）；
 * 整体起止时再提一句去看下方岗位，不然志愿者看着起止日期以为要连着来这么多天 */
const actTime = computed(() => ({
  text: tTime(act.value?.headline_text) || t('待定'),
  more: !!act.value?.headline_more,
}))

// —— 岗位怎么列 ——
//
// 志愿者只和岗位打交道：普通岗位各自带一个场次（一岗一场，g 就是它的时段、
// 锁定、暂停），只有统一分配挂多个岗位，报名从「待分配」进。下面把每条可报的
// 东西摊成一个 item：{ g, p, assign }，按时间看、按岗位看都由它排出来。

/** 统一分配的叫法：「上午场志愿者」，后面再跟一句「报名后统一安排具体岗位」 */
function assignName(g) {
  return g.name.endsWith('志愿者') ? g.name : t('{0}志愿者', [g.name])
}
/** 一条报名在说哪里：普通岗位写岗位名，统一分配写它的叫法 */
function slotName(g, p) {
  if (g.needs_assign) return assignName(g)
  return p?.name || g.positions.find(x => !x.is_placeholder)?.name || g.name
}

const items = computed(() => {
  const out = []
  for (const g of act.value?.shifts || []) {
    if (g.needs_assign) {
      // 分到真实岗位的人看到的是自己的岗位；其余人看到的是报名入口「待分配」
      const mine = myPosition(g)
      const p = mine && !mine.is_placeholder ? mine : g.positions.find(x => x.is_placeholder)
      if (p) out.push({ key: g.id, g, p, assign: true, name: assignName(g) })
      continue
    }
    for (const p of g.positions) {
      if (!p.is_placeholder) out.push({ key: p.id, g, p, assign: false, name: p.name })
    }
  }
  return out.sort((a, b) => a.g.start_time.localeCompare(b.g.start_time)
                            || a.name.localeCompare(b.name, 'zh'))
})

/** 按岗位看：同名的普通岗位合成一组（可以分别报的几个时段），统一分配各自一组 */
const roles = computed(() => {  // vuecheck: script-only（表格行与卡片由它排出）
  const by = new Map()
  for (const it of items.value) {
    const k = it.assign ? `assign:${it.g.id}` : `post:${it.name}`
    if (!by.has(k)) by.set(k, { key: k, name: it.name, slots: [] })
    by.get(k).slots.push(it)
  }
  return [...by.values()]
})

/** 培训/彩排：活动上的那份没有「我要不要去、请没请假」，这些在各场次的
 * attached 里；取我要去的那一份，没有就取任一份 */
const trainings = computed(() => {  // vuecheck: script-only
  const m = new Map((act.value?.trainings || []).map(x => [x.id, x]))
  for (const g of act.value?.shifts || []) {
    for (const s of g.attached || []) {
      const cur = m.get(s.id)
      if (cur && (!('mine' in cur) || (!cur.mine && s.mine))) m.set(s.id, s)
    }
  }
  return [...m.values()].sort((a, b) => a.start_time.localeCompare(b.start_time))
})
/** 这场培训叫不叫这条岗位的人：没圈岗位 = 全体；统一分配圈了其中任一岗位就算 */
function calls(tr, it) {
  const ids = tr.applies_to_positions
  if (!ids.length) return true
  return it.assign ? it.g.positions.some(p => ids.includes(p.id)) : ids.includes(it.p.id)
}
const callsOf = it => trainings.value.filter(tr => calls(tr, it))

/** 按岗位看时培训挂在哪：只叫组里某几个时段的，跟在那几个时段后面；叫整组的
 * （含全体参加）放在组的最后 */
function roleLayout(r) {
  const partial = new Map(r.slots.map(x => [x.key, []]))
  const whole = []
  for (const tr of trainings.value) {
    const hit = r.slots.filter(x => calls(tr, x))
    if (!hit.length) continue
    if (hit.length === r.slots.length) whole.push(tr)
    else hit.forEach(x => partial.get(x.key).push(tr))
  }
  return { partial, whole }
}

/** 宽屏表格的行。type：slot 一条岗位；training 一场培训，挂在叫到的岗位下面。
 * 按岗位看时组的第一行带 span（岗位格往下合并几行），其余行 span 为 0 */
const tableRows = computed(() => {
  if (detailView.value === 'time') {
    const rows = []
    for (const it of [...items.value].sort((a, b) => a.g.start_time.localeCompare(b.g.start_time))) {
      rows.push({ type: 'slot', key: it.key, it, name: it.name, count: 1 })
      for (const tr of callsOf(it)) rows.push({ type: 'training', key: `${it.key}:${tr.id}`, tr })
    }
    return rows
  }
  const rows = []
  for (const r of roles.value) {
    const { partial, whole } = roleLayout(r)
    const first = rows.length
    for (const it of r.slots) {
      rows.push({ type: 'slot', key: it.key, it, span: 0 })
      for (const tr of partial.get(it.key)) {
        rows.push({ type: 'training', key: `${it.key}:${tr.id}`, tr, span: 0 })
      }
    }
    for (const tr of whole) rows.push({ type: 'training', key: `${r.key}:${tr.id}`, tr, span: 0 })
    Object.assign(rows[first], { span: rows.length - first, name: r.name, count: r.slots.length })
  }
  return rows
})
/** 手机卡片：按时间看一条岗位一张卡，下面挂它要参加的培训；
 * 按岗位看一组一张卡，培训的位置同宽屏 */
const cardRows = computed(() => {
  if (detailView.value === 'time') {
    return tableRows.value.filter(row => row.type === 'slot').map(row => (
      { type: 'card', key: row.key, name: row.name,
        slots: [{ ...row.it, trainings: callsOf(row.it) }] }))
  }
  const rows = []
  for (const r of roles.value) {
    const { partial, whole } = roleLayout(r)
    rows.push({ type: 'card', key: r.key, name: r.name, trainings: whole,
                slots: r.slots.map(it => ({ ...it, trainings: partial.get(it.key) })) })
  }
  return rows
})
// 老师不报名，没有「操作」列
const ncols = computed(() => (isTeacher.value ? 5 : 6))  // vuecheck: script-only
/** 培训行横跨整行：按时间看从第一列起，按岗位看从时段列起（左边是合并的岗位格） */
function rowSpan({ row, columnIndex }) {
  if (detailView.value === 'role') {
    if (columnIndex === 0) return row.span ? [row.span, 1] : [0, 0]
    if (row.type === 'training') return columnIndex === 1 ? [1, ncols.value - 1] : [0, 0]
    return [1, 1]
  }
  if (row.type === 'training') return columnIndex === 0 ? [1, ncols.value] : [0, 0]
  return [1, 1]
}
const rowClass = ({ row }) => (row.it && isMine(row.it) ? 'mine-row' : '')
const isMine = it => myStationId(it.g) === it.p.id

/** 岗位名旁的标签：组长、服装都是这条岗位自己的 */
function nameTags(it) {
  if (it.assign) return []
  const out = []
  if (it.p.needs_leader) out.push({ text: t('设组长'), type: 'warning' })
  if (it.p.attire !== 'none') out.push({ text: t('服装{0}', [t(it.p.attire_label)]), type: 'primary' })
  if (it.p.pay_optional) out.push({ text: t('可选劳务'), type: 'warning' })
  if (it.p.material_count) {
    out.push({ text: t('材料 {0}{1}', [it.p.material_count, it.p.training_visible ? '' : t('·报名后可看')]),
               type: it.p.training_visible ? 'success' : 'info' })
  }
  return out
}
const slotPlace = it => it.p.location || it.g.location || act.value?.location || t('地点待定')

// 活动在报名期、已开始报名：这时某条岗位不收人了，才值得写它几点截止的
const regOpen = computed(() => act.value?.status === 'published' && !act.value.opens_in)

// 两种看法：默认按时间看，切过之后记住本人上次选的
const VIEW_KEY = 'yvruc_detail_view'
function readView() {
  try { return localStorage.getItem(VIEW_KEY) === 'role' ? 'role' : 'time' } catch { return 'time' }
}
const detailView = ref(readView())
watch(detailView, v => {
  try { localStorage.setItem(VIEW_KEY, v) } catch { /* 存不了就每次默认按时间看 */ }
})

// 我报了的：每条一张卡，摊开组长、培训内容与材料
const myShifts = computed(() => (act.value?.shifts || []).filter(g => g.my_enrollment))
const MY_STATUS = {
  enrolled: { type: 'success' },
  waitlisted: { type: 'warning' },
  pending_confirm: { type: 'danger' },
}
function myStatusText(g) {
  const e = g.my_enrollment
  if (e.status === 'waitlisted') return t('候补第 {0} 位', [e.waitlist_position])
  if (e.status === 'pending_confirm') return t('候补转正，待确认')
  return t('已报名')
}

function myPosition(g) {
  const pid = g.my_enrollment?.position_id
  return pid ? g.positions.find(p => p.id === pid) || null : null
}

/** 「您的岗位」后面的名字：统一分配里还没分到岗位的人显示「待分配」。 */
function myPositionName(g) {
  const p = myPosition(g)
  return p.is_placeholder ? t('待分配') : p.name
}

/** 服装、报酬的一句话（后端 attire_text / pay_text 的拼法），方式按语言翻，
 *  备注是负责人填的、原样接在后面。 */
function attireText(p) {
  if (!p || p.attire === 'none') return ''
  return p.attire_note ? t('{0}，{1}', [t(p.attire_label), p.attire_note]) : t(p.attire_label)
}
function payText(p) {
  if (!p?.pay_optional) return ''
  return p.pay_note ? t('{0}，{1}', [t('可选劳务'), p.pay_note]) : t('可选劳务')
}

function myStationId(g) {
  return g.my_enrollment?.position_id || ''
}
/** 开始之后后端不再收退选，退选和退出候补按钮都不摆出来 */
function canCancel(g) {
  return new Date(g.start_time.replace(' ', 'T')) > new Date()
}
/** 我报的这条岗位名单是否已锁定：此时退出会被记录 */
function myListLocked(g) {
  const sid = myStationId(g)
  return g.locked || !!g.positions.find(p => p.id === sid)?.list_locked
}
// 报名后筛选：录取前只有申请入口；录取之后才看岗位安排
const screenedApplying = computed(
  () => act.value?.recruit_mode === 'screened'
        && act.value.application?.mine?.status !== 'accepted')

/** 这条岗位现在还收不收人。
 *
 * 活动整体在报名期还不够：每条岗位在**自己开始时**就关闸（enroll_open 由后端
 * 按 min(活动报名截止, 开始时间) 算）。支教那种连办八周的活动，第三周了还开着
 * 报名很正常，但第一周那条早就不该再收人。
 */
function openFor(g) {
  return act.value?.status === 'published' && g.enroll_open
         && act.value.recruit_mode !== 'screened'
}
function closedText(g) {
  if (act.value?.recruit_mode === 'screened') return t('录取后由负责人安排')
  if (act.value?.status !== 'published') return t('不在报名期')
  if (act.value.opens_in) return t('未开始报名')
  return g.started ? t('已开始') : t('报名已截止')
}
function enrollText(g) {
  return t(g.remaining > 0 ? '立即报名' : '加入候补')
}
// 岗位的报名按钮（PositionAction）在几种视角里共用，动作和状态从这里给
provide('enrollCtx', { deadlineTs, confirmPromo, cancelEnroll, canCancel, queuedShift,
                       queuedHere, openFor, doEnroll, enrollText, closedText, paused,
                       queueNote: QUEUE_NOTE })

/** 点岗位名：普通岗位看岗位详情，统一分配看这一批的人数与说明 */
function showDetail(it) {
  if (it.assign) groupDetail.value = { visible: true, data: it.g }
  else stationDetail.value = { visible: true, data: it.p, g: it.g }
}

/** 倒计时目标（本地时间戳）。
 *
 * 用后端给的「还剩多少毫秒」加上收到响应的此刻，而不是直接 parse reg_start：
 * 客户端时钟偏几分钟，倒计时就会比服务端早到或晚到，按钮跟着错。毫秒数是
 * 服务端向上取整算的，再加上响应路上那点时间，归零时服务端一定已经到点——
 * 只会晚一点，不会早。
 */
const opensAt = ref(0)
const openFormat = computed(
  () => (act.value?.opens_in > 86400 ? t('DD 天 HH:mm:ss') : 'HH:mm:ss'))

// 这份详情上次拉成功的时刻：报名、退选之后也走 load 重拉，刷新条上的时刻跟着走
const loadedAt = ref(0)

// 变更记录全文的弹窗：右边卡片只留标题行，点「查看全部」到这里看
const logDialog = ref(false)

/** 右栏两块的高度跟着左栏的活动信息卡片走：变更记录在上，时间冲突占剩下的
 * 高度并内部滚动。手机上两栏已经拉直，不限高（见样式里的 .side-col）。 */
const infoCardRef = ref(null)
const sideColStyle = ref({})
let infoRO = null
function syncSideHeight() {
  const h = infoCardRef.value?.$el?.getBoundingClientRect().height || 0
  sideColStyle.value = narrow.value || !h ? {} : { height: `${h}px` }
}
watch(act, () => {
  infoRO?.disconnect()
  infoRO = null
  const el = infoCardRef.value?.$el
  if (!el) return
  syncSideHeight()
  infoRO = new ResizeObserver(syncSideHeight)
  infoRO.observe(el)
}, { flush: 'post' })
watch(narrow, syncSideHeight, { flush: 'post' })

async function load() {
  loading.value = true
  let d
  try {
    d = await api.get(`/api/activities/${route.params.id}`, { silent: true })
  } catch (e) {
    if (e.status === 403) { blocked.value = e.message; return }
    // 已经有内容时刷新失败只弹一句，别把整页换成错误；还没有内容才占住整页
    if (!act.value) loadError.value = e.message || t('请稍后重试')
    else ElMessage.error(e.message)
    return
  } finally {
    loading.value = false
  }
  loadError.value = ''
  const at = Date.now()
  // 每场的截止也换成本地时间戳，本地开闸时拿它判断这一场还收不收人
  for (const g of d.shifts || []) g.closesAt = at + (g.closes_in_ms ?? Infinity)
  act.value = d
  loadedAt.value = at
  const ms = d.opens_in_ms ?? d.opens_in * 1000
  opensAt.value = ms ? at + ms : 0
}
onMounted(load)

/** 「返回」：从通知等外部链接打开时这一窗没有上一页，router.back() 原地不动 */
function goBack() {
  if (window.history.state?.back) { router.back(); return }
  router.push('/square')
}

/** 倒计时归零：就地开闸，不等重拉。
 *
 * 要是归零时重拉一次、等后端说「开了」按钮才亮，谁的网快谁先亮，抢报拼的
 * 就成了刷新速度。就地开闸，每个人的按钮在同一刻亮起，规则与后端相同：报名已开始、
 * 且没过本场截止（min(活动报名截止, 本场开场)）。点下去的报名请求由后端按
 * 精确时间再判一次，谁报得上始终以数据库为准。
 *
 * 人数随后再拉，错开 0–3 秒：几百人同一毫秒重拉只会让服务端排队。重拉不影响
 * 按钮——那时后端也已经说开了。
 */
let refreshTimer = null
function unlock() {
  const a = act.value
  opensAt.value = 0
  if (!a) return
  const at = Date.now()
  a.opens_in = 0
  a.opens_in_ms = 0
  if (a.display_status === 'upcoming') {
    a.display_status = 'published'
    a.display_label = '报名中'
  }
  for (const g of a.shifts || []) g.enroll_open = at <= g.closesAt
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(load, Math.random() * 3000)
}
onBeforeUnmount(() => {
  clearTimeout(refreshTimer)
  clearTimeout(subTimer)
  infoRO?.disconnect()
})

/** 订阅报名开始提醒。
 *
 * 离报名开始不到 reg_reminder_minutes 分钟时按钮收起（subClosed）：提醒在
 * 开始前那几分钟发，再订就赶不上了，后端也会拒。到点用定时器收，不等重拉。
 * 已订阅的按钮鼠标移上去显示「取消订阅」，手机上没有悬停，直接写在按钮上。
 */
const subClosed = ref(true)
const subBusy = ref(false)
const subHover = ref(false)
let subTimer = null
function armSubscribe() {
  clearTimeout(subTimer)
  const lead = (act.value?.reg_reminder_minutes ?? 3) * 60000
  const left = opensAt.value ? opensAt.value - lead - Date.now() : 0
  subClosed.value = left <= 0
  // setTimeout 超过 2^31-1 毫秒（约 24.8 天）会立刻触发
  if (left > 0 && left < 2 ** 31 - 1) {
    subTimer = setTimeout(() => { subClosed.value = true }, left)
  }
}
watch(opensAt, armSubscribe)
const subLabel = computed(() => {
  if (!act.value?.subscribed) return t('订阅开始提醒')
  if (narrow.value) return t('已订阅 · 点此取消')
  return subHover.value ? t('取消订阅') : t('已订阅')
})
async function toggleSubscribe() {
  const a = act.value
  const url = `/api/activities/${a.id}/subscription`
  subBusy.value = true
  try {
    if (a.subscribed) {
      await api.del(url)
      a.subscribed = false
      ElMessage.success(t('已取消订阅'))
    } else {
      const r = await api.post(url)
      a.subscribed = true
      ElMessage.success(t('已订阅，将在报名开始前 {0} 分钟提醒你', [r.lead_minutes]))
    }
  } finally {
    subBusy.value = false
  }
}

function doEnroll(g, position) {
  // 答过的题先放进答案：不再露出，但后面的题的显示条件要拿它们判断
  enrollDlg.value = { visible: true, shift: g, position, allowReassign: true,
                      leaderWilling: false,
                      answers: structuredClone(act.value.my_answers || {}) }
}

const enrollFields = ref(null)
async function submitEnroll() {
  const { shift: g, position, allowReassign, leaderWilling } = enrollDlg.value
  if (queuedShift(g)) return
  let answers = {}
  if (enrollFields.value) {
    const got = enrollFields.value.collect()
    if (got.error) return ElMessage.warning(got.error)
    answers = got.answers
  }
  // 确认即关窗、按钮转「排队中…」，结果回来再提示（见 queued）
  enrollDlg.value.visible = false
  queued.value = { ...queued.value, [g.id]: position ? position.id : '' }
  try {
    // 错误自己提示：同时排着好几条岗位时，得说清是哪一条没报上。
    // 时段冲突先问一句，确认了才带 confirm_conflict 重发
    const d = await withConflictConfirm(confirm => api.post(
      `/api/activities/${act.value.id}/shifts/${g.id}/enroll`,
      { position_id: position ? position.id : null,
        allow_reassign: allowReassign, leader_willing: leaderWilling, answers,
        confirm_conflict: confirm },
      { skipErrorToast: true }),
    { title: t('「{0}」时间冲突', [slotName(g, position)]), ask: t('仍要报名？'), activityId: act.value.id })
    if (!d) return
    ElMessage.success(t('「{0}」{1}', [slotName(g, position), t(d.message)]))
    groupQr.value.open({ qr: d.contact_group_qr, title: act.value.title })
  } catch (e) {
    // 资料未完善被拦截：提示后弹出资料表单，补全后即可报名
    if (e?.data?.code === 'PROFILE_INCOMPLETE') {
      try {
        await ElMessageBox.alert(
          t('报名前需先完善个人信息，便于后期联络与志愿时长录入。'),
          t('需先完善个人信息'),
          { type: 'warning', confirmButtonText: t('去完善') })
      } catch { /* 关闭提示也照样打开表单 */ }
      openProfileDialog()
    } else if (e.status !== 401) {     // 401 已经统一提示并跳去登录了
      ElMessage.error(t('「{0}」{1}', [slotName(g, position), e.message]))
    }
  } finally {
    // 先拉到新数据再撤「排队中」：反过来的话按钮会闪回「选中」，还能再点一次
    await load()
    const rest = { ...queued.value }
    delete rest[g.id]
    queued.value = rest
  }
}

// 调剂意愿随时可改：它只是给负责人的参考
async function toggleReassign(g) {
  const enr = g.my_enrollment
  const d = await api.patch(
    `/api/activities/enrollments/${enr.enrollment_id}/reassign-pref`,
    { allow_reassign: !enr.allow_reassign })
  enr.allow_reassign = d.allow_reassign
  ElMessage.success(t(d.allow_reassign ? '已设为接受岗位调剂' : '已设为不接受岗位调剂'))
}

async function cancelEnroll(g) {
  const enrId = g.my_enrollment.enrollment_id
  const locked = g.my_enrollment.status === 'enrolled' && myListLocked(g)
  try {
    await ElMessageBox.confirm(
      t(locked ? '名单已锁定，此时退出会被记录一次。确定退出吗？' : '确定退选吗？'),
      t('确认退选'), { type: 'warning', confirmButtonText: t('确定'), cancelButtonText: t('取消') })
  } catch { return }
  const d = await api.del(`/api/activities/enrollments/${enrId}`)
  ElMessage[d.effect === 'locked' ? 'warning' : 'success'](t(d.message))
  load()
}

async function confirmPromo(g) {
  const d = await api.post(
    `/api/activities/enrollments/${g.my_enrollment.enrollment_id}/confirm-promotion`)
  ElMessage.success(t(d.message))
  groupQr.value.open({ qr: d.contact_group_qr, title: act.value.title })
  load()
}

const profileDialog = ref(null)
const groupQr = ref(null)
const openProfileDialog = () => profileDialog.value.open(true)
</script>

<style scoped>
.pick-rule { margin-top: 12px; }
/* 页头：宽屏标题与标签同一行，窄屏标签落到下一行 */
.hd-title { font-weight: 600; }
.hd-tags { display: inline-flex; gap: 8px; margin-left: 12px; vertical-align: middle; }
@media (max-width: 860px) {
  .hd-title { display: block; font-size: 16px; line-height: 1.5; }
  .hd-tags { display: flex; flex-wrap: wrap; margin: 4px 0 0; }
}

/* 报名开始倒计时：一行放得下就一行，窄屏自然换行 */
.reg-countdown {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin: 12px 0 0; padding: 8px 12px; font-size: 13px;
  border-radius: 4px; background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.reg-countdown :deep(.el-statistic__content) {
  font-size: 15px; font-weight: 600; color: var(--el-color-primary);
}
.reg-countdown .sub-btn { margin-left: auto; }
/* 已订阅：灰底，和广场卡片上的「已订阅」一个样子 */
.reg-countdown .sub-btn.sub-on {
  --el-button-bg-color: #f0f2f5; --el-button-border-color: #f0f2f5;
  --el-button-text-color: #909399;
  --el-button-hover-bg-color: #e9ecf1; --el-button-hover-border-color: #e9ecf1;
  --el-button-hover-text-color: #606266;
}
@media (max-width: 860px) {
  .reg-countdown .sub-btn { width: 100%; margin-left: 0; }
}

/* —— 窄屏的岗位卡片 —— */
.m-shifts { display: flex; flex-direction: column; gap: 12px; }
.m-shift :deep(.el-card__body) { padding: 14px; }
.m-shift-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.m-shift-name, .m-shift-name :deep(.el-link__inner) { font-size: 15px; font-weight: 600; }
.m-quota { white-space: nowrap; }
.m-pos {
  border: 1px solid #e4e7ed; border-radius: 4px; padding: 10px 12px; margin-top: 8px;
}
.m-pos.mine { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.m-pos.blocked { opacity: .7; }
.m-pos-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.m-pos-meta { margin-top: 4px; line-height: 1.6; }
.m-pos-act { margin-top: 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.m-pos-act-wrap { margin-top: 8px; }
.queue-note { margin-top: 4px; font-size: 12px; color: #909399; }
.m-reassign { margin-top: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.req-blocked { color: #e6a23c; }
/* 我报的岗位：一条一张卡，排在岗位表上面 */
.mine-list { display: flex; flex-direction: column; gap: 12px; margin-bottom: 12px; }
.mine-card { border-left: 3px solid var(--el-color-success); }
.mine-title { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.mine-note { margin: 4px 0; font-size: 13px; line-height: 1.6; color: #606266;
             white-space: pre-wrap; }
.reassign-box { margin-top: 12px; padding: 10px 12px; border-radius: 4px; background: #f7f8fa; }
.reassign-box .dim { margin: 8px 0 0; font-size: 13px; line-height: 1.5; }

/* 活动级的要求与保障 */
.side-note {
  border-left: 3px solid var(--el-color-warning); background: #fdf6ec;
  padding: 8px 12px; border-radius: 0 4px 4px 0; margin-bottom: 8px;
}
.side-note.benefit { border-left-color: var(--el-color-success); background: #f0f9eb; }
.side-note b { font-size: 13px; }
.side-note p { margin: 4px 0 0; font-size: 13px; line-height: 1.6;
               white-space: pre-wrap; color: #606266; }

/* 培训 / 彩排：跟在岗位后面的附加时段 */
.att-list { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.att-item {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 13px; padding: 6px 10px; border-radius: 4px; background: #f7f8fa;
}
.att-item.mine { background: var(--el-color-primary-light-9); }
/* 表格里培训独占一整行，整行只有它，不用再留列表上方那条间距 */
.att-in-row { margin-top: 0; }
/* 我报了的那一行 */
:deep(.mine-row) > td.el-table__cell { background: var(--el-color-primary-light-9); }

/* 右栏：变更记录只留标题行；时间冲突占剩下的高度，在这块里滚。
   两块合计的高度由脚本按左栏活动信息卡片算出来。 */
.side-col { display: flex; flex-direction: column; gap: 12px; }
.logs-card :deep(.el-card__body) { padding: 12px 16px; }
.logs-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.conflict-wrap { flex: 1; min-height: 0; overflow: auto; }
@media (max-width: 860px) {
  .side-col { height: auto !important; }
  .conflict-wrap { flex: none; overflow: visible; }
}

</style>
