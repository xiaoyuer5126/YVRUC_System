<template>
  <div class="page" v-if="act">
    <el-page-header @back="$router.back()" :title="'返回'">
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
        <el-card shadow="never">
          <h3>{{ act.title }}</h3>
          <p style="white-space:pre-wrap;color:#606266;line-height:1.6">{{ act.intro }}</p>
          <el-descriptions :column="narrow ? 1 : 2" border size="small">
            <el-descriptions-item label="组织单位">{{ act.organizer || '—' }}</el-descriptions-item>
            <el-descriptions-item label="活动时间">{{ act.time_text }}</el-descriptions-item>
            <el-descriptions-item label="活动地点">{{ act.location || '待定' }}</el-descriptions-item>
            <el-descriptions-item label="联系人">
              <template v-if="act.contacts?.length">
                <span v-for="(c, i) in act.contacts" :key="i" style="margin-right:12px">
                  {{ c.name }} <span class="mono">{{ c.phone }}</span>
                </span>
              </template>
              <span v-else>{{ act.contact_info || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="报名时间">
              {{ fmtDT(act.reg_start) }} ~ {{ fmtDT(act.reg_end) }}
            </el-descriptions-item>
            <!-- 招募范围写在明处：不在范围内的人不该报到一半才被拦下 -->
            <el-descriptions-item label="招募范围">
              {{ act.recruit_scope_text }}
            </el-descriptions-item>
          </el-descriptions>
          <div v-if="act.volunteer_requirement || act.volunteer_benefit"
               style="margin-top:12px">
            <div v-if="act.volunteer_requirement" class="side-note">
              <b>志愿者要求</b>
              <p>{{ act.volunteer_requirement }}</p>
            </div>
            <div v-if="act.volunteer_benefit" class="side-note benefit">
              <b>志愿者保障</b>
              <p>{{ act.volunteer_benefit }}</p>
            </div>
          </div>
        </el-card>

        <!-- 审批人看的是同一个页面，只是活动还没公开；不另做一套只读页 -->
        <el-alert v-if="act.review_preview" type="info" show-icon :closable="false"
                  :title="`审批预览 · 当前状态「${act.status_label}」`"
                  description="当前作为审批人查看活动的完整安排。通过或驳回请回「活动审批」页操作。" />

        <el-alert v-if="act.editing" type="warning" show-icon :closable="false"
                  title="负责人正在调整本活动的安排，报名暂时关闭"
                  description="调整期间无法报名或退选，保存后会将变更通知已报名志愿者。" />

        <!-- 还没到报名开始：倒计时归零时就地开闸（unlock），不等重拉。
             按钮在这之前是灰的（enroll_open 由后端算，含 reg_start）。 -->
        <div v-if="opensAt" class="reg-countdown">
          <el-icon><Clock /></el-icon>
          <span>报名于 <b>{{ fmtDT(act.reg_start) }}</b> 开始，还有</span>
          <el-countdown :value="opensAt" :format="openFormat" @finish="unlock" />
          <span class="dim">时间到自动开放，不用刷新页面</span>
        </div>

        <div v-if="screenedApplying" class="section-title">报名申请<span class="dim">
          勾选意向场次并填写报名问卷，截止后由负责人筛选录取；
          场次与岗位见下方安排</span></div>
        <!-- 名额是抢的：刷新按钮就放在名额上方，别让人翻回页头去找 -->
        <div v-else class="section-title">岗位安排<span class="dim">
          点击场次、岗位名称可查看详细安排</span>
          <RefreshBar :load="load" style="margin-left:auto" /></div>

        <!-- 报名后筛选：先是申请入口，下面照旧列场次与岗位——
             审批人要在同一页看完整安排，学生也要先知道自己在报什么 -->
        <ApplicationPanel v-if="screenedApplying" :act="act" @changed="load"
                          style="margin-bottom:12px" />
        <div v-if="screenedApplying" class="section-title">场次与岗位<span class="dim">
          仅供了解；录取后由负责人安排到具体岗位</span>
          <RefreshBar :load="load" style="margin-left:auto" /></div>

        <!-- 窄屏：场次与岗位改成竖排卡片。手机上选岗位是最要紧的动作，
             不能藏在表格的展开箭头后面，所以岗位直接铺开。 -->
        <div v-if="narrow" class="m-shifts">
          <el-card v-for="g in act.shifts" :key="g.id" shadow="never" class="m-shift">
            <div class="m-shift-head">
              <b @click="showGroupDetail(g)">{{ g.name }}</b>
              <el-tag size="small" effect="plain"
                      :type="g.mode === 'assign' ? 'warning' : 'info'">
                {{ g.mode_label }}
              </el-tag>
              <el-tag v-if="g.started" size="small" type="info" effect="plain">已开场</el-tag>
            </div>
            <div class="dim">{{ g.time_text }}</div>
            <div v-if="act.status === 'published' && !act.opens_in && !g.enroll_open"
                 class="dim">
              本场报名已于 {{ fmtDT(g.enroll_deadline) }} 结束
            </div>
            <div class="m-quota">
              已报名 {{ g.enrolled }}/{{ g.capacity }}
              <span v-if="g.waitlist_count" class="dim">· 候补 {{ g.waitlist_count }}</span>
              <el-progress :percentage="pct(g)" :stroke-width="5" :show-text="false"
                           style="margin-top:4px" />
            </div>
            <div v-if="g.attached?.length" class="att-list">
              <div v-for="s in g.attached" :key="s.id" class="att-item"
                   :class="{ mine: s.mine }">
                <el-tag size="small" effect="dark"
                        :type="s.kind === 'training' ? 'success' : 'warning'">
                  {{ s.kind_label }}
                </el-tag>
                <span>{{ s.time_text }}</span>
                <span class="dim">{{ s.location || act.location }}</span>
                <span class="dim">{{ attachedWho(g, s) }}</span>
                <span v-if="s.note" class="dim">备注：{{ s.note }}</span>
                <template v-if="s.mine">
                  <el-tag size="small" :type="s.excused ? 'info' : 'primary'" effect="plain">
                    {{ s.excused ? '已请假' : '要参加' }}
                  </el-tag>
                  <el-button size="small" link
                             :type="s.excused ? 'primary' : 'warning'"
                             @click="toggleExcuse(s)">
                    {{ s.excused ? '销假' : '我无法参加' }}
                  </el-button>
                </template>
              </div>
            </div>

            <!-- 报名即选岗位：每个岗位一块 -->
            <template v-if="g.positions.length">
              <div v-for="s in g.positions" :key="s.id" class="m-pos"
                   :class="{ mine: myStationId(g) === s.id, blocked: s.eligible === false }">
                <div class="m-pos-head">
                  <!-- 桌面端是 el-link，手机端原来是普通文本，看不出能点 -->
                  <el-link class="m-pos-name" type="primary" underline="never"
                           @click="showStationDetail(g, s)">{{ s.display_name }}</el-link>
                  <span class="dim">{{ s.enrolled }}/{{ s.quota }}</span>
                </div>
                <div v-if="s.is_placeholder" class="m-pos-meta dim">
                  {{ g.note || '报名只锁定时段；岗位在报名截止后统一分配。' }}
                </div>
                <div v-else class="m-pos-meta dim">
                  {{ s.location || act.location || '地点待定' }}
                  <template v-if="s.requirement_desc !== '不限'"> · {{ s.requirement_desc }}</template>
                  <template v-if="s.needs_leader"> · 设组长</template>
                  <template v-if="s.attire !== 'none'"> · 服装{{ s.attire_label }}</template>
                  <template v-if="s.pay_optional"> · 报酬可选劳务</template>
                  <template v-if="s.material_count"> · 材料 {{ s.material_count }}</template>
                </div>
                <div class="m-pos-act">
                  <!-- 我选中的岗位：原地退选／确认 -->
                  <template v-if="g.my_enrollment && myStationId(g) === s.id">
                    <template v-if="g.my_enrollment.status === 'pending_confirm'">
                      <el-countdown format="HH:mm:ss" :value="deadlineTs(g)" />
                      <el-button type="primary" size="small" :disabled="paused"
                                 @click="confirmPromo(g)">确认</el-button>
                      <el-button size="small" plain :disabled="paused"
                                 @click="cancelEnroll(g)">放弃</el-button>
                    </template>
                    <template v-else>
                      <el-tag :type="g.my_enrollment.status === 'waitlisted' ? 'warning' : 'success'"
                              size="small">
                        {{ g.my_enrollment.status === 'waitlisted'
                           ? `候补第 ${g.my_enrollment.waitlist_position} 位` : '已选中' }}
                      </el-tag>
                      <el-button size="small" type="danger" plain
                                 :disabled="paused || !canCancel(g)"
                                 @click="cancelEnroll(g)">
                        {{ g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选' }}
                      </el-button>
                    </template>
                  </template>
                  <span v-else-if="g.my_enrollment" class="dim">同场次只能选一个岗位</span>
                  <el-button v-else-if="queuedShift(g)" size="small" disabled
                             :loading="queuedHere(g, s)">
                    {{ queuedHere(g, s) ? '排队中…' : (s.remaining > 0 ? '选中' : '候补') }}
                  </el-button>
                  <el-button v-else-if="s.eligible === false" size="small" disabled>
                    不符合条件
                  </el-button>
                  <el-button v-else-if="paused" size="small" disabled>报名暂停</el-button>
                  <el-button v-else-if="openFor(g)" size="small"
                             type="primary" plain @click="doEnroll(g, s)">
                    {{ s.is_placeholder ? enrollText(g)
                       : s.remaining > 0 ? '选中' : '候补' }}
                  </el-button>
                  <el-button v-else size="small" disabled>{{ closedText(g) }}</el-button>
                </div>
                <div v-if="s.eligible === false" class="m-pos-why">
                  {{ s.ineligible_reason }}
                </div>
                <div v-if="queuedHere(g, s)" class="queue-note">{{ QUEUE_NOTE }}</div>
              </div>
              <!-- 报了名却没有对应的岗位卡（补招时没指定岗位，或岗位被删） -->
              <div v-if="g.my_enrollment && !myStationId(g)" class="m-pos-act"
                   style="margin-top:8px">
                <el-tag v-if="g.my_enrollment.status === 'enrolled'" type="success" size="small">
                  已报名（岗位待分配）
                </el-tag>
                <el-tag v-else-if="g.my_enrollment.status === 'waitlisted'" type="warning" size="small">
                  候补第 {{ g.my_enrollment.waitlist_position }} 位
                </el-tag>
                <template v-else>
                  <el-countdown format="HH:mm:ss" :value="deadlineTs(g)" />
                  <el-button type="primary" size="small" :disabled="paused"
                             @click="confirmPromo(g)">确认转正</el-button>
                </template>
                <el-button v-if="g.my_enrollment.status !== 'pending_confirm'"
                           size="small" type="danger" plain
                           :disabled="paused
                                      || (g.my_enrollment.status === 'enrolled'
                                          && !canCancel(g))"
                           @click="cancelEnroll(g)">
                  {{ g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选' }}
                </el-button>
              </div>
            </template>

            <!-- 一个岗位都没有：只可能是管理者还没建，报不了 -->
            <template v-else>
              <p class="dim" style="margin:8px 0 0">
                本场次尚未设置岗位，请联系负责人。
              </p>
              <div class="m-pos-act" style="margin-top:8px">
                <template v-if="g.my_enrollment">
                  <el-tag v-if="g.my_enrollment.status === 'enrolled'" type="success" size="small">
                    已报名（岗位待分配）
                  </el-tag>
                  <el-tag v-else-if="g.my_enrollment.status === 'waitlisted'" type="warning" size="small">
                    候补第 {{ g.my_enrollment.waitlist_position }} 位
                  </el-tag>
                  <template v-else>
                    <el-countdown format="HH:mm:ss" :value="deadlineTs(g)" />
                    <el-button type="primary" size="small" @click="confirmPromo(g)">确认转正</el-button>
                  </template>
                  <el-button v-if="g.my_enrollment.status !== 'pending_confirm'"
                             size="small" type="danger" plain
                             :disabled="g.my_enrollment.status === 'enrolled' && !canCancel(g)"
                             @click="cancelEnroll(g)">
                    {{ g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选' }}
                  </el-button>
                </template>
                <template v-else-if="queuedShift(g)">
                  <el-button size="small" disabled loading>排队中…</el-button>
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
              <div class="mine-title">
                您的岗位：<b>{{ myPosition(g).name }}</b>
                <span class="dim">{{ myPosition(g).location || act.location || '地点待定' }}</span>
              </div>
              <p v-if="myPosition(g).leader_name" class="mine-note">
                <b>组长：</b>{{ myPosition(g).leader_name }}
                <template v-if="myPosition(g).leader_contact">
                  （手机 {{ myPosition(g).leader_contact.phone || '—' }} ·
                  微信 {{ myPosition(g).leader_contact.wechat || '—' }}）
                </template>
              </p>
              <p v-if="myPosition(g).training_note" class="mine-note">
                <b>培训内容：</b>{{ myPosition(g).training_note }}
              </p>
              <p v-if="myPosition(g).attire_text" class="mine-note">
                <b>志愿服装：</b>{{ myPosition(g).attire_text }}
              </p>
              <p v-if="myPosition(g).pay_text" class="mine-note">
                <b>报酬方式：</b>{{ myPosition(g).pay_text }}（核准时长时再选）
              </p>
              <MaterialList v-if="myPosition(g).attachments?.length"
                            :items="myPosition(g).attachments" label="本岗位学习材料" />
            </el-card>

            <div v-if="g.my_enrollment" class="m-reassign">
              <span class="dim">岗位调剂：</span>
              <el-tag size="small" effect="plain"
                      :type="g.my_enrollment.allow_reassign ? 'success' : 'info'"
                      @click="toggleReassign(g)">
                {{ g.my_enrollment.allow_reassign ? '接受' : '不接受' }}
              </el-tag>
            </div>
          </el-card>
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
                    <el-table :data="g.positions" size="small" border>
                      <el-table-column label="岗位" min-width="150">
                        <template #default="{ row: s }">
                          <el-link type="primary" underline="never" style="font-weight:600"
                                   @click="showStationDetail(g, s)">{{ s.display_name }}</el-link>
                          <el-tag v-if="myStationId(g) === s.id" size="small" type="success"
                                  effect="light" style="margin-left:8px">已选</el-tag>
                          <el-tag v-if="s.needs_leader" size="small" type="warning"
                                  effect="plain" style="margin-left:8px">
                            设组长{{ s.leader_name ? `·${s.leader_name}` : '' }}
                          </el-tag>
                          <el-tag v-if="s.attire !== 'none'" size="small" type="primary"
                                  effect="plain" style="margin-left:8px">服装{{ s.attire_label }}</el-tag>
                          <el-tag v-if="s.pay_optional" size="small" type="warning"
                                  effect="plain" style="margin-left:8px">可选劳务</el-tag>
                          <el-tag v-if="s.material_count" size="small" effect="plain"
                                  style="margin-left:8px"
                                  :type="s.training_visible ? 'success' : 'info'">
                            材料 {{ s.material_count }}{{ s.training_visible ? '' : '·报名后可看' }}
                          </el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column label="服务地点" min-width="110">
                        <template #default="{ row: s }">{{ s.location || act.location }}</template>
                      </el-table-column>
                      <el-table-column label="名额" width="100">
                        <template #default="{ row: s }">
                          {{ s.enrolled }}/{{ s.quota }}
                          <el-tag v-if="s.remaining <= 0" size="small" type="danger"
                                  effect="plain">满</el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column label="要求" min-width="130">
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
                      <el-table-column label="操作" width="170" align="center">
                        <template #default="{ row: s }">
                          <!-- 我选中的岗位：原地退选/确认 -->
                          <template v-if="g.my_enrollment && myStationId(g) === s.id">
                            <template v-if="g.my_enrollment.status === 'pending_confirm'">
                              <el-countdown format="HH:mm:ss" :value="deadlineTs(g)"
                                            style="vertical-align:middle;margin-right:8px" />
                              <el-button type="primary" size="small" :disabled="paused"
                                         @click="confirmPromo(g)">确认</el-button>
                              <el-button size="small" plain :disabled="paused"
                                         @click="cancelEnroll(g)">放弃</el-button>
                            </template>
                            <el-button v-else size="small" type="danger" plain
                                       :disabled="paused || !canCancel(g)"
                                       @click="cancelEnroll(g)">
                              {{ g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选' }}
                            </el-button>
                          </template>
                          <!-- 已选其他岗位：置灰 -->
                          <el-tooltip v-else-if="g.my_enrollment"
                                      content="同场次只能选择一个岗位，如需更换请先退选" placement="top">
                            <el-button size="small" disabled>
                              {{ s.remaining > 0 ? '选中' : '候补' }}
                            </el-button>
                          </el-tooltip>
                          <!-- 已提交、还在排队：结果回来之前不能再点，也不能改选同场次别的岗位 -->
                          <template v-else-if="queuedShift(g)">
                            <el-button size="small" disabled :loading="queuedHere(g, s)">
                              {{ queuedHere(g, s) ? '排队中…' : (s.remaining > 0 ? '选中' : '候补') }}
                            </el-button>
                            <div v-if="queuedHere(g, s)" class="queue-note">{{ QUEUE_NOTE }}</div>
                          </template>
                          <!-- 条件不满足：灰掉并说明为什么，别让人点了再被拒 -->
                          <el-tooltip v-else-if="s.eligible === false" placement="top"
                                      :content="`不符合报名条件：${s.ineligible_reason}`">
                            <el-button size="small" disabled>不符合条件</el-button>
                          </el-tooltip>
                          <!-- 未报名 -->
                          <el-tooltip v-else-if="paused" placement="top"
                                      content="负责人正在调整安排，报名暂停中">
                            <el-button size="small" disabled>报名暂停</el-button>
                          </el-tooltip>
                          <el-button v-else-if="openFor(g)" size="small"
                                     type="primary" plain @click="doEnroll(g, s)">
                            {{ s.is_placeholder ? enrollText(g)
                               : s.remaining > 0 ? '选中' : '候补' }}
                          </el-button>
                          <el-button v-else size="small" disabled>
                            {{ closedText(g) }}
                          </el-button>
                        </template>
                      </el-table-column>
                    </el-table>
                    <div class="dim" style="margin-top:8px">
                      同场次只能选一个岗位，候补将在名额释放后转正，转正需 2 小时内确认。
                    </div>
                  </template>
                  <p v-else class="dim" style="margin:4px 0">本场次尚未设置岗位，请联系负责人。</p>

                  <!-- 报了名却没有对应的岗位行（补招时没指定岗位，或岗位被删）：
                       退选与确认转正没有别的落脚点，放在这里 -->
                  <div v-if="g.my_enrollment && !myStationId(g)" class="shift-bar">
                    <el-tag v-if="g.my_enrollment.status === 'enrolled'" type="success">
                      已报名（岗位待分配）
                    </el-tag>
                    <el-tag v-else-if="g.my_enrollment.status === 'waitlisted'" type="warning">
                      候补中 · 第 {{ g.my_enrollment.waitlist_position }} 位
                    </el-tag>
                    <template v-else>
                      <el-countdown format="HH:mm:ss" :value="deadlineTs(g)">
                        <template #prefix>
                          <el-tag type="danger" size="small">转正确认剩</el-tag>&nbsp;
                        </template>
                      </el-countdown>
                      <el-button type="primary" size="small" :disabled="paused"
                                 @click="confirmPromo(g)">确认转正</el-button>
                    </template>
                    <el-button v-if="g.my_enrollment.status !== 'pending_confirm'"
                               size="small" type="danger" plain
                               :disabled="paused
                                          || (g.my_enrollment.status === 'enrolled'
                                              && !canCancel(g))"
                               @click="cancelEnroll(g)">
                      {{ g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选' }}
                    </el-button>
                  </div>

                  <!-- 调剂意愿：点一下即可改，只作为负责人排班时的参考 -->
                  <div v-if="g.my_enrollment" class="shift-bar">
                    <span class="dim">岗位调剂：</span>
                    <el-tooltip content="仅在名额不足时可能进行调剂，负责人可看到你的意愿"
                                placement="top">
                      <el-tag size="small" effect="plain" style="cursor:pointer"
                              :type="g.my_enrollment.allow_reassign ? 'success' : 'info'"
                              @click="toggleReassign(g)">
                        {{ g.my_enrollment.allow_reassign ? '接受调剂' : '不接受调剂' }}
                      </el-tag>
                    </el-tooltip>
                  </div>

                  <!-- 已报名：把自己岗位的培训内容与材料直接摊开 -->
                  <el-card v-if="myPosition(g)" shadow="never" class="mine-card">
                    <div class="mine-title">
                      您的岗位：<b>{{ myPosition(g).name }}</b>
                      <span class="dim">{{ myPosition(g).location || act.location || '地点待定' }}</span>
                      <el-tag v-if="g.my_enrollment?.status === 'waitlisted'"
                              size="small" type="warning" effect="plain">候补中</el-tag>
                    </div>
                    <p v-if="myPosition(g).leader_name" class="mine-note">
                      <b>组长：</b>{{ myPosition(g).leader_name }}
                      <template v-if="myPosition(g).leader_contact">
                        （手机 {{ myPosition(g).leader_contact.phone || '—' }} ·
                        微信 {{ myPosition(g).leader_contact.wechat || '—' }}）
                      </template>
                    </p>
                    <p v-if="myPosition(g).training_note" class="mine-note">
                      <b>培训内容：</b>{{ myPosition(g).training_note }}
                    </p>
                    <p v-if="myPosition(g).service_task" class="mine-note">
                      <b>服务任务：</b>{{ myPosition(g).service_task }}
                    </p>
                    <p v-if="myPosition(g).position_requirement" class="mine-note">
                      <b>岗位要求：</b>{{ myPosition(g).position_requirement }}
                    </p>
                    <p v-if="myPosition(g).attire_text" class="mine-note">
                      <b>志愿服装：</b>{{ myPosition(g).attire_text }}
                    </p>
                    <p v-if="myPosition(g).pay_text" class="mine-note">
                      <b>报酬方式：</b>{{ myPosition(g).pay_text }}（核准时长时再选）
                    </p>
                    <MaterialList v-if="myPosition(g).attachments?.length"
                                  :items="myPosition(g).attachments"
                                  label="本岗位学习材料（请提前学习）" />
                    <p v-if="!myPosition(g).training_note
                             && !myPosition(g).attachments?.length"
                       class="dim" style="margin:0">
                      负责人还没有上传本岗位的培训内容与材料。
                    </p>
                  </el-card>

                  <!-- 培训 / 彩排：报了这个场次就要一起去 -->
                  <div v-if="g.attached?.length" class="att-list">
                    <div v-for="s in g.attached" :key="s.id" class="att-item"
                         :class="{ mine: s.mine }">
                      <el-tag size="small" effect="dark"
                              :type="s.kind === 'training' ? 'success' : 'warning'">
                        {{ s.kind_label }}
                      </el-tag>
                      <b>{{ s.name }}</b>
                      <span>{{ s.time_text }}</span>
                      <span class="dim">{{ s.location || act.location || '地点待定' }}</span>
                      <span class="dim">{{ attachedWho(g, s) }}</span>
                      <span class="dim">
                        {{ s.hours_mode === 'none' ? '不计时长' : '计入志愿时长' }}
                      </span>
                      <span v-if="s.note" class="dim">备注：{{ s.note }}</span>
                      <template v-if="s.mine">
                        <el-tag size="small" :type="s.excused ? 'info' : 'primary'"
                                effect="plain">
                          {{ s.excused ? '已请假' : '您需要参加' }}
                        </el-tag>
                        <el-button size="small" link
                                   :type="s.excused ? 'primary' : 'warning'"
                                   @click="toggleExcuse(s)">
                          {{ s.excused ? '销假' : '我无法参加' }}
                        </el-button>
                      </template>
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="场次" min-width="180">
              <template #default="{ row }">
                <el-link type="primary" underline="never" style="font-weight:600"
                         @click="showGroupDetail(row)">{{ row.name }}</el-link>
                <el-tag size="small" effect="plain" style="margin-left:8px"
                        :type="row.mode === 'pick' ? 'info' : row.mode === 'assign' ? 'warning' : 'primary'">
                  {{ row.mode_label }}
                </el-tag>
                <!-- 每个场次在自己开场时就停止收人，活动整体可能还在报名期 -->
                <div v-if="act.status === 'published' && !act.opens_in && !row.enroll_open"
                     class="dim">
                  本场报名已于 {{ fmtDT(row.enroll_deadline) }} 结束
                </div>
              </template>
            </el-table-column>
            <el-table-column label="时间" min-width="140" prop="time_text" />
            <el-table-column label="已报名/名额" min-width="150">
              <template #default="{ row }">
                <div>{{ row.enrolled }}/{{ row.capacity }}
                  <span v-if="row.waitlist_count" class="dim">· 候补 {{ row.waitlist_count }}</span>
                </div>
                <el-progress :percentage="pct(row)" :stroke-width="5" :show-text="false" />
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 右：冲突提示 / 变更记录 -->
      <el-col :span="7">
        <el-alert v-if="act.my_time_conflicts?.length" type="error" :closable="false">
          <p style="margin:0 0 4px"><b>您有时间冲突的活动：</b></p>
          <p v-for="c in act.my_time_conflicts" :key="c.enrollment_id" style="margin:4px 0;font-size:13px">
            「{{ c.activity_title }}」{{ c.shift_name }}（{{ c.time }}）
          </p>
        </el-alert>
        <el-card shadow="never">
          <template #header><b>变更记录</b></template>
          <el-timeline v-if="act.change_logs?.length" style="padding-left:4px">
            <el-timeline-item v-for="l in act.change_logs" :key="l.id"
                              :timestamp="fmtDT(l.created_at)" placement="top"
                              :type="l.action === 'delete' ? 'danger' : 'primary'">
              <b style="font-size:13px">{{ l.actor_name }}</b>
              <span class="dim"> {{ l.summary }}</span>
              <div v-for="(ch, i) in l.changes" :key="i" class="dim" style="margin-top:4px">
                {{ ch.label }}：{{ ch.old || '空' }} → <b style="color:var(--el-color-primary)">{{ ch.new || '空' }}</b>
              </div>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-else description="暂无变更" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 场次详情 -->
    <el-dialog v-model="groupDetail.visible" :title="(groupDetail.data?.name || '') + ' · 场次详情'" width="480">
      <el-descriptions v-if="groupDetail.data" :column="1" border>
        <el-descriptions-item label="岗位模式">{{ groupDetail.data.mode_label }}</el-descriptions-item>
        <el-descriptions-item label="时间">{{ groupDetail.data.time_text }}</el-descriptions-item>
        <el-descriptions-item label="人数">
          已报名 {{ groupDetail.data.enrolled }} / {{ groupDetail.data.capacity }}
          <span v-if="groupDetail.data.waitlist_count" class="dim">（候补 {{ groupDetail.data.waitlist_count }} 人）</span>
          <span v-if="groupDetail.data.held_seats" class="dim">
            · 另有 {{ groupDetail.data.held_seats }} 个名额因临期退选被扣留，不再释放</span>
        </el-descriptions-item>
        <el-descriptions-item label="剩余名额">{{ groupDetail.data.remaining }}</el-descriptions-item>
        <el-descriptions-item label="条件要求">{{ groupDetail.data.requirement_desc }}</el-descriptions-item>
        <el-descriptions-item label="说明">{{ groupDetail.data.note || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 岗位详情 -->
    <el-dialog v-model="stationDetail.visible" :title="(stationDetail.data?.name || '') + ' · 岗位详情'" width="480">
      <el-descriptions v-if="stationDetail.data" :column="1" border>
        <el-descriptions-item label="所属场次">{{ stationDetail.groupName }}（{{ stationDetail.groupTime }}）</el-descriptions-item>
        <el-descriptions-item label="服务地点">{{ stationDetail.data.location || act.location || '待定' }}</el-descriptions-item>
        <el-descriptions-item label="名额">
          {{ stationDetail.data.enrolled }}/{{ stationDetail.data.quota }}
        </el-descriptions-item>
        <el-descriptions-item label="条件要求">{{ stationDetail.data.requirement_desc }}</el-descriptions-item>
        <el-descriptions-item label="服务任务"><span class="pre">{{ stationDetail.data.service_task || '—' }}</span></el-descriptions-item>
        <el-descriptions-item label="岗位要求"><span class="pre">{{ stationDetail.data.position_requirement || '—' }}</span></el-descriptions-item>
        <el-descriptions-item label="志愿服装">{{ stationDetail.data.attire_text || '不需要' }}</el-descriptions-item>
        <el-descriptions-item label="报酬方式">{{ stationDetail.data.pay_text || '志愿时长' }}</el-descriptions-item>
        <el-descriptions-item label="培训内容">
          <template v-if="stationDetail.data.training_visible">
            <span class="pre">{{ stationDetail.data.training_note || '—' }}</span>
          </template>
          <span v-else class="dim">报名本场次后可见</span>
        </el-descriptions-item>
        <el-descriptions-item label="候补名额">
          {{ stationDetail.data.waitlist_count }}/{{ stationDetail.data.waitlist_quota }}
        </el-descriptions-item>
        <el-descriptions-item label="组长">
          <template v-if="!stationDetail.data.needs_leader">
            <span class="dim">本岗位不设组长</span>
          </template>
          <template v-else-if="!stationDetail.data.training_visible">
            <span class="dim">本岗位设组长，报名后可见组长信息</span>
          </template>
          <template v-else>
            {{ stationDetail.data.leader_name || '尚未任命' }}
            <div v-if="stationDetail.data.leader_contact" class="dim" style="line-height:1.8">
              手机 {{ stationDetail.data.leader_contact.phone || '—' }} ·
              微信 {{ stationDetail.data.leader_contact.wechat || '—' }}
            </div>
            <div v-else-if="stationDetail.data.leader_name" class="dim">
              报名本岗位所在的组后可见联系方式
            </div>
          </template>
        </el-descriptions-item>
      </el-descriptions>
      <MaterialList v-if="stationDetail.data?.attachments?.length"
                    :items="stationDetail.data.attachments"
                    label="本岗位学习材料" />
    </el-dialog>

    <!-- 报名确认：确认时段 + 表态是否接受岗位调剂 -->
    <el-dialog v-model="enrollDlg.visible" title="确认报名" width="480px">
      <template v-if="enrollDlg.shift">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="场次">
            {{ enrollDlg.shift.name }}（{{ enrollDlg.shift.time_text }}）
          </el-descriptions-item>
          <el-descriptions-item label="岗位">
            {{ enrollDlg.position ? enrollDlg.position.name
               : (enrollDlg.shift.mode === 'assign' ? '将由负责人统一筛选分配' : '无需选择') }}
          </el-descriptions-item>
          <el-descriptions-item v-if="enrollExtra.length" label="还需参加">
            <div v-for="s in enrollExtra" :key="s.id" style="line-height:1.7">
              {{ s.kind_label }}：{{ s.time_text }}
              <span class="dim">{{ s.location || act.location || '地点待定' }}</span>
              <div v-if="s.note" class="dim">备注：{{ s.note }}</div>
            </div>
          </el-descriptions-item>
          <el-descriptions-item label="结果">
            <el-tag :type="willWaitlist ? 'warning' : 'success'" size="small">
              {{ willWaitlist ? '名额已满，将进入候补队列' : '名额充足，即选即得' }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <el-alert v-if="enrollExtra.length && !willWaitlist" type="info" show-icon
                  :closable="false"
                  title="请确认这些时间你可到场，如时间冲突需向负责人请假。" />

        <!-- 活动设了报名问卷：报名时一并答，答案跟着报名记录走 -->
        <el-form v-if="enrollQuestions.length" label-position="top" style="margin-top:12px">
          <QuestionFields ref="enrollFields" v-model="enrollDlg.answers"
                          :questions="enrollQuestions"
                          :upload-url="`/api/activities/${act.id}/apply/upload`" />
        </el-form>

        <div class="reassign-box">
          <el-checkbox v-model="enrollDlg.allowReassign" :disabled="willWaitlist">
            接受岗位调剂
          </el-checkbox>
          <div v-if="willWaitlist" class="dim" style="margin-top:4px">
            候补须接受调剂，负责人可调至空缺岗位
          </div>
          <div v-else class="dim" style="margin-top:4px">
            仅在名额不足时可能进行调剂，负责人可看到你的意愿。
          </div>
          <el-checkbox v-model="enrollDlg.leaderWilling" style="display:block;margin-top:8px">
            愿意担任组长（如本岗位需要）
          </el-checkbox>
        </div>
      </template>
      <template #footer>
        <el-button @click="enrollDlg.visible = false">再想想</el-button>
        <el-button type="primary" @click="submitEnroll">
          确认{{ willWaitlist ? '加入候补' : '报名' }}
        </el-button>
      </template>
    </el-dialog>

    <ProfileDialog ref="profileDialog" />
    <GroupQrDialog ref="groupQr" />
  </div>
  <!-- 转发来的链接：不在招募范围内的人看到的不是 404，而是原因 -->
  <div v-else-if="blocked" class="page">
    <el-result icon="warning" title="本活动不面向您招募" :sub-title="blocked">
      <template #extra>
        <el-button type="primary" @click="$router.push('/')">回活动广场</el-button>
      </template>
    </el-result>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtDT } from '../api'
import { MODE_COLORS } from '../store'
import ProfileDialog from '../components/ProfileDialog.vue'
import GroupQrDialog from '../components/GroupQrDialog.vue'
import ApplicationPanel from '../components/ApplicationPanel.vue'
import MaterialList from '../components/MaterialList.vue'
import QuestionFields from '../components/QuestionFields.vue'
import RefreshBar from '../components/RefreshBar.vue'
import { useNarrow } from '../composables/useNarrow'
import { Clock, Warning } from '@element-plus/icons-vue'

const route = useRoute()
const narrow = useNarrow()
const act = ref(null)
const blocked = ref('')
const groupDetail = ref({ visible: false, data: null })
const enrollDlg = ref({ visible: false, shift: null, position: null,
                        leaderWilling: false, allowReassign: true })
/** 已提交、结果还没回来的报名：场次 id → 岗位 id（没有岗位记 ''）。
 *
 * 抢报时一个请求要按先后排十几秒。从前弹窗一直开着等结果，这段时间别的场次
 * 也点不了；现在确认即关窗，按钮原地显示「排队中…」，可以接着报别的场次，
 * 各自出结果各自提示。同一场次在结果回来之前不能再点——后端也会按重复报名
 * 拒掉，但那是白排一轮队。
 */
const queued = ref({})       // vuecheck: script-only（模板经 queuedShift / queuedHere 读它）
const QUEUE_NOTE = '排队中，请勿刷新'
const queuedShift = g => g.id in queued.value
const queuedHere = (g, s) => queued.value[g.id] === (s ? s.id : '')
// 挂了适用岗位的题只问报这个岗位的人
const enrollQuestions = computed(() => (act.value?.questions || []).filter(q => {
  const scope = q.applies_to_positions || []
  return !scope.length || scope.includes(enrollDlg.value.position?.id)
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

function pct(g) {
  return Math.min(100, Math.round((g.enrolled / Math.max(1, g.capacity)) * 100))
}
function deadlineTs(g) {
  return new Date(g.my_enrollment.confirm_deadline.replace(' ', 'T').slice(0, 19)).getTime()
}
// 我在这个场次的岗位对象（报名后才有；统一分配的场次分配前为 null）
// 培训/彩排请假：服务场次不来是退选（有名额扣留规则），这里只管附属时段
async function toggleExcuse(s) {
  if (s.excused) {
    await api.post(`/api/activities/enrollments/${s.my_enrollment_id}/excuse`,
                   { excused: false })
    ElMessage.success('已销假')
    return load()
  }
  let reason = ''
  try {
    const r = await ElMessageBox.prompt(
      `「${s.name}」${s.time_text}。请说明无法参加的原因，负责人会看到。`,
      `${s.kind_label}请假`,
      { inputPlaceholder: '如：与专业课时间冲突', inputPattern: /.{2,}/,
        inputErrorMessage: '请简单写一下原因',
        confirmButtonText: '提交请假', cancelButtonText: '再想想' })
    reason = r.value
  } catch { return }
  await api.post(`/api/activities/enrollments/${s.my_enrollment_id}/excuse`,
                 { excused: true, reason })
  ElMessage.success('已请假，负责人会看到')
  load()
}

// 培训/彩排可以只对某几个岗位生效，把「谁要去」写在脸上
function attachedWho(g, s) {
  if (!s.applies_to_positions?.length) return '全体参加'
  const names = (g.positions || [])
    .filter(p => s.applies_to_positions.includes(p.id)).map(p => p.name)
  return names.length ? `仅 ${names.join('、')}` : '仅部分岗位'
}

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
function canCancel(g) {
  return new Date(g.start_time.replace(' ', 'T')) > new Date()
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
  if (act.value?.recruit_mode === 'screened') return '录取后由负责人安排'
  if (act.value?.status !== 'published') return '不在报名期'
  if (act.value.opens_in) return '未开始报名'
  return g.started ? '本场已开场' : '本场报名已截止'
}
function enrollText(g) {
  if (g.mode === 'pick') {
    return g.remaining > 0 ? '选中' : '加入候补'
  }
  return g.remaining > 0 ? '立即报名' : '加入候补'
}
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
  () => (act.value?.opens_in > 86400 ? 'DD 天 HH:mm:ss' : 'HH:mm:ss'))

async function load() {
  let d
  try {
    d = await api.get(`/api/activities/${route.params.id}`, { silent: true })
  } catch (e) {
    if (e.status === 403) { blocked.value = e.message; return }
    ElMessage.error(e.message)
    return
  }
  const at = Date.now()
  // 每场的截止也换成本地时间戳，本地开闸时拿它判断这一场还收不收人
  for (const g of d.shifts || []) g.closesAt = at + (g.closes_in_ms ?? Infinity)
  act.value = d
  const ms = d.opens_in_ms ?? d.opens_in * 1000
  opensAt.value = ms ? at + ms : 0
}
onMounted(load)

/** 倒计时归零：就地开闸，不等重拉。
 *
 * 从前归零时重拉一次、等后端说「开了」按钮才亮——谁的网快谁先亮，抢报拼的
 * 成了刷新速度。现在每个人的按钮在同一刻亮起，规则与后端相同：报名已开始、
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
onBeforeUnmount(() => clearTimeout(refreshTimer))

function doEnroll(g, position) {
  if (g.mode === 'pick' && g.positions.length && !position) {
    return ElMessage.warning('请先展开该场次，选择一个岗位')
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
    // 错误自己提示：同时排着好几场时，得说清是哪一场没报上
    const d = await api.post(
      `/api/activities/${act.value.id}/shifts/${g.id}/enroll`,
      { position_id: position ? position.id : null,
        allow_reassign: allowReassign, leader_willing: leaderWilling, answers },
      { skipErrorToast: true })
    ElMessage.success(`「${g.name}」${d.message}`)
    groupQr.value.open({ qr: d.contact_group_qr, title: act.value.title })
  } catch (e) {
    // 资料未完善被拦截：提示后弹出资料表单，补全后即可报名
    if (e?.message?.includes('完善个人信息')) {
      try {
        await ElMessageBox.alert(
          '报名前需先完善个人信息，便于后期联络与志愿时长录入。',
          '需先完善个人信息',
          { type: 'warning', confirmButtonText: '去完善' })
      } catch { /* 关闭提示也照样打开表单 */ }
      openProfileDialog()
    } else if (e.data && e.data.conflicts) {
      // 纯文本，不拼 HTML：活动名是别人填的，当 HTML 渲染就等于把一段
      // 脚本塞进每个撞时间的志愿者的浏览器里
      const list = e.data.conflicts
        .map(c => `「${c.activity_title}」${c.shift_name}（${c.time}）`
                  + (c.waiting ? '（候补中）' : '')).join('\n')
      ElMessageBox.alert(list, `「${g.name}」与其他活动时间冲突`, { type: 'error' })
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
  ElMessage.success(d.allow_reassign ? '已设为接受岗位调剂' : '已设为不接受岗位调剂')
}

async function cancelEnroll(g) {
  const enrId = g.my_enrollment.enrollment_id
  const late = !canCancel(g)
  try {
    await ElMessageBox.confirm(
      late ? '距开始不足24小时，将记录一次临期退选。确定退选吗？' : '确定退选吗？',
      '确认退选', { type: 'warning', confirmButtonText: '确定', cancelButtonText: '取消' })
  } catch { return }
  const d = await api.del(`/api/activities/enrollments/${enrId}`)
  ElMessage[d.effect === 'held' ? 'warning' : 'success'](d.message)
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
  border-radius: 8px; background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.reg-countdown :deep(.el-statistic__content) {
  font-size: 15px; font-weight: 600; color: var(--el-color-primary);
}

/* —— 窄屏的场次／岗位卡片 —— */
.m-shifts { display: flex; flex-direction: column; gap: 12px; }
.m-shift :deep(.el-card__body) { padding: 14px; }
.m-shift-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.m-shift-head b { font-size: 15px; color: var(--el-color-primary); }
.m-quota { margin: 8px 0 4px; font-size: 13px; }
.m-pos {
  border: 1px solid #e4e7ed; border-radius: 8px; padding: 10px 12px; margin-top: 8px;
}
.m-pos.mine { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.m-pos.blocked { opacity: .7; }
.m-pos-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.m-pos-name { font-weight: 600; font-size: 14px; }
.m-pos-name :deep(.el-link__inner) { font-weight: 600; }
.m-pos-meta { margin-top: 4px; line-height: 1.6; }
.m-pos-act { margin-top: 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.m-pos-why { margin-top: 8px; font-size: 12px; color: #e6a23c; }
.queue-note { margin-top: 4px; font-size: 12px; color: #909399; }
.m-reassign { margin-top: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.shift-bar { margin-top: 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.req-blocked { color: #e6a23c; }
.mine-card { margin-top: 12px; border-left: 3px solid var(--el-color-success); }
.mine-title { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.mine-note { margin: 4px 0; font-size: 13px; line-height: 1.75; color: #606266;
             white-space: pre-wrap; }
.group-card { margin-bottom: 12px; border-radius: 12px; }
.g-head { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
.reassign-box { margin-top: 12px; padding: 10px 12px; border-radius: 8px; background: #f7f8fa; }
.reassign-box .dim { margin: 8px 0 0; font-size: 13px; line-height: 1.7; }

/* 活动级的要求与保障 */
.side-note {
  border-left: 3px solid var(--el-color-warning); background: #fdf6ec;
  padding: 8px 12px; border-radius: 0 8px 8px 0; margin-bottom: 8px;
}
.side-note.benefit { border-left-color: var(--el-color-success); background: #f0f9eb; }
.side-note b { font-size: 13px; }
.side-note p { margin: 4px 0 0; font-size: 13px; line-height: 1.75;
               white-space: pre-wrap; color: #606266; }

/* 培训 / 彩排：跟在场次后面的附加时段 */
.att-list { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.att-item {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 13px; padding: 6px 10px; border-radius: 8px; background: #f7f8fa;
}
.att-item.mine { background: var(--el-color-primary-light-9); }

/* 仅场次：没有可展开的岗位，隐藏展开箭头 */
</style>
