<template>
  <div class="page" v-if="act">
    <el-page-header @back="$router.back()" :title="'返回'">
      <!-- 窄屏下标题与标签分行：三样东西挤一行会被折成三行 -->
      <template #content>
        <span class="hd-title">{{ act.title }}</span>
        <span class="hd-tags">
          <el-tag :color="typeColor(act.type)" effect="dark" style="border:none">
            {{ act.type_label }}
          </el-tag>
          <el-tag :type="statusType">{{ act.display_label }}</el-tag>
        </span>
      </template>
    </el-page-header>

    <el-row :gutter="18" style="margin-top:16px">
      <el-col :span="17">
        <el-card shadow="never">
          <h3 style="margin-top:0">{{ act.title }}</h3>
          <p style="white-space:pre-wrap;color:#606266;line-height:1.8">{{ act.intro }}</p>
          <el-descriptions :column="narrow ? 1 : 2" border size="small">
            <el-descriptions-item label="组织单位">{{ act.organizer || '—' }}</el-descriptions-item>
            <el-descriptions-item label="整体时间">{{ act.time_text }}</el-descriptions-item>
            <el-descriptions-item label="活动地点">{{ act.location || '待定' }}</el-descriptions-item>
            <el-descriptions-item label="公示联系方式">
              <template v-if="act.contacts?.length">
                <span v-for="(c, i) in act.contacts" :key="i" style="margin-right:14px">
                  {{ c.name }} <span class="mono">{{ c.phone }}</span>
                </span>
              </template>
              <span v-else>{{ act.contact_info || '—' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="报名窗口">
              {{ fmtDT(act.reg_start) }} ~ {{ fmtDT(act.reg_end) }}
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
          <div v-if="act.qa_group_qr" style="margin-top:10px;display:flex;gap:10px;align-items:center">
            <img :src="act.qa_group_qr" width="86" height="86"
                 style="border-radius:8px;border:1px solid #eceef1" />
            <span class="dim">扫码入群，自愿加入<br />（平台仅作为二维码展示容器）</span>
          </div>
        </el-card>

        <!-- 审批人看的是同一个页面，只是活动还没公开；不另做一套只读页 -->
        <el-alert v-if="act.review_preview" type="info" show-icon :closable="false"
                  style="margin:12px 0"
                  :title="`审批预览 · 当前状态「${act.status_label}」`"
                  description="这条活动还没有公开，你是作为审批人在看它的完整安排。通过或驳回请回「活动审批」页操作。" />

        <el-alert v-if="act.editing" type="warning" show-icon :closable="false"
                  style="margin:12px 0"
                  title="负责老师正在调整本活动的安排，报名暂时关闭"
                  description="调整期间无法报名或退选；改好后会通过企业微信通知已报名的志愿者。" />

        <div class="section-title">岗位安排<span class="dim" style="font-weight:400;margin-left:8px">
          点击岗位名称可查看详情；点击行首箭头展开岗位</span></div>

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
            <div v-if="act.status === 'published' && !g.enroll_open" class="dim">
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
            <template v-if="g.mode === 'pick' && g.positions.length">
              <div v-for="s in g.positions" :key="s.id" class="m-pos"
                   :class="{ mine: myStationId(g) === s.id, blocked: s.eligible === false }">
                <div class="m-pos-head">
                  <span class="m-pos-name" @click="showStationDetail(g, s)">{{ s.name }}</span>
                  <span class="dim">{{ s.enrolled }}/{{ s.quota }}</span>
                </div>
                <div class="m-pos-meta dim">
                  {{ s.location || '地点待定' }}
                  <template v-if="s.requirement_desc !== '不限'"> · {{ s.requirement_desc }}</template>
                  <template v-if="s.needs_leader"> · 设组长</template>
                  <template v-if="s.needs_vest"> · 需领马甲</template>
                  <template v-if="s.material_count"> · 材料 {{ s.material_count }}</template>
                </div>
                <div class="m-pos-act">
                  <!-- 我选中的岗位：原地退选／确认 -->
                  <template v-if="g.my_enrollment && myStationId(g) === s.id">
                    <template v-if="g.my_enrollment.status === 'pending_confirm'">
                      <el-countdown format="HH:mm:ss" :value="deadlineTs(g)" />
                      <el-button type="primary" size="small" @click="confirmPromo(g)">确认</el-button>
                      <el-button size="small" plain @click="cancelEnroll(g)">放弃</el-button>
                    </template>
                    <template v-else>
                      <el-tag :type="g.my_enrollment.status === 'waitlisted' ? 'warning' : 'success'"
                              size="small">
                        {{ g.my_enrollment.status === 'waitlisted'
                           ? `候补第 ${g.my_enrollment.waitlist_position} 位` : '已选中' }}
                      </el-tag>
                      <el-button size="small" type="danger" plain
                                 :disabled="!canCancel(g)" @click="cancelEnroll(g)">
                        {{ g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选' }}
                      </el-button>
                    </template>
                  </template>
                  <span v-else-if="g.my_enrollment" class="dim">同场次只能选一个岗位</span>
                  <el-button v-else-if="s.eligible === false" size="small" disabled>
                    不符合条件
                  </el-button>
                  <el-button v-else-if="openFor(g)" size="small"
                             type="primary" plain @click="doEnroll(g, s)">
                    {{ s.remaining > 0 ? '选中' : '候补' }}
                  </el-button>
                </div>
                <div v-if="s.eligible === false" class="m-pos-why">
                  {{ s.ineligible_reason }}
                </div>
              </div>
            </template>

            <!-- 截止后统一分配：报名按钮留在场次上 -->
            <template v-else>
              <p class="dim" style="margin:8px 0 0">
                {{ g.mode === 'assign'
                   ? (g.note || '本场次报名只锁定时段；报名截止后由青协统一分配岗位。')
                   : '本场次尚未设置岗位，请联系负责老师。' }}
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
                <span class="dim">{{ myPosition(g).location || '地点待定' }}</span>
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
              <MaterialList v-if="myPosition(g).attachments?.length"
                            :items="myPosition(g).attachments" label="本岗位学习材料" />
            </el-card>
            <MaterialList v-if="g.attachments?.length" :items="g.attachments"
                          label="本场次学习材料" />

            <div v-if="g.my_enrollment" class="m-reassign">
              <span class="dim">岗位调剂：</span>
              <el-tag size="small" effect="plain"
                      :type="g.my_enrollment.allow_reassign ? 'success' : 'info'"
                      @click="toggleReassign(g)">
                {{ g.my_enrollment.allow_reassign ? '接受' : '不接受' }}
              </el-tag>
              <span class="dim">点击切换，仅供负责老师参考</span>
            </div>
          </el-card>
        </div>

        <el-card v-else shadow="never">
          <el-table :data="act.shifts" row-key="id"
                    :expand-row-keys="act.shifts.map(g => g.id)">
            <el-table-column type="expand" width="34">
              <template #default="{ row: g }">
                <div style="padding:6px 12px 6px 40px">
                  <!-- 直接定二级：岗位表格 -->
                  <template v-if="g.mode === 'pick' && g.positions.length">
                    <el-table :data="g.positions" size="small" border>
                      <el-table-column label="岗位" min-width="150">
                        <template #default="{ row: s }">
                          <el-link type="primary" :underline="false" style="font-weight:600"
                                   @click="showStationDetail(g, s)">{{ s.name }}</el-link>
                          <el-tag v-if="myStationId(g) === s.id" size="small" type="success"
                                  effect="light" style="margin-left:6px">已选</el-tag>
                          <el-tag v-if="s.needs_leader" size="small" type="warning"
                                  effect="plain" style="margin-left:6px">
                            设组长{{ s.leader_name ? `·${s.leader_name}` : '' }}
                          </el-tag>
                          <el-tag v-if="s.needs_vest" size="small" type="primary"
                                  effect="plain" style="margin-left:6px">需领马甲</el-tag>
                          <el-tag v-if="s.material_count" size="small" effect="plain"
                                  style="margin-left:6px"
                                  :type="s.training_visible ? 'success' : 'info'">
                            材料 {{ s.material_count }}{{ s.training_visible ? '' : '·报名后可看' }}
                          </el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column prop="location" label="服务地点" min-width="110" />
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
                                            style="vertical-align:middle;margin-right:6px" />
                              <el-button type="primary" size="small" @click="confirmPromo(g)">确认</el-button>
                              <el-button size="small" plain @click="cancelEnroll(g)">放弃</el-button>
                            </template>
                            <el-button v-else size="small" type="danger" plain
                                       :disabled="!canCancel(g)" @click="cancelEnroll(g)">
                              {{ g.my_enrollment.status === 'waitlisted' ? '退出候补' : '退选' }}
                            </el-button>
                          </template>
                          <!-- 已选其他岗位：置灰 -->
                          <el-tooltip v-else-if="g.my_enrollment"
                                      content="同场次只能选择一个岗位；如需更换请先退选" placement="top">
                            <el-button size="small" disabled>
                              {{ s.remaining > 0 ? '选中' : '候补' }}
                            </el-button>
                          </el-tooltip>
                          <!-- 条件不满足：灰掉并说明为什么，别让人点了再被拒 -->
                          <el-tooltip v-else-if="s.eligible === false" placement="top"
                                      :content="`不符合报名条件：${s.ineligible_reason}`">
                            <el-button size="small" disabled>不符合条件</el-button>
                          </el-tooltip>
                          <!-- 未报名 -->
                          <el-button v-else-if="openFor(g)" size="small"
                                     type="primary" plain @click="doEnroll(g, s)">
                            {{ s.remaining > 0 ? '选中' : '候补' }}
                          </el-button>
                          <span v-else class="dim">—</span>
                        </template>
                      </el-table-column>
                    </el-table>
                    <div class="dim" style="margin-top:6px">
                      同场次只能选一个岗位；满员可点「候补」，按顺序递补（转正需 2 小时内确认）。
                    </div>
                  </template>
                  <p v-else-if="g.mode === 'assign'" class="dim" style="margin:4px 0">
                    {{ g.note || '本场次报名只锁定时段；报名截止后由青协统一分配岗位，结果经企业微信通知。' }}
                  </p>
                  <p v-else class="dim" style="margin:4px 0">本场次尚未设置岗位，请联系负责老师。</p>

                  <!-- 已报名：把自己岗位的培训内容与材料直接摊开 -->
                  <el-card v-if="myPosition(g)" shadow="never" class="mine-card">
                    <div class="mine-title">
                      您的岗位：<b>{{ myPosition(g).name }}</b>
                      <span class="dim">{{ myPosition(g).location || '地点待定' }}</span>
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
                    <p v-if="myPosition(g).requirement_note" class="mine-note">
                      <b>服务要求：</b>{{ myPosition(g).requirement_note }}
                    </p>
                    <MaterialList v-if="myPosition(g).attachments?.length"
                                  :items="myPosition(g).attachments"
                                  label="本岗位学习材料（请提前学习）" />
                    <p v-if="!myPosition(g).training_note
                             && !myPosition(g).attachments?.length"
                       class="dim" style="margin:0">
                      负责老师还没有上传本岗位的培训内容与材料。
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
                    <div class="dim" style="font-size:12px">
                      报名本场次时自动带上，退选时一并退出；到场同样要扫码签到。
                    </div>
                  </div>

                  <!-- 场次级学习材料 -->
                  <MaterialList v-if="g.attachments?.length" :items="g.attachments"
                                label="本场次学习材料" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="场次" min-width="180">
              <template #default="{ row }">
                <el-link type="primary" :underline="false" style="font-weight:600"
                         @click="showGroupDetail(row)">{{ row.name }}</el-link>
                <el-tag size="small" effect="plain" style="margin-left:6px"
                        :type="row.mode === 'pick' ? 'info' : row.mode === 'assign' ? 'warning' : 'primary'">
                  {{ row.mode_label }}
                </el-tag>
                <!-- 每个场次在自己开场时就停止收人，活动整体可能还在报名期 -->
                <div v-if="act.status === 'published' && !row.enroll_open" class="dim">
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
            <el-table-column label="操作" width="230" align="right">
              <template #default="{ row }">
                <template v-if="row.my_enrollment">
                  <!-- 调剂意愿：点一下即可改，只作为负责老师排班时的参考 -->
                  <div style="margin-bottom:4px">
                    <el-tooltip content="点击切换。仅供负责老师参考，不代表岗位不会被调整"
                                placement="top">
                      <el-tag size="small" effect="plain" style="cursor:pointer"
                              :type="row.my_enrollment.allow_reassign ? 'success' : 'info'"
                              @click="toggleReassign(row)">
                        {{ row.my_enrollment.allow_reassign ? '接受调剂' : '不接受调剂' }}
                      </el-tag>
                    </el-tooltip>
                  </div>
                  <!-- 直接定二级 -->
                  <template v-if="row.mode === 'pick'">
                    <!-- 有岗位：状态标签（退选/确认在对应岗位行原地操作） -->
                    <template v-if="myStationId(row)">
                      <el-tag v-if="row.my_enrollment.status === 'enrolled'" type="success">
                        已选中{{ myStationName(row) ? '·' + myStationName(row) : '' }}
                      </el-tag>
                      <el-tag v-else-if="row.my_enrollment.status === 'waitlisted'" type="warning">
                        候补中 · 第 {{ row.my_enrollment.waitlist_position }} 位
                      </el-tag>
                      <template v-else>
                        <el-tag type="danger">转正确认剩</el-tag>&nbsp;
                        <el-countdown format="HH:mm:ss" :value="deadlineTs(row)"
                                      style="vertical-align:middle" />
                        <span class="dim" style="margin-left:6px">在对应岗位行确认</span>
                      </template>
                    </template>
                    <!-- 无岗位（管理员补录/历史数据）：操作留在场次行 -->
                    <template v-else>
                      <el-tag v-if="row.my_enrollment.status === 'enrolled'" type="success">
                        已选中（岗位待分配）
                      </el-tag>
                      <el-tag v-else-if="row.my_enrollment.status === 'waitlisted'" type="warning">
                        候补中 · 第 {{ row.my_enrollment.waitlist_position }} 位
                      </el-tag>
                      <template v-else>
                        <el-countdown format="HH:mm:ss" :value="deadlineTs(row)"
                                      style="vertical-align:middle;margin-right:8px">
                          <template #prefix>
                            <el-tag type="danger" size="small">转正确认剩</el-tag>&nbsp;
                          </template>
                        </el-countdown>
                        <el-button type="primary" size="small"
                                   @click="confirmPromo(row)">确认转正</el-button>
                      </template>
                      <el-button v-if="row.my_enrollment.status === 'enrolled'"
                                 size="small" type="danger" plain
                                 :disabled="!canCancel(row)" @click="cancelEnroll(row)">
                        退选
                      </el-button>
                      <el-button v-else-if="row.my_enrollment.status === 'waitlisted'"
                                 size="small" type="danger" plain @click="cancelEnroll(row)">
                        退出候补
                      </el-button>
                    </template>
                  </template>
                  <!-- 截止后统一分配：按钮留在场次行 -->
                  <template v-else>
                    <el-tag v-if="row.my_enrollment.status === 'enrolled'" type="success">
                      已选中
                    </el-tag>
                    <el-tag v-else-if="row.my_enrollment.status === 'waitlisted'" type="warning">
                      候补中 · 第 {{ row.my_enrollment.waitlist_position }} 位
                    </el-tag>
                    <template v-else>
                      <el-countdown format="HH:mm:ss" :value="deadlineTs(row)"
                                    style="vertical-align:middle;margin-right:8px">
                        <template #prefix>
                          <el-tag type="danger" size="small">转正确认剩</el-tag>&nbsp;
                        </template>
                      </el-countdown>
                      <el-button type="primary" size="small"
                                 @click="confirmPromo(row)">确认转正</el-button>
                    </template>
                    <el-button v-if="row.my_enrollment.status === 'enrolled'"
                               size="small" type="danger" plain
                               :disabled="!canCancel(row)" @click="cancelEnroll(row)">
                      退选
                    </el-button>
                    <el-button v-else-if="row.my_enrollment.status === 'waitlisted'"
                               size="small" type="danger" plain @click="cancelEnroll(row)">
                      退出候补
                    </el-button>
                  </template>
                </template>
                <template v-else-if="openFor(row)">
                  <el-tooltip v-if="row.mode === 'pick'"
                              content="点击行首箭头展开，选择一个岗位" placement="top">
                    <el-button size="small" disabled>展开选择岗位</el-button>
                  </el-tooltip>
                  <el-button v-else type="primary" size="small" @click="doEnroll(row, null)">
                    {{ enrollText(row) }}
                  </el-button>
                </template>
                <el-tag v-else type="info" size="small">{{ closedText(row) }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 右：冲突提示 / 变更记录 -->
      <el-col :span="7">
        <el-alert v-if="act.my_time_conflicts?.length" type="error" :closable="false"
                  style="margin-bottom:14px">
          <p style="margin:0 0 4px"><b>您存在时段重叠的既有报名：</b></p>
          <p v-for="c in act.my_time_conflicts" :key="c.enrollment_id" style="margin:2px 0;font-size:12px">
            「{{ c.activity_title }}」{{ c.shift_name }}（{{ c.time }}）
          </p>
        </el-alert>
        <el-card shadow="never">
          <template #header><b>变更记录</b><span class="dim" style="font-weight:400;margin-left:6px">公开透明 · 全程留痕</span></template>
          <el-timeline v-if="act.change_logs?.length" style="padding-left:2px">
            <el-timeline-item v-for="l in act.change_logs" :key="l.id"
                              :timestamp="fmtDT(l.created_at)" placement="top"
                              :type="l.action === 'delete' ? 'danger' : 'primary'">
              <b style="font-size:13px">{{ l.actor_name }}</b>
              <span class="dim"> {{ l.summary }}</span>
              <div v-for="(ch, i) in l.changes" :key="i" class="dim" style="margin-top:2px">
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
            · 另有 {{ groupDetail.data.held_seats }} 个名额因临近退选被扣留，不再释放</span>
        </el-descriptions-item>
        <el-descriptions-item label="剩余名额">{{ groupDetail.data.remaining }}</el-descriptions-item>
        <el-descriptions-item label="说明">{{ groupDetail.data.note || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 岗位详情 -->
    <el-dialog v-model="stationDetail.visible" :title="(stationDetail.data?.name || '') + ' · 岗位详情'" width="480">
      <el-descriptions v-if="stationDetail.data" :column="1" border>
        <el-descriptions-item label="所属场次">{{ stationDetail.groupName }}（{{ stationDetail.groupTime }}）</el-descriptions-item>
        <el-descriptions-item label="服务地点">{{ stationDetail.data.location || '待定' }}</el-descriptions-item>
        <el-descriptions-item label="名额">
          {{ stationDetail.data.enrolled }}/{{ stationDetail.data.quota }}
          <span class="dim">（岗位之间互斥，一场次一人一个岗位）</span>
        </el-descriptions-item>
        <el-descriptions-item label="条件要求">{{ stationDetail.data.requirement_desc }}</el-descriptions-item>
        <el-descriptions-item label="要求说明">{{ stationDetail.data.requirement_note || '—' }}</el-descriptions-item>
        <el-descriptions-item label="培训内容">
          <template v-if="stationDetail.data.training_visible">
            {{ stationDetail.data.training_note || '—' }}
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
            {{ stationDetail.data.leader_name || '尚未产生（报名时可表态愿意担任）' }}
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
               : (enrollDlg.shift.mode === 'assign' ? '报名截止后由青协统一分配' : '无需选择') }}
          </el-descriptions-item>
          <el-descriptions-item v-if="enrollExtra.length" label="还需参加">
            <div v-for="s in enrollExtra" :key="s.id" style="line-height:1.7">
              {{ s.kind_label }}：{{ s.time_text }}
              <span class="dim">{{ s.location || act.location || '地点待定' }}</span>
            </div>
          </el-descriptions-item>
          <el-descriptions-item label="结果">
            <el-tag :type="willWaitlist ? 'warning' : 'success'" size="small">
              {{ willWaitlist ? '名额已满，将加入候补队列' : '名额充足，报名即生效' }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <el-alert v-if="enrollExtra.length && !willWaitlist" type="info" show-icon
                  :closable="false" style="margin-top:10px"
                  title="以上培训/彩排会随本次报名一并报上"
                  description="它们各自需要到场签到；退选本场次时会一并退出。请确认这些时间你都能到。" />

        <div class="reassign-box">
          <el-checkbox v-model="enrollDlg.allowReassign" :disabled="willWaitlist">
            接受岗位调剂
          </el-checkbox>
          <div v-if="willWaitlist" class="dim" style="margin-top:2px">
            候补必须接受调剂：有空位的岗位才好把排队的人调过去
          </div>
          <el-checkbox v-if="enrollDlg.position?.needs_leader"
                       v-model="enrollDlg.leaderWilling" style="display:block;margin-top:6px">
            愿意担任本岗位组长
          </el-checkbox>
          <div v-if="enrollDlg.position?.needs_leader" class="dim" style="margin-top:2px">
            该岗位需要一名组长，按报名先后从有意愿的人里产生；负责老师可改判
          </div>
          <p class="dim">
            仅供负责老师排班参考：不勾选也可能被调整，但名单上会标注你的意愿。
          </p>
        </div>

        <el-alert v-if="needLateWarn(enrollDlg.shift)" type="warning" :closable="false"
                  style="margin-top:10px"
                  title="距该场次开始已不足 24 小时：报名后再退选将扣除信用分，且名额不再释放" />
      </template>
      <template #footer>
        <el-button @click="enrollDlg.visible = false">再想想</el-button>
        <el-button type="primary" :loading="enrollDlg.submitting" @click="submitEnroll">
          确认{{ willWaitlist ? '加入候补' : '报名' }}
        </el-button>
      </template>
    </el-dialog>

    <ProfileDialog ref="profileDialog" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtDT } from '../api'
import { TYPE_COLORS } from '../store'
import ProfileDialog from '../components/ProfileDialog.vue'
import MaterialList from '../components/MaterialList.vue'
import { useNarrow } from '../composables/useNarrow'
import { Warning } from '@element-plus/icons-vue'

const route = useRoute()
const narrow = useNarrow()
const act = ref(null)
const groupDetail = ref({ visible: false, data: null })
const enrollDlg = ref({ visible: false, shift: null, position: null,
                        leaderWilling: true,
                        allowReassign: true, submitting: false })
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

const typeColor = t => TYPE_COLORS[t]?.color
// 与广场共用同一套：状态按时间算，颜色也要一致，否则两处对同一个活动说法不同
const STATUS_TYPES = {
  draft: 'warning', upcoming: 'primary', published: 'success',
  closed: 'warning', ongoing: 'danger', finished: 'info',
}
const statusType = computed(() => STATUS_TYPES[act.value?.display_status] || 'info')

function pct(g) {
  return Math.min(100, Math.round((g.enrolled / Math.max(1, g.capacity)) * 100))
}
function needLateWarn(g) {
  const st = new Date(g.start_time.replace(' ', 'T'))
  return Date.now() > st.getTime() - 24 * 3600 * 1000
}
function deadlineTs(g) {
  return new Date(g.my_enrollment.confirm_deadline.replace(' ', 'T').slice(0, 19)).getTime()
}
// 我在这个场次的岗位对象（报名后才有；统一分配的场次分配前为 null）
// 培训/彩排请假：服务场次不来是退选（有信用分规则），这里只管附属时段
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
      `「${s.name}」${s.time_text}。请说明无法参加的原因，负责老师会看到。`,
      `${s.kind_label}请假`,
      { inputPlaceholder: '如：与专业课时间冲突', inputPattern: /.{2,}/,
        inputErrorMessage: '请简单写一下原因',
        confirmButtonText: '提交请假', cancelButtonText: '再想想' })
    reason = r.value
  } catch { return }
  await api.post(`/api/activities/enrollments/${s.my_enrollment_id}/excuse`,
                 { excused: true, reason })
  ElMessage.success('已请假，负责老师会看到')
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
function openFor(g) {
  return act.value?.status === 'published' && g.enroll_open
}
function closedText(g) {
  if (act.value?.status !== 'published') return '不在报名期'
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

async function load() {
  act.value = await api.get(`/api/activities/${route.params.id}`)
}
onMounted(load)

function doEnroll(g, position) {
  if (g.mode === 'pick' && g.positions.length && !position) {
    return ElMessage.warning('请先展开该场次，选择一个岗位')
  }
  enrollDlg.value = { visible: true, shift: g, position, allowReassign: true,
                      leaderWilling: true,
                      submitting: false }
}

async function submitEnroll() {
  const { shift: g, position, allowReassign, leaderWilling } = enrollDlg.value
  enrollDlg.value.submitting = true
  try {
    const d = await api.post(
      `/api/activities/${act.value.id}/shifts/${g.id}/enroll`,
      { position_id: position ? position.id : null,
        allow_reassign: allowReassign, leader_willing: leaderWilling })
    enrollDlg.value.visible = false
    ElMessage.success(d.message)
    load()
  } catch (e) {
    // 资料未完善被拦截：提示后弹出资料表单，补全后即可报名
    if (e?.message?.includes('完善个人信息')) {
      enrollDlg.value.visible = false
      try {
        await ElMessageBox.alert(
          '报名前需先完善个人信息（手机号/微信号/身份证号），请在接下来的弹窗中填写并保存。',
          '需先完善个人信息',
          { type: 'warning', confirmButtonText: '去完善' })
      } catch { /* 关闭提示也照样打开表单 */ }
      openProfileDialog()
      return
    }
    if (e.data && e.data.conflicts) {
      enrollDlg.value.visible = false
      const list = e.data.conflicts
        .map(c => `「${c.activity_title}」${c.shift_name}（${c.time}）`
                  + (c.waiting ? '　<i>候补中</i>' : '')).join('<br>')
      ElMessageBox.alert(list, '与已有报名时间冲突',
                         { dangerouslyUseHTMLString: true, type: 'error' })
    }
  } finally {
    enrollDlg.value.submitting = false
  }
}

// 调剂意愿随时可改：它只是给负责老师的参考
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
      late ? '距开始不足24小时：退选将扣除信用分5分，且该名额会被扣留、不再释放给任何人，'
             + '你本人之后也无法再报名这个场次。确定退选吗？'
           : '正常退选不扣信用分，名额将按候补顺序自动递补。',
      '确认退选', { type: 'warning', confirmButtonText: '确定退选', cancelButtonText: '取消' })
  } catch { return }
  const d = await api.del(`/api/activities/enrollments/${enrId}`)
  ElMessage[d.effect === 'penalized' ? 'warning' : 'success'](d.message)
  load()
}

async function confirmPromo(g) {
  const d = await api.post(
    `/api/activities/enrollments/${g.my_enrollment.enrollment_id}/confirm-promotion`)
  ElMessage.success(d.message)
  load()
}

const profileDialog = ref(null)
const openProfileDialog = () => profileDialog.value.open(true)
</script>

<style scoped>
/* 页头：宽屏标题与标签同一行，窄屏标签落到下一行 */
.hd-title { font-weight: 600; }
.hd-tags { display: inline-flex; gap: 6px; margin-left: 10px; vertical-align: middle; }
@media (max-width: 860px) {
  .hd-title { display: block; font-size: 16px; line-height: 1.5; }
  .hd-tags { display: flex; flex-wrap: wrap; margin: 4px 0 0; }
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
.m-pos-meta { margin-top: 3px; line-height: 1.6; }
.m-pos-act { margin-top: 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.m-pos-why { margin-top: 6px; font-size: 12px; color: #e6a23c; }
.m-reassign { margin-top: 10px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.req-blocked { color: #e6a23c; }
.mine-card { margin-top: 10px; border-left: 3px solid var(--el-color-success); }
.mine-title { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.mine-note { margin: 4px 0; font-size: 13px; line-height: 1.75; color: #606266; }
.group-card { margin-bottom: 14px; border-radius: 12px; }
.g-head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.reassign-box { margin-top: 14px; padding: 10px 12px; border-radius: 8px; background: #f7f8fa; }
.reassign-box .dim { margin: 6px 0 0; font-size: 12px; line-height: 1.7; }

/* 活动级的要求与保障 */
.side-note {
  border-left: 3px solid var(--el-color-warning); background: #fdf6ec;
  padding: 8px 12px; border-radius: 0 8px 8px 0; margin-bottom: 8px;
}
.side-note.benefit { border-left-color: var(--el-color-success); background: #f0f9eb; }
.side-note b { font-size: 13px; }
.side-note p { margin: 2px 0 0; font-size: 13px; line-height: 1.75;
               white-space: pre-wrap; color: #606266; }

/* 培训 / 彩排：跟在场次后面的附加时段 */
.att-list { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.att-item {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 13px; padding: 6px 10px; border-radius: 8px; background: #f7f8fa;
}
.att-item.mine { background: var(--el-color-primary-light-9); }

/* 仅场次：没有可展开的岗位，隐藏展开箭头 */
</style>
