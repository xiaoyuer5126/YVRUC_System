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
            {{ act.recruit_label }}
          </el-tag>
          <el-tag :type="statusType">{{ act.display_label }}</el-tag>
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
              {{ actTime.text }}<span v-if="actTime.more" class="dim">{{ t('（具体时段见下方场次）') }}</span>
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
              {{ act.recruit_scope_text }}
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

        <!-- 补记录活动只有管理者和审批线上的人看得到这一页：没有场次可报 -->
        <el-alert v-if="act.record_only" type="info" show-icon :closable="false"
                  :title="t('不公开招募（补记录）')"
                  :description="t('线下已组织过的活动，不在广场展示、不接受报名，没有场次与岗位；审批通过后由管理者导入名单补记时长。')" />

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
    </div>

    <!-- 下面两段各占一整行：报名申请、场次与岗位 -->
    <div v-if="screenedApplying && !isTeacher" class="section-title">{{ t('报名申请') }}<span class="dim">{{ t('勾选意向场次并填写报名问卷，截止后由负责人筛选录取； 场次与岗位见下方安排') }}</span></div>
    <!-- 名额是抢的：刷新按钮就放在名额上方，别让人翻回页头去找 -->
    <div v-else class="section-title">{{ t('岗位安排') }}<span class="dim">{{ t('点击场次、岗位名称可查看详细安排') }}</span>
      <RefreshBar :load="load" :at="loadedAt" size="small" style="margin-left:auto" /></div>

    <!-- 报名后筛选：先是申请入口，下面照旧列场次与岗位——
         审批人要在同一页看完整安排，学生也要先知道自己在报什么 -->
    <ApplicationPanel v-if="screenedApplying && !isTeacher" :act="act" @changed="load"
                      style="margin-bottom:12px" />
    <div v-if="screenedApplying && !isTeacher" class="section-title">{{ t('场次与岗位') }}<span class="dim">{{ t('仅供了解；录取后由负责人安排到具体岗位') }}</span>
      <RefreshBar :load="load" :at="loadedAt" size="small" style="margin-left:auto" /></div>

    <!-- 两种看法只是同一份数据换个排法，报名按钮是同一个（PositionAction） -->
    <div v-if="act.shifts.length" class="toolbar">
      <el-radio-group v-model="detailView" size="small">
        <el-radio-button value="shift">{{ t('按场次看') }}</el-radio-button>
        <el-radio-button value="role">{{ t('按岗位看') }}</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 按岗位看：同名岗位一张卡，列出它的各个时段 -->
    <div v-if="detailView === 'role'" class="m-shifts">
      <el-card v-for="r in roles" :key="r.name" shadow="never" class="m-shift">
        <div class="m-shift-head">
          <el-link class="m-shift-name" type="primary" underline="never"
                   @click="showStationDetail(r.slots[0].g, r.slots[0].p)">{{ r.name }}</el-link>
          <el-tag v-if="r.slots[0].p.needs_leader" size="small" type="warning"
                  effect="plain">{{ t('设组长') }}</el-tag>
          <el-tag v-if="r.slots[0].p.attire !== 'none'" size="small" type="primary"
                  effect="plain">{{ t('服装 {0}', [r.slots[0].p.attire_label]) }}</el-tag>
        </div>
        <div v-if="r.slots[0].p.service_task" class="dim pre">{{ r.slots[0].p.service_task }}</div>
        <div v-for="x in r.slots" :key="x.p.id" class="m-pos"
             :class="{ mine: myStationId(x.g) === x.p.id, blocked: x.p.eligible === false }">
          <div class="m-pos-head">
            <span>{{ x.g.name }} · {{ x.g.time_text }}
              <el-tag v-if="x.g.flexible_time" size="small" type="success" effect="plain" style="margin-left:4px">{{ t('时间灵活·可多次签到') }}</el-tag>
            </span>
            <span class="dim">{{ x.p.enrolled }}/{{ x.p.quota }}</span>
          </div>
          <div class="m-pos-meta dim">
            {{ x.p.location || x.g.location || act.location || t('地点待定') }}
            <template v-if="x.p.requirement_desc !== '不限'"> · {{ x.p.requirement_desc }}</template>
          </div>
          <div v-if="!isTeacher" class="m-pos-act-wrap">
            <PositionAction :g="x.g" :s="x.p" by-role />
          </div>
        </div>
        <div v-if="roleTrainings(r).length" class="att-list">
          <div v-for="t in roleTrainings(r)" :key="t.id" class="att-item">
            <el-tag size="small" effect="dark"
                    :type="t.kind === 'training' ? 'success' : 'warning'">{{ t.kind_label }}</el-tag>
            <span>{{ t.time_text }}</span>
            <span class="dim">{{ t.location || act.location }}</span>
          </div>
        </div>
      </el-card>
      <!-- 截止后统一分配的场次报名只选时间，归不到哪个岗位下 -->
      <el-card v-for="g in act.shifts.filter(x => x.needs_assign)" :key="g.id"
               shadow="never" class="m-shift">
        <div class="m-shift-head">
          <el-link class="m-shift-name" type="primary" underline="never"
                   @click="showGroupDetail(g)">{{ g.name }}</el-link>
          <el-tag size="small" type="warning" effect="plain">{{ t('按时间报名') }}</el-tag>
          <el-tag v-if="g.flexible_time" size="small" type="success" effect="plain">{{ t('时间灵活·可多次签到') }}</el-tag>
          <el-tag v-if="g.enroll_paused" size="small" type="danger" effect="plain">{{ t('暂停报名') }}</el-tag>
          <el-tag v-if="g.locked" size="small" type="info">{{ t('名单已锁定') }}</el-tag>
        </div>
        <div class="dim">{{ t('{0} · 岗位在报名截止后统一分配', [g.time_text]) }}</div>
        <div v-if="lockText(g)" class="dim">{{ lockText(g) }}</div>
        <div v-for="p in g.positions.filter(x => x.is_placeholder || myStationId(g) === x.id)"
             :key="p.id" class="m-pos" :class="{ mine: myStationId(g) === p.id }">
          <div class="m-pos-head">
            <span>{{ p.display_name }}</span>
            <span class="dim">{{ p.enrolled }}/{{ p.quota }}</span>
          </div>
          <div v-if="!isTeacher" class="m-pos-act-wrap">
            <PositionAction :g="g" :s="p" by-role />
          </div>
        </div>
      </el-card>
      <div v-if="showPickRule" class="dim">{{ PICK_RULE }}</div>
    </div>

    <!-- 窄屏：场次与岗位改成竖排卡片。手机上选岗位是最要紧的动作，
         不能藏在表格的展开箭头后面，所以岗位直接铺开。 -->
    <div v-else-if="narrow" class="m-shifts">
      <el-card v-for="g in act.shifts" :key="g.id" shadow="never" class="m-shift">
        <div class="m-shift-head">
          <el-link class="m-shift-name" type="primary" underline="never"
                   @click="showGroupDetail(g)">{{ g.name }}</el-link>
          <el-tag size="small" effect="plain"
                  :type="g.mode === 'assign' ? 'warning' : 'info'">
            {{ g.mode_label }}
          </el-tag>
          <el-tag v-if="g.flexible_time" size="small" type="success" effect="plain">{{ t('时间灵活·可多次签到') }}</el-tag>
          <el-tag v-if="g.started" size="small" type="info" effect="plain">{{ t('已开场') }}</el-tag>
          <el-tag v-if="g.enroll_paused" size="small" type="danger" effect="plain">{{ t('暂停报名') }}</el-tag>
          <el-tag v-if="g.locked" size="small" type="info">{{ t('名单已锁定') }}</el-tag>
        </div>
        <div class="dim">{{ g.time_text }}</div>
        <div v-if="lockText(g)" class="dim">{{ lockText(g) }}</div>
        <div v-if="act.status === 'published' && !act.opens_in && !g.enroll_open"
             class="dim">{{ t('本场报名已于 {0} 结束', [fmtDT(g.enroll_deadline)]) }}</div>
        <div class="m-quota">{{ t('已报名 {0} / {1}', [g.enrolled, g.capacity]) }}<span v-if="g.waitlist_count" class="dim">{{ t('· 候补 {0}', [g.waitlist_count]) }}</span>
          <el-progress :percentage="pct(g)" :stroke-width="5" :show-text="false"
                       style="margin-top:4px" />
        </div>
        <div v-if="wholeShiftTrainings(g).length" class="att-list">
          <TrainingItem v-for="s in wholeShiftTrainings(g)" :key="s.id" :s="s"
                        :location="act.location" @excuse="toggleExcuse" />
        </div>

        <!-- 报名即选岗位：每个岗位一块 -->
        <template v-if="g.positions.length">
          <div v-for="s in g.positions" :key="s.id" class="m-pos"
               :class="{ mine: myStationId(g) === s.id, blocked: s.eligible === false }">
            <div class="m-pos-head">
              <!-- 能点开详情的名字一律是 el-link：普通粗体字看不出能点 -->
              <el-link class="m-pos-name" type="primary" underline="never"
                       @click="showStationDetail(g, s)">{{ s.display_name }}</el-link>
              <span class="dim">{{ s.enrolled }}/{{ s.quota }}</span>
            </div>
            <div v-if="s.is_placeholder" class="m-pos-meta dim">
              {{ g.note || t('报名只锁定时段；岗位在报名截止后统一分配。') }}
            </div>
            <div v-else class="m-pos-meta dim">
              {{ s.location || act.location || t('地点待定') }}
              <template v-if="s.requirement_desc !== '不限'"> · {{ s.requirement_desc }}</template>
              <template v-if="s.needs_leader">{{ t('· 设组长') }}</template>
              <template v-if="s.attire !== 'none'">{{ t('· 服装 {0}', [s.attire_label]) }}</template>
              <template v-if="s.pay_optional">{{ t('· 报酬可选劳务') }}</template>
              <template v-if="s.material_count">{{ t('· 材料 {0}', [s.material_count]) }}</template>
            </div>
            <div v-if="!isTeacher" class="m-pos-act-wrap">
              <PositionAction :g="g" :s="s" />
            </div>
            <div v-if="positionTrainings(g, s).length" class="att-list">
              <TrainingItem v-for="t in positionTrainings(g, s)" :key="t.id" :s="t"
                            :location="act.location" @excuse="toggleExcuse" />
            </div>
          </div>
          <!-- 报了名却没有对应的岗位卡（补招时没指定岗位，或岗位被删） -->
          <div v-if="g.my_enrollment && !myStationId(g)" class="m-pos-act"
               style="margin-top:8px">
            <el-tag v-if="g.my_enrollment.status === 'enrolled'" type="success" size="small">{{ t('已报名（岗位待分配）') }}</el-tag>
            <el-tag v-else-if="g.my_enrollment.status === 'waitlisted'" type="warning" size="small">{{ t('候补第 {0} 位', [g.my_enrollment.waitlist_position]) }}</el-tag>
            <template v-else>
              <el-countdown format="HH:mm:ss" :value="deadlineTs(g)" />
              <el-button type="primary" size="small" :disabled="paused"
                         @click="confirmPromo(g)">{{ t('确认转正') }}</el-button>
            </template>
            <el-button v-if="g.my_enrollment.status !== 'pending_confirm' && canCancel(g)"
                       size="small" type="danger" plain
                       :disabled="paused"
                       @click="cancelEnroll(g)">
              {{ t(g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选') }}
            </el-button>
          </div>
        </template>

        <!-- 一个岗位都没有：只可能是管理者还没建，报不了 -->
        <template v-else>
          <p class="dim" style="margin:8px 0 0">{{ t('本场次尚未设置岗位，请联系负责人。') }}</p>
          <div v-if="!isTeacher" class="m-pos-act" style="margin-top:8px">
            <template v-if="g.my_enrollment">
              <el-tag v-if="g.my_enrollment.status === 'enrolled'" type="success" size="small">{{ t('已报名（岗位待分配）') }}</el-tag>
              <el-tag v-else-if="g.my_enrollment.status === 'waitlisted'" type="warning" size="small">{{ t('候补第 {0} 位', [g.my_enrollment.waitlist_position]) }}</el-tag>
              <template v-else>
                <el-countdown format="HH:mm:ss" :value="deadlineTs(g)" />
                <el-button type="primary" size="small" @click="confirmPromo(g)">{{ t('确认转正') }}</el-button>
              </template>
              <el-button v-if="g.my_enrollment.status !== 'pending_confirm' && canCancel(g)"
                         size="small" type="danger" plain
                         @click="cancelEnroll(g)">
                {{ t(g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选') }}
              </el-button>
            </template>
            <template v-else-if="queuedShift(g)">
              <el-button size="small" disabled loading>{{ t('排队中…') }}</el-button>
              <span class="queue-note">{{ QUEUE_NOTE }}</span>
            </template>
            <el-button v-else-if="openFor(g) && g.mode === 'assign'"
                       type="primary" size="small" @click="doEnroll(g, null)">
              {{ enrollText(g) }}
            </el-button>
            <el-tag v-else-if="!openFor(g)" type="info" size="small">
              {{ closedText(g) }}
            </el-tag>
          </div>
        </template>

        <!-- 已报名：把自己岗位的培训内容与材料摊开 -->
        <el-card v-if="myPosition(g)" shadow="never" class="mine-card">
          <div class="mine-title">{{ t('您的岗位：') }}<b>{{ myPosition(g).name }}</b>
            <span class="dim">{{ myPosition(g).location || act.location || t('地点待定') }}</span>
          </div>
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
            <b>{{ t('志愿服装：') }}</b>{{ myPosition(g).attire_text }}
          </p>
          <p v-if="myPosition(g).pay_text" class="mine-note">
            <b>{{ t('报酬方式：') }}</b>{{ t('{0}（核准时长时再选）', [myPosition(g).pay_text]) }}</p>
          <MaterialList v-if="myPosition(g).attachments?.length"
                        :items="myPosition(g).attachments" :label="t('本岗位学习材料')" />
        </el-card>

        <div v-if="g.my_enrollment" class="m-reassign">
          <span class="dim">{{ t('岗位调剂：') }}</span>
          <el-tag size="small" effect="plain"
                  :type="g.my_enrollment.allow_reassign ? 'success' : 'info'">
            {{ t(g.my_enrollment.allow_reassign ? '接受调剂' : '不接受调剂') }}
          </el-tag>
          <el-switch :model-value="g.my_enrollment.allow_reassign" size="small"
                     @change="toggleReassign(g)" />
        </div>
      </el-card>
      <div v-if="showPickRule" class="dim">{{ PICK_RULE }}</div>
    </div>

    <el-card v-else shadow="never">
      <el-table :data="act.shifts" row-key="id"
                :expand-row-keys="act.shifts.map(g => g.id)">
        <el-table-column type="expand" width="34">
          <template #default="{ row: g }">
            <div style="padding:8px 12px 8px 40px">
              <!-- 直接定二级：岗位表格 -->
              <!-- 报名一律选岗位。「截止后统一分配」的场次只有一条，
                   就是那个写明「暂不选岗位」的占位。 -->
              <template v-if="g.positions.length">
                <el-table :data="positionRows(g)" :span-method="posSpan" size="small" border>
                  <el-table-column :label="t('岗位')" min-width="150">
                    <template #default="{ row: s }">
                      <!-- 培训/彩排行：整行合并，用整张表的宽度放下整条 -->
                      <div v-if="s.attachment" class="att-list att-in-row">
                        <TrainingItem :s="s.attachment" :location="act.location" full
                                      @excuse="toggleExcuse" />
                      </div>
                      <template v-else>
                        <el-link type="primary" underline="never" style="font-weight:600"
                                 @click="showStationDetail(g, s)">{{ s.display_name }}</el-link>
                        <el-tag v-if="myStationId(g) === s.id" size="small" type="success"
                                effect="light" style="margin-left:8px">{{ t('已报名') }}</el-tag>
                        <el-tag v-if="s.needs_leader" size="small" type="warning"
                                effect="plain" style="margin-left:8px">{{ t('设组长 {0}', [s.leader_name ? `·${s.leader_name}` : '']) }}</el-tag>
                        <el-tag v-if="s.attire !== 'none'" size="small" type="primary"
                                effect="plain" style="margin-left:8px">{{ t('服装 {0}', [s.attire_label]) }}</el-tag>
                        <el-tag v-if="s.pay_optional" size="small" type="warning"
                                effect="plain" style="margin-left:8px">{{ t('可选劳务') }}</el-tag>
                        <el-tag v-if="s.material_count" size="small" effect="plain"
                                style="margin-left:8px"
                                :type="s.training_visible ? 'success' : 'info'">{{ t('材料 {0} {1}', [s.material_count, s.training_visible ? '' : '·报名后可看']) }}</el-tag>
                      </template>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('服务地点')" min-width="110">
                    <template #default="{ row: s }">{{ s.location || act.location }}</template>
                  </el-table-column>
                  <el-table-column :label="t('名额')" width="100">
                    <template #default="{ row: s }">
                      {{ s.enrolled }}/{{ s.quota }}
                      <el-tag v-if="s.remaining <= 0" size="small" type="danger"
                              effect="plain">{{ t('满') }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('要求')" min-width="130">
                    <template #default="{ row: s }">
                      <span :class="{ 'req-blocked': s.eligible === false }">
                        {{ s.requirement_desc }}
                      </span>
                      <el-tooltip v-if="s.eligible === false" placement="top"
                                  :content="s.ineligible_reason">
                        <el-icon color="#e6a23c" style="vertical-align:-2px;margin-left:4px">
                          <Warning />
                        </el-icon>
                      </el-tooltip>
                    </template>
                  </el-table-column>
                  <el-table-column v-if="!isTeacher" :label="t('操作')" width="170" align="center">
                    <template #default="{ row: s }">
                      <PositionAction :g="g" :s="s" :show-why="false" />
                    </template>
                  </el-table-column>
                </el-table>
              </template>
              <p v-else class="dim" style="margin:4px 0">{{ t('本场次尚未设置岗位，请联系负责人。') }}</p>

              <!-- 报了名却没有对应的岗位行（补招时没指定岗位，或岗位被删）：
                   退选与确认转正没有别的落脚点，放在这里 -->
              <div v-if="g.my_enrollment && !myStationId(g)" class="shift-bar">
                <el-tag v-if="g.my_enrollment.status === 'enrolled'" type="success">{{ t('已报名（岗位待分配）') }}</el-tag>
                <el-tag v-else-if="g.my_enrollment.status === 'waitlisted'" type="warning">{{ t('候补第 {0} 位', [g.my_enrollment.waitlist_position]) }}</el-tag>
                <template v-else>
                  <el-countdown format="HH:mm:ss" :value="deadlineTs(g)">
                    <template #prefix>
                      <el-tag type="danger" size="small">{{ t('转正确认剩') }}</el-tag>&nbsp;
                    </template>
                  </el-countdown>
                  <el-button type="primary" size="small" :disabled="paused"
                             @click="confirmPromo(g)">{{ t('确认转正') }}</el-button>
                </template>
                <el-button v-if="g.my_enrollment.status !== 'pending_confirm' && canCancel(g)"
                           size="small" type="danger" plain
                           :disabled="paused"
                           @click="cancelEnroll(g)">
                  {{ t(g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选') }}
                </el-button>
              </div>

              <!-- 调剂意愿：状态用标签显示，切换走右边的开关 -->
              <div v-if="g.my_enrollment" class="shift-bar">
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

              <!-- 已报名：把自己岗位的培训内容与材料直接摊开 -->
              <el-card v-if="myPosition(g)" shadow="never" class="mine-card">
                <div class="mine-title">{{ t('您的岗位：') }}<b>{{ myPosition(g).name }}</b>
                  <span class="dim">{{ myPosition(g).location || act.location || t('地点待定') }}</span>
                  <el-tag v-if="g.my_enrollment?.status === 'waitlisted'"
                          size="small" type="warning" effect="plain">{{ t('候补第 {0} 位', [g.my_enrollment.waitlist_position]) }}</el-tag>
                </div>
                <p v-if="myPosition(g).leader_name" class="mine-note">
                  <b>{{ t('组长：') }}</b>{{ myPosition(g).leader_name }}
                  <template v-if="myPosition(g).leader_contact">{{ t('（手机 {0} · 微信 {1}）', [myPosition(g).leader_contact.phone || '—', myPosition(g).leader_contact.wechat || '—']) }}</template>
                </p>
                <p v-if="myPosition(g).training_note" class="mine-note">
                  <b>{{ t('培训内容：') }}</b>{{ myPosition(g).training_note }}
                </p>
                <p v-if="myPosition(g).service_task" class="mine-note">
                  <b>{{ t('服务任务：') }}</b>{{ myPosition(g).service_task }}
                </p>
                <p v-if="myPosition(g).position_requirement" class="mine-note">
                  <b>{{ t('岗位要求：') }}</b>{{ myPosition(g).position_requirement }}
                </p>
                <p v-if="myPosition(g).attire_text" class="mine-note">
                  <b>{{ t('志愿服装：') }}</b>{{ myPosition(g).attire_text }}
                </p>
                <p v-if="myPosition(g).pay_text" class="mine-note">
                  <b>{{ t('报酬方式：') }}</b>{{ t('{0}（核准时长时再选）', [myPosition(g).pay_text]) }}</p>
                <MaterialList v-if="myPosition(g).attachments?.length"
                              :items="myPosition(g).attachments"
                              :label="t('本岗位学习材料（请提前学习）')" />
                <p v-if="!myPosition(g).training_note
                         && !myPosition(g).attachments?.length"
                   class="dim" style="margin:0">{{ t('负责人还没有上传本岗位的培训内容与材料。') }}</p>
              </el-card>

              <!-- 培训 / 彩排：报了这个场次就要一起去 -->
              <div v-if="wholeShiftTrainings(g).length" class="att-list">
                <TrainingItem v-for="s in wholeShiftTrainings(g)" :key="s.id" :s="s" full
                              :location="act.location" @excuse="toggleExcuse" />
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('场次')" min-width="180">
          <template #default="{ row }">
            <el-link type="primary" underline="never" style="font-weight:600"
                     @click="showGroupDetail(row)">{{ row.name }}</el-link>
            <el-tag size="small" effect="plain" style="margin-left:8px"
                    :type="row.mode === 'pick' ? 'info' : row.mode === 'assign' ? 'warning' : 'primary'">
              {{ row.mode_label }}
            </el-tag>
            <el-tag v-if="row.flexible_time" size="small" type="success" effect="plain"
                    style="margin-left:8px">{{ t('时间灵活·可多次签到') }}</el-tag>
            <el-tag v-if="row.enroll_paused" size="small" type="danger" effect="plain"
                    style="margin-left:8px">{{ t('暂停报名') }}</el-tag>
            <el-tag v-if="row.locked" size="small" type="info"
                    style="margin-left:8px">{{ t('名单已锁定') }}</el-tag>
            <div v-if="lockText(row)" class="dim">{{ lockText(row) }}</div>
            <!-- 每个场次在自己开场时就停止收人，活动整体可能还在报名期 -->
            <div v-if="act.status === 'published' && !act.opens_in && !row.enroll_open"
                 class="dim">{{ t('本场报名已于 {0} 结束', [fmtDT(row.enroll_deadline)]) }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('时间')" min-width="140" prop="time_text" />
        <el-table-column :label="t('已报名/名额')" min-width="150">
          <template #default="{ row }">
            <div>{{ row.enrolled }}/{{ row.capacity }}
              <span v-if="row.waitlist_count" class="dim">{{ t('· 候补 {0}', [row.waitlist_count]) }}</span>
            </div>
            <el-progress :percentage="pct(row)" :stroke-width="5" :show-text="false" />
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
            {{ t('{0}：{1} → {2}', [ch.label, ch.old || t('空'), '']) }}<b style="color:var(--el-color-primary)">{{ ch.new || t('空') }}</b>
          </div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else :description="t('暂无变更')" />
    </el-dialog>

    <!-- 场次详情 -->
    <el-dialog v-model="groupDetail.visible" :title="t('{0} · 场次详情', [groupDetail.data?.name || ''])" width="480">
      <el-descriptions v-if="groupDetail.data" :column="1" border>
        <el-descriptions-item :label="t('岗位模式')">{{ groupDetail.data.mode_label }}</el-descriptions-item>
        <el-descriptions-item :label="t('时间')">{{ groupDetail.data.time_text }}</el-descriptions-item>
        <el-descriptions-item :label="t('人数')">{{ t('已报名 {0} / {1}', [groupDetail.data.enrolled, groupDetail.data.capacity]) }}<span v-if="groupDetail.data.waitlist_count" class="dim">{{ t('（候补 {0} 人）', [groupDetail.data.waitlist_count]) }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('剩余名额')">{{ groupDetail.data.remaining }}</el-descriptions-item>
        <el-descriptions-item :label="t('条件要求')">{{ groupDetail.data.requirement_desc }}</el-descriptions-item>
        <el-descriptions-item :label="t('说明')">{{ groupDetail.data.note || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 岗位详情 -->
    <el-dialog v-model="stationDetail.visible" :title="t('{0} · 岗位详情', [stationDetail.data?.name || ''])" width="480">
      <el-descriptions v-if="stationDetail.data" :column="1" border>
        <el-descriptions-item :label="t('所属场次')">{{ stationDetail.groupName }}（{{ stationDetail.groupTime }}）</el-descriptions-item>
        <el-descriptions-item :label="t('服务地点')">{{ stationDetail.data.location || act.location || t('待定') }}</el-descriptions-item>
        <el-descriptions-item :label="t('名额')">
          {{ stationDetail.data.enrolled }}/{{ stationDetail.data.quota }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('条件要求')">{{ stationDetail.data.requirement_desc }}</el-descriptions-item>
        <el-descriptions-item :label="t('服务任务')"><span class="pre">{{ stationDetail.data.service_task || '—' }}</span></el-descriptions-item>
        <el-descriptions-item :label="t('岗位要求')"><span class="pre">{{ stationDetail.data.position_requirement || '—' }}</span></el-descriptions-item>
        <el-descriptions-item :label="t('志愿服装')">{{ stationDetail.data.attire_text || t('不需要') }}</el-descriptions-item>
        <el-descriptions-item :label="t('报酬方式')">{{ stationDetail.data.pay_text || t('志愿时长') }}</el-descriptions-item>
        <el-descriptions-item :label="t('培训内容')">
          <template v-if="stationDetail.data.training_visible">
            <span class="pre">{{ stationDetail.data.training_note || '—' }}</span>
          </template>
          <span v-else class="dim">{{ t('报名本场次后可见') }}</span>
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
               :close-on-click-modal="false">
      <template v-if="enrollDlg.shift">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item :label="t('场次')">
            {{ enrollDlg.shift.name }}（{{ enrollDlg.shift.time_text }}）
          </el-descriptions-item>
          <el-descriptions-item :label="t('岗位')">
            {{ enrollDlg.position ? enrollDlg.position.name
               : t(enrollDlg.shift.mode === 'assign' ? '将由负责人统一筛选分配' : '无需选择') }}
          </el-descriptions-item>
          <el-descriptions-item v-if="enrollExtra.length" :label="t('还需参加')">
            <div v-for="s in enrollExtra" :key="s.id" style="line-height:1.6">
              {{ s.kind_label }}：{{ s.time_text }}
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

        <!-- 活动设了报名问卷：报名时一并答，答案跟着报名记录走 -->
        <el-form v-if="enrollQuestions.length" label-position="top" style="margin-top:12px">
          <QuestionFields ref="enrollFields" v-model="enrollDlg.answers"
                          :questions="enrollQuestions"
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
        <el-button type="primary" @click="submitEnroll">{{ t('确认 {0}', [t(willWaitlist ? '加入候补' : '报名')]) }}</el-button>
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
import { useNarrow } from '../composables/useNarrow'
import { Clock, Warning } from '@element-plus/icons-vue'
import { t } from '../i18n'

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
/** 已提交、结果还没回来的报名：场次 id → 岗位 id（没有岗位记 ''）。
 *
 * 抢报时一个请求要按先后排十几秒。弹窗要是一直开着等结果，这段时间别的场次
 * 也点不了；所以确认即关窗，按钮原地显示「排队中…」，可以接着报别的场次，
 * 各自出结果各自提示。同一场次在结果回来之前不能再点——后端也会按重复报名
 * 拒掉，但那是白排一轮队。
 */
const queued = ref({})       // vuecheck: script-only（模板经 queuedShift / queuedHere 读它）
const QUEUE_NOTE = computed(() => t('排队中，请勿刷新'))
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
const stationDetail = ref({ visible: false, data: null, groupName: '', groupTime: '' })

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
// 选岗规则：各场次通用，全部场次（或岗位）列完之后说一次
const PICK_RULE = computed(() => t('同场次只能选一个岗位，候补将在名额释放后转正，转正后需在确认窗口内确认（2 小时，夜间 23:00–8:00 不计时）。'))
const showPickRule = computed(() =>
  !isTeacher.value && act.value?.shifts.some(g => g.positions.length))

function pct(g) {
  return Math.min(100, Math.round((g.enrolled / Math.max(1, g.capacity)) * 100))
}
function deadlineTs(g) {
  return new Date(g.my_enrollment.confirm_deadline.replace(' ', 'T').slice(0, 19)).getTime()
}
// 我在这个场次的岗位对象（报名后才有；统一分配的场次分配前为 null）
// 培训/彩排请假：服务场次不来是退选（名单锁定后退出会记一次），这里只管附属时段
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
      t('「{0}」{1}。请说明无法参加的原因，负责人会看到。', [s.name, s.time_text]),
      t('{0}请假', [s.kind_label]),
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

// 按场次看时培训/彩排挂在谁下面，就是叫谁去：没圈岗位的全体参加，挂在场次下；
// 圈了岗位的挂在那几个岗位下
const wholeShiftTrainings = g => (g.attached || []).filter(s => !s.applies_to_positions?.length)
const positionTrainings = (g, p) => (g.attached || [])
  .filter(s => s.applies_to_positions?.includes(p.id))

// 岗位子表的行：每个岗位一行，它下面的培训/彩排各占一行（带 attachment 标记）。
// 岗位行本身不再被培训撑高。
const positionRows = g => g.positions.flatMap(p =>
  [p, ...positionTrainings(g, p).map(t => ({ attachment: t }))])
// 培训行整行合并成一格，用整张表的宽度放内容；其余列收成 0 列不渲染。
// 老师不报名，没有「操作」列，列数少一列。
const posSpan = ({ row, columnIndex }) => {
  if (!row.attachment) return [1, 1]
  return columnIndex === 0 ? [1, isTeacher.value ? 4 : 5] : [0, 0]
}

/** 活动时间：所有场次只有同一段时间时写到钟点；有好几段时只写日期范围，
 * 再提一句去看场次，不然志愿者看着起止日期以为要连着来这么多天 */
const actTime = computed(() => {
  const spans = new Map()
  for (const g of act.value?.shifts || []) {
    for (const p of g.periods || []) spans.set(`${p.start_time}|${p.end_time}`, p.text)
  }
  if (spans.size === 1) return { text: [...spans.values()][0], more: false }
  return { text: act.value?.time_text || t('待定'), more: spans.size > 1 }
})

// —— 按岗位看 ——
const VIEW_KEY = 'yvruc_detail_view'
function readView() {
  try { return localStorage.getItem(VIEW_KEY) === 'role' ? 'role' : 'shift' } catch { return 'shift' }
}
const detailView = ref(readView())
watch(detailView, v => {
  try { localStorage.setItem(VIEW_KEY, v) } catch { /* 存不了就每次默认按场次看 */ }
})
/** 报名即选岗位的场次里，同名岗位合成一个「岗位」，列它的各个时段 */
const roles = computed(() => {
  const by = new Map()
  for (const g of act.value?.shifts || []) {
    if (g.needs_assign) continue
    for (const p of g.positions) {
      if (!by.has(p.name)) by.set(p.name, { name: p.name, slots: [] })
      by.get(p.name).slots.push({ g, p })
    }
  }
  for (const r of by.values()) r.slots.sort((a, b) => a.g.start_time.localeCompare(b.g.start_time))
  return [...by.values()]
})
const roleTrainings = r => (act.value?.trainings || []).filter(t => !t.applies_to_positions.length
  || r.slots.some(x => t.applies_to_positions.includes(x.p.id)))

function myPosition(g) {
  const pid = g.my_enrollment?.position_id
  return pid ? g.positions.find(p => p.id === pid) || null : null
}

function myStationId(g) {
  return g.my_enrollment?.position_id || ''
}
function myStationName(g) {
  const sid = myStationId(g)
  if (!sid) return ''
  for (const s of g.positions) if (s.id === sid) return s.name
  return ''
}
/** 场次开场后后端不再收退选，退选和退出候补按钮都不摆出来 */
function canCancel(g) {
  return new Date(g.start_time.replace(' ', 'T')) > new Date()
}
/** 「开场前 N 小时锁定名单」：还没锁、会自动锁的场次给志愿者提个醒 */
function lockText(g) {
  if (g.locked || !g.lock_hours || g.kind !== 'service') return ''
  return t('开场前 {0} 小时锁定名单', [g.lock_hours])
}
/** 我报的这一场（或我的岗位）名单是否已锁定：此时退出会被记录 */
function myListLocked(g) {
  const sid = myStationId(g)
  return g.locked || !!g.positions.find(p => p.id === sid)?.list_locked
}
/** 这一场现在还收不收人。
 *
 * 活动整体在报名期还不够：每个场次在**自己开场时**就关闸（enroll_open 由后端
 * 按 min(活动报名截止, 本场开场) 算）。支教那种连办八周的活动，第三周了还开着
 * 报名很正常，但第一周那场早就不该再收人。
 */
// 报名后筛选：录取前只有申请入口；录取之后才看岗位安排
const screenedApplying = computed(
  () => act.value?.recruit_mode === 'screened'
        && act.value.application?.mine?.status !== 'accepted')

function openFor(g) {
  return act.value?.status === 'published' && g.enroll_open
         && act.value.recruit_mode !== 'screened'
}
function closedText(g) {
  if (act.value?.recruit_mode === 'screened') return t('录取后由负责人安排')
  if (act.value?.status !== 'published') return t('不在报名期')
  if (act.value.opens_in) return t('未开始报名')
  return g.started ? t('本场已开场') : t('本场报名已截止')
}
function enrollText(g) {
  if (g.mode === 'pick') {
    return t(g.remaining > 0 ? '选中' : '加入候补')
  }
  return t(g.remaining > 0 ? '立即报名' : '加入候补')
}
// 岗位的报名按钮（PositionAction）在几种视角里共用，动作和状态从这里给
provide('enrollCtx', { deadlineTs, confirmPromo, cancelEnroll, canCancel, queuedShift,
                       queuedHere, openFor, doEnroll, enrollText, closedText, paused,
                       queueNote: QUEUE_NOTE })

function showGroupDetail(g) {
  groupDetail.value = { visible: true, data: g }
}
function showStationDetail(g, s) {
  stationDetail.value = { visible: true, data: s,
                          groupName: g.name, groupTime: g.time_text }
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
    a.display_label = t('报名中')
  }
  for (const g of a.shifts || []) g.enroll_open = at <= g.closesAt
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(load, Math.random() * 3000)
}
onBeforeUnmount(() => {
  clearTimeout(refreshTimer)
  infoRO?.disconnect()
})

function doEnroll(g, position) {
  if (g.mode === 'pick' && g.positions.length && !position) {
    return ElMessage.warning(t('请先展开该场次，选择一个岗位'))
  }
  enrollDlg.value = { visible: true, shift: g, position, allowReassign: true,
                      leaderWilling: false, answers: {} }
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
    // 错误自己提示：同时排着好几场时，得说清是哪一场没报上。
    // 时段冲突先问一句，确认了才带 confirm_conflict 重发
    const d = await withConflictConfirm(confirm => api.post(
      `/api/activities/${act.value.id}/shifts/${g.id}/enroll`,
      { position_id: position ? position.id : null,
        allow_reassign: allowReassign, leader_willing: leaderWilling, answers,
        confirm_conflict: confirm },
      { skipErrorToast: true }),
    { title: t('「{0}」时间冲突', [g.name]), ask: t('仍要报名？'), activityId: act.value.id })
    if (!d) return
    ElMessage.success(`「${g.name}」${d.message}`)
    groupQr.value.open({ qr: d.contact_group_qr, title: act.value.title })
  } catch (e) {
    // 资料未完善被拦截：提示后弹出资料表单，补全后即可报名
    if (e?.message?.includes('完善个人信息')) {
      try {
        await ElMessageBox.alert(
          t('报名前需先完善个人信息，便于后期联络与志愿时长录入。'),
          t('需先完善个人信息'),
          { type: 'warning', confirmButtonText: t('去完善') })
      } catch { /* 关闭提示也照样打开表单 */ }
      openProfileDialog()
    } else if (e.status !== 401) {     // 401 已经统一提示并跳去登录了
      ElMessage.error(`「${g.name}」${e.message}`)
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
      locked ? '名单已锁定，此时退出会被记录一次。确定退出吗？' : t('确定退选吗？'),
      '确认退选', { type: 'warning', confirmButtonText: t('确定'), cancelButtonText: t('取消') })
  } catch { return }
  const d = await api.del(`/api/activities/enrollments/${enrId}`)
  ElMessage[d.effect === 'locked' ? 'warning' : 'success'](d.message)
  load()
}

async function confirmPromo(g) {
  const d = await api.post(
    `/api/activities/enrollments/${g.my_enrollment.enrollment_id}/confirm-promotion`)
  ElMessage.success(d.message)
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

/* —— 窄屏的场次／岗位卡片 —— */
.m-shifts { display: flex; flex-direction: column; gap: 12px; }
.m-shift :deep(.el-card__body) { padding: 14px; }
.m-shift-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.m-shift-name, .m-shift-name :deep(.el-link__inner) { font-size: 15px; font-weight: 600; }
.m-quota { margin: 8px 0 4px; font-size: 13px; }
.m-pos {
  border: 1px solid #e4e7ed; border-radius: 4px; padding: 10px 12px; margin-top: 8px;
}
.m-pos.mine { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.m-pos.blocked { opacity: .7; }
.m-pos-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.m-pos-name { font-weight: 600; font-size: 14px; }
.m-pos-name :deep(.el-link__inner) { font-weight: 600; }
.m-pos-meta { margin-top: 4px; line-height: 1.6; }
.m-pos-act { margin-top: 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.m-pos-act-wrap { margin-top: 8px; }
.queue-note { margin-top: 4px; font-size: 12px; color: #909399; }
.m-reassign { margin-top: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.shift-bar { margin-top: 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.req-blocked { color: #e6a23c; }
.mine-card { margin-top: 12px; border-left: 3px solid var(--el-color-success); }
.mine-title { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.mine-note { margin: 4px 0; font-size: 13px; line-height: 1.6; color: #606266;
             white-space: pre-wrap; }
.group-card { margin-bottom: 12px; border-radius: 4px; }
.g-head { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
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

/* 培训 / 彩排：跟在场次后面的附加时段 */
.att-list { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.att-item {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 13px; padding: 6px 10px; border-radius: 4px; background: #f7f8fa;
}
.att-item.mine { background: var(--el-color-primary-light-9); }
/* 岗位子表里培训独占一整行，整行只有它，不用再留列表上方那条间距 */
.att-in-row { margin-top: 0; }

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

/* 仅场次：没有可展开的岗位，隐藏展开箭头 */
</style>
