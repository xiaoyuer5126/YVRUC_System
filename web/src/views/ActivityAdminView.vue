<template>
  <div class="page page-wide" v-if="loading" v-loading="loading" style="min-height:300px"></div>
  <div class="page page-wide" v-else-if="notFound">
    <el-result icon="warning" title="活动不存在"
               sub-title="可能已被删除，或演示数据重置后 ID 已变化">
      <template #extra>
        <el-button type="primary" @click="$router.push('/manage')">返回活动列表</el-button>
      </template>
    </el-result>
  </div>
  <div class="page page-wide" v-else-if="loadError">
    <el-result icon="error" :title="'加载失败：' + loadError">
      <template #extra>
        <el-button type="primary" @click="loadAll">重试</el-button>
        <el-button @click="$router.push('/manage')">返回活动列表</el-button>
      </template>
    </el-result>
  </div>
  <div class="page page-wide" v-else-if="act">
    <el-page-header @back="$router.push('/manage')" title="返回列表">
      <template #content>
        <b>{{ act.title }}</b>
        <el-tag size="small" style="margin-left:8px" :type="{published:'success',closed:'info',draft:'warning',finished:'info'}[act.status]">
          {{ act.status_label }}
        </el-tag>
        <span class="dim" style="margin-left:10px">{{ act.time_text }} · {{ act.location }}</span>
      </template>
      <template #extra>
        <el-button v-if="structureLocked" type="warning" plain @click="startEditing">
          {{ editLabel }}
        </el-button>
        <!-- 草稿是首次发布，编辑态是发布改动，对管理者是同一件事：改完了，让学生看到 -->
        <el-popconfirm v-if="canPublish" :title="publishConfirm"
                       @confirm="saveAndPublish">
          <template #reference>
            <el-button type="success" :loading="lockBusy">保存并发布</el-button>
          </template>
        </el-popconfirm>
        <el-button @click="editBase = true">编辑基本信息</el-button>
        <el-popconfirm v-if="act.status === 'published'" title="截止后不再接受新报名，确认？"
                       @confirm="transition('close')">
          <template #reference><el-button type="warning">截止报名</el-button></template>
        </el-popconfirm>
        <el-popconfirm v-else-if="['published','closed'].includes(act.status)"
                       title="结束活动，确认？" @confirm="transition('finish')">
          <template #reference><el-button type="danger">结束活动</el-button></template>
        </el-popconfirm>
      </template>
    </el-page-header>

    <el-alert v-if="act.editing" type="warning" :closable="false" show-icon
              style="margin:12px 0"
              title="本活动正在调整安排，学生已暂停报名与退选"
              :description="`改完请点右上角「保存并发布」恢复报名，并把改动汇总推送给已报名的志愿者；
                             ${lockHours} 小时未保存会自动解冻。`" />

    <el-tabs v-model="tab" style="margin-top:8px">
      <!-- ============ 岗位树 ============ -->
      <el-tab-pane label="岗位树" name="tree">
        <el-alert type="info" :closable="false" style="margin-bottom:12px">
          场次只管时间，岗位管具体的事。两种模式：<b>报名即选岗位</b>（名额与候补都在各个岗位上）
          / <b>截止后统一分配</b>（报名只锁时段，名额记在场次上，截止后一键分配）。
          同场次的岗位互斥，一人只占一个；点击岗位名称可查看详情。
        </el-alert>
        <el-alert v-if="structureLocked" type="info" show-icon :closable="false"
                  style="margin-bottom:12px">
          <template #title>
            当前为只读——场次与岗位需要进入调整状态才能修改
            <el-button type="warning" size="small" style="margin-left:10px"
                       @click="startEditing">{{ editLabel }}</el-button>
          </template>
          <div style="line-height:1.8">
            {{ act.status === 'published'
               ? '已发布的活动改到一半会被学生看到，所以改动要成批进行：进入调整后报名暂停，改完点「保存并发布」，这批改动会一次性通知已报名的志愿者。'
               : '报名已结束，进入调整只是把改动成批起来——改完点「保存并发布」，会把这批改动一次性通知已报名的志愿者。' }}
          </div>
        </el-alert>

        <el-alert v-if="shiftsNeedingPositions.length" type="error" show-icon
                  :closable="false" style="margin-bottom:12px"
                  :title="`有 ${shiftsNeedingPositions.length} 个场次还没有岗位，学生报不了名`"
                  :description="shiftsNeedingPositions.map(g => g.name).join('、')
                                + '：请为这些场次添加岗位；名额与候补都记在岗位上，'
                                + '没有岗位就等于没有名额。'" />

        <div v-for="g in shifts" :key="g.id" class="group-card"
             :class="{ 'needs-positions': needsPositions(g) }">
          <div class="g-head">
            <el-link type="primary" :underline="false" style="font-weight:600;font-size:14px"
                     @click="showGroupDetail(g)">{{ g.name }}</el-link>
            <el-tag size="small" effect="plain"
                    :type="g.mode === 'assign' ? 'warning' : 'info'">
              {{ g.mode_label }}
            </el-tag>
            <span class="dim">{{ g.start_time.slice(5,16).replace('T',' ') }}
              ~ {{ g.end_time.slice(5,16).replace('T',' ') }}</span>
            <span>{{ g.mode === 'assign' ? `上限 ${g.capacity}` : `岗位合计 ${sumQuota(g)}` }}</span>
            <el-tag v-if="needsPositions(g)" size="small" type="danger" effect="dark">
              未设岗位
            </el-tag>
            <el-tooltip v-else-if="g.unassigned_count" placement="top"
                        content="已确认报名但还没拿到岗位的人数；增加岗位名额或运行一键分配">
              <el-tag size="small" type="warning" effect="dark">
                余 {{ g.unassigned_count }} 人待分配
              </el-tag>
            </el-tooltip>
            <span style="margin-left:auto">
              <el-button size="small" :disabled="structureLocked"
                         @click="openShift(g)">编辑</el-button>
              <el-button size="small" plain :disabled="structureLocked"
                         @click="openCopyPositions(g)">复制岗位到其它场次</el-button>
              <el-button size="small" type="primary" plain :disabled="structureLocked"
                         @click="openStation(null, g)">添加岗位</el-button>
              <el-popconfirm :title="`删除场次「${g.name}」？`" @confirm="delShift(g)"
                             :disabled="structureLocked">
                <template #reference>
                  <el-button size="small" type="danger" plain
                             :disabled="structureLocked">删除</el-button>
                </template>
              </el-popconfirm>
            </span>
          </div>

          <el-table v-if="g.positions.length" :data="g.positions" size="small">
            <el-table-column label="岗位" min-width="130">
              <template #default="{row}">
                <el-link type="primary" :underline="false"
                         @click="showStationDetail(g, row)">{{ row.name }}</el-link>
              </template>
            </el-table-column>
            <el-table-column prop="location" label="服务地点" min-width="120" />
            <el-table-column label="名额/已确认" width="130">
              <template #default="{row}">{{ row.quota }} / {{ row.confirmed }}</template>
            </el-table-column>
            <el-table-column prop="requirement_desc" label="要求" min-width="140" />
            <el-table-column prop="training_note" label="培训内容" min-width="160" show-overflow-tooltip />
            <el-table-column label="操作" width="130">
              <template #default="{row}">
                <el-button size="small" link type="primary" :disabled="structureLocked"
                           @click="openStation(row, g)">编辑</el-button>
                <el-popconfirm title="删除该岗位？" @confirm="delStation(row)"
                               :disabled="structureLocked">
                  <template #reference>
                    <el-button size="small" link type="danger"
                               :disabled="structureLocked">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
          <p v-else class="dim" style="margin:6px 0 0">
            {{ g.mode === 'assign'
               ? '截止后统一分配：先建好岗位定名额，报名截止后到「报名与调配」一键分配。'
               : '报名即选岗位：请先建好岗位，名额与候补都在岗位上。' }}
          </p>
        </div>
        <el-button style="width:100%;margin-top:4px" plain dashed
                   :disabled="structureLocked" @click="openShift(null)">
          <el-icon><Plus /></el-icon>&nbsp;新建场次
        </el-button>
      </el-tab-pane>

      <!-- ============ 报名与调配 ============ -->
      <el-tab-pane name="roster">
        <template #label>报名与调配
          <el-badge v-if="pendingPromo" value="!" type="danger" />
        </template>
        <div class="toolbar" style="margin-bottom:12px">
          <el-button type="primary" plain @click="openNotify">
            <el-icon><Bell /></el-icon>&nbsp;发送通知
          </el-button>
          <span class="dim">向本活动已确认报名的志愿者发送企业微信通知（演示模式下进入其「消息中心」）</span>
        </div>
        <el-row :gutter="14">
          <el-col :span="7">
            <el-select v-model="currentGroupId" style="width:100%;margin-bottom:10px">
              <el-option v-for="g in shifts" :key="g.id"
                         :value="g.id" :label="`${g.name}（${g.confirmed}/${g.capacity}）`" />
            </el-select>
            <template v-if="currentShift">
              <el-card shadow="never" class="wl-card">
                <template #header><b>候补队列</b>
                  <span class="dim">（按报名时间排序，正常退选自动递补）</span></template>
                <div v-for="w in roster.waitlist" :key="w.enrollment_id" class="wl-row">
                  <el-tag size="small" circle>{{ w.queue_position }}</el-tag>
                  {{ w.user.name }}
                  <span class="dim">{{ w.user.student_id }}</span>
                  <el-tag v-if="w.status === 'pending_confirm'" size="small" type="danger">
                    待确认 {{ w.confirm_deadline?.slice(11,16) }} 前
                  </el-tag>
                </div>
                <el-empty v-if="!roster.waitlist.length" description="暂无候补" :image-size="50" />
              </el-card>
              <el-card shadow="never" class="wl-card" v-if="roster.cancelled.length">
                <template #header><b>退选记录（近20条）</b></template>
                <div v-for="x in roster.cancelled" :key="x.enrollment_id" class="wl-row dim">
                  {{ x.user.name }}
                  <el-tag size="small" :type="x.status === 'cancelled' && x.confirm_deadline ? 'info':'info'"
                          effect="plain">{{ cancelType(x) }}</el-tag>
                </div>
              </el-card>
            </template>
          </el-col>
          <el-col :span="17">
            <template v-if="currentShift">
              <div class="section-title" style="margin-top:0">已确认名单（{{ roster.confirmed.length }} 人）
                <el-tooltip content="依据签到任务实时回流"><el-icon><InfoFilled /></el-icon></el-tooltip>
              </div>
              <el-table :data="roster.confirmed" size="small" max-height="300" style="margin-bottom:14px">
                <el-table-column label="姓名" width="110">
              <template #default="{row}">
                {{ row.user.name }}
                <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark"
                        style="margin-left:4px">组长</el-tag>
                <el-tooltip v-else-if="row.leader_willing" content="愿意担任组长">
                  <el-tag size="small" type="warning" effect="plain"
                          style="margin-left:4px">愿任</el-tag>
                </el-tooltip>
              </template>
            </el-table-column>
                <el-table-column prop="user.student_id" label="学号" width="110" />
                <el-table-column prop="user.college" label="学院" width="130" />
                <el-table-column prop="user.phone" label="手机号" width="120" />
                <el-table-column label="调剂意愿" width="100">
                  <template #default="{row}">
                    <el-tag size="small" effect="plain"
                            :type="row.allow_reassign ? 'success' : 'info'">
                      {{ row.allow_reassign ? '接受' : '不接受' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="岗位" min-width="130">
                  <template #default="{row}">
                    {{ row.position_name || '待分配' }}
                    <el-tag v-if="row.assign_forced" size="small" type="danger">强制</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="到岗" width="80">
                  <template #default="{row}">
                    <el-tag v-if="row.checked_in" type="success" size="small" effect="light">✓</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="组长" width="96">
                  <template #default="{row}">
                    <el-button v-if="row.is_leader" size="small" link type="warning"
                               @click="setLeader(row, false)">取消</el-button>
                    <el-tooltip v-else-if="canBeLeader(row)" placement="top"
                                content="改判为本岗位组长；原组长会被自动摘掉">
                      <el-button size="small" link @click="setLeader(row, true)">
                        指定
                      </el-button>
                    </el-tooltip>
                    <span v-else class="dim">—</span>
                  </template>
                </el-table-column>
                <el-table-column v-if="roster.positions.length" label="调岗" width="210">
                  <template #default="{row}">
                    <el-select v-model="moveSel[row.enrollment_id]" size="small"
                               clearable placeholder="调至岗位…" style="width:140px">
                      <el-option v-for="s in roster.positions" :key="s.id" :value="s.id"
                                 :label="`${s.name}（余${Math.max(0, s.quota - s.confirmed)}）`" />
                    </el-select>
                    <el-button size="small" link type="primary"
                               :disabled="moveSel[row.enrollment_id] === undefined"
                               @click="applyMove(row)">应用</el-button>
                  </template>
                </el-table-column>
              </el-table>

              <AssignPanel v-if="currentShift" :shift="currentShift"
                           :unassigned-count="unassignedCount" @reload="loadAll" />
            </template>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ============ 签到管理 ============ -->
      <el-tab-pane label="签到管理" name="checkin" lazy>
        <CheckinPanel v-if="currentShift" :shift="currentShift" :activity-id="act.id" />
        <el-empty v-else description="请先创建场次" />
      </el-tab-pane>

      <!-- ============ 导出与时长 ============ -->
      <el-tab-pane label="导出与时长" name="export" lazy>
        <div class="toolbar">
          <el-button type="primary" @click="doExport">
            <el-icon><Download /></el-icon>&nbsp;导出排班表 Excel
          </el-button>
          <span class="dim">按场次分 Sheet：姓名、学号、学院、手机号、岗位、时间、地点、签到状态——可直接交付现场负责人。
            生产版将强制加密并由企业微信单独发送密码；本次下载已计入审计。</span>
        </div>
        <el-divider />
        <div class="toolbar">
          <el-button @click="hoursPreview()" :loading="hoursLoading">基于签到记录计算应录时长</el-button>
          <el-button type="primary" plain :disabled="!hoursSel.length" @click="confirmSelectedHours">
            {{ hoursSel.length ? `确认选中时长（${hoursSel.length} 人）` : '确认选中时长' }}
          </el-button>
          <el-tooltip content="只确认正常签退的记录；未签退/自动兜底的需逐条核对后确认" placement="top">
            <el-button type="primary" :disabled="!hours.length" @click="confirmAllHours">
              确认正常签退的
            </el-button>
          </el-tooltip>
          <el-button type="success" @click="uploadHours" :disabled="!confirmedCount">回传数智人大</el-button>
          <el-tag size="small" effect="plain">对接状态见「集成设置」（当前为演示回执）</el-tag>
        </div>
        <el-table v-if="hours.length" :data="hours" size="small" style="margin-top:10px"
                  max-height="360" @selection-change="onHoursSelect">
          <el-table-column type="selection" width="40" />
          <el-table-column label="姓名" width="110">
              <template #default="{row}">
                {{ row.user.name }}
                <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark"
                        style="margin-left:4px">组长</el-tag>
                <el-tooltip v-else-if="row.leader_willing" content="愿意担任组长">
                  <el-tag size="small" type="warning" effect="plain"
                          style="margin-left:4px">愿任</el-tag>
                </el-tooltip>
              </template>
            </el-table-column>
          <el-table-column prop="user.student_id" label="学号" width="110" />
          <el-table-column prop="user.college" label="学院" width="130" />
          <el-table-column label="时长(h)" width="170">
            <template #default="{row}">
              <el-input-number v-model="row.editHours" size="small"
                               :min="0" :step="0.5" style="width:130px" />
            </template>
          </el-table-column>
          <el-table-column label="标记" width="150">
            <template #default="{row}">
              <el-tooltip v-if="row.pending" content="未签退，暂按当前时间估算；场次结束2小时后自动按场次结束时间计">
                <el-tag size="small" type="warning" effect="plain">待签退</el-tag>
              </el-tooltip>
              <el-tooltip v-else-if="row.auto_checkout"
                          content="超时未签退，已按场次结束时间兜底——到场扫码即走的人也会算满时长，需人工核对">
                <el-tag size="small" type="danger" effect="plain">待核·自动兜底</el-tag>
              </el-tooltip>
              <span v-else class="dim">正常签退</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="150">
            <template #default="{row}">
              <el-tag v-if="row.status === 'uploaded'" type="success" effect="light">
                已回传 {{ row.upload_ref }}
              </el-tag>
              <el-tag v-else-if="row.status === 'confirmed'" type="warning" effect="light">已确认</el-tag>
              <el-tag v-else effect="info">待确认</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p class="dim" style="margin-top:8px">
          时长 = 签退 − 签到，向上取整至 0.5 小时。<b>未签退与自动兜底的记录标为「待核」</b>，
          一键确认会跳过它们——请核对实际服务情况后，勾选并修改数值再单独确认。
        </p>

        <!-- 手动添加时长 -->
        <div class="section-title" style="margin-top:18px">手动添加时长（不依赖签到，如场外支援/补录）</div>
        <el-card shadow="never">
          <div class="toolbar" style="margin-bottom:8px;align-items:flex-start">
            <el-select v-model="mh.userIds" multiple filterable collapse-tags
                       collapse-tags-tooltip placeholder="选择志愿者（可多选）"
                       style="width:320px">
              <el-option v-for="v in rosterOptions" :key="v.student_id"
                         :value="v.student_id"
                         :label="`${v.name}（${v.student_id}）${v.shift_name}`" />
            </el-select>
            <el-input-number v-model="mh.hours" :min="0.5" :step="0.5" style="width:120px" />
            <el-input v-model="mh.note" placeholder="备注（如：场外支援）" style="width:220px" />
            <el-button type="primary" :disabled="!mh.userIds.length"
                       @click="addManualHours">
              批量添加{{ mh.userIds.length ? `（${mh.userIds.length} 人）` : '' }}
            </el-button>
            <span class="dim">同一时长与备注一次性写给所选志愿者</span>
          </div>
          <el-table v-if="manualHours.length" :data="manualHours" size="small">
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="student_id" label="学号" width="120" />
            <el-table-column prop="hours" label="时长(h)" width="90" />
            <el-table-column prop="note" label="备注" min-width="160" />
            <el-table-column label="状态" width="110">
              <template #default="{row}">
                <el-tag v-if="row.status === 'uploaded'" type="success" effect="light">已回传</el-tag>
                <el-tag v-else type="warning" effect="light">已确认</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80">
              <template #default="{row}">
                <el-button v-if="row.status !== 'uploaded'" size="small" link type="danger"
                           @click="delManualHours(row)">删除</el-button>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ============ 管理者 ============ -->
      <el-tab-pane label="签收情况" name="acks" lazy>
        <div class="dim" style="margin-bottom:10px">
          只列出「要求签收」的群发通知。签收 = 志愿者点过「我已知悉」，
          比「已读」硬——已读只说明他打开过消息中心。
        </div>
        <el-empty v-if="!ackBatches.length"
                  description="还没有要求签收的通知；发送通知时勾选「要求志愿者签收」即可" />
        <el-card v-for="b in ackBatches" :key="b.batch_id" shadow="never"
                 style="margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <b>{{ b.title }}</b>
            <span class="dim">{{ fmtDT(b.created_at) }}</span>
            <el-tag size="small" :type="b.acked === b.total ? 'success' : 'warning'">
              已签收 {{ b.acked }}/{{ b.total }}
            </el-tag>
            <el-button v-if="b.acked < b.total" size="small" type="primary" plain
                       style="margin-left:auto" @click="remindBatch(b)">
              一键提醒未签收（{{ b.total - b.acked }} 人）
            </el-button>
          </div>
          <el-progress :percentage="Math.round(b.acked / b.total * 100)"
                       :status="b.acked === b.total ? 'success' : undefined"
                       style="margin-top:10px" />
          <div v-if="b.pending.length" style="margin-top:10px">
            <span class="dim">未签收：</span>
            <el-tag v-for="p in b.pending" :key="p.student_id" size="small"
                    type="info" effect="plain" style="margin:2px 4px 2px 0">
              {{ p.name }}（{{ p.student_id }}）
            </el-tag>
          </div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="管理者" name="managers" lazy>
        <el-alert type="info" :closable="false" style="margin-bottom:12px">
          一个活动可由多人共同管理：创建者自动成为管理者（<b>不可被移除</b>，但权限上不特殊）；
          管理者之间权限完全相同，可互相添加/移除；被添加者须持有 <b>老师/青协</b> 身份。
        </el-alert>
        <el-card shadow="never">
          <div class="toolbar" style="margin-bottom:10px">
            <el-input v-model="newMgrSid" placeholder="输入学号添加管理者" style="width:220px" />
            <el-button type="primary" @click="addManager">添加管理者</el-button>
          </div>
          <el-table :data="managers" size="small">
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="student_id" label="学号" width="120" />
            <el-table-column prop="role_label" label="身份" min-width="140" />
            <el-table-column label="角色" width="110">
              <template #default="{row}">
                <el-tag v-if="row.is_creator" size="small" type="warning" effect="light">创建者</el-tag>
                <el-tag v-else size="small" effect="plain">管理者</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{row}">
                <el-popconfirm v-if="!row.is_creator && row.id !== auth.user?.id"
                               title="移除该管理者的管理权限？"
                               @confirm="removeManager(row)">
                  <template #reference>
                    <el-button size="small" link type="danger">移除</el-button>
                  </template>
                </el-popconfirm>
                <span v-else-if="row.is_creator" class="dim">—</span>
                <el-tooltip v-else content="不能移除自己的管理权限，可由其他管理者操作">
                  <span class="dim">自己</span>
                </el-tooltip>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ============ 变更记录 ============ -->
      <el-tab-pane label="变更记录" name="logs" lazy>
        <el-timeline v-if="logs.length">
          <el-timeline-item v-for="l in logs" :key="l.id" placement="top"
                            :timestamp="fmtDT(l.created_at)"
                            :type="l.action === 'delete' ? 'danger' : 'primary'">
            <b>{{ l.actor_name }}</b>
            <el-tag size="small" effect="plain" style="margin:0 6px">{{ actionLabel(l.action) }}</el-tag>
            <span>{{ l.summary }}</span>
            <div v-for="(ch, i) in l.changes" :key="i" class="dim" style="margin-top:2px">
              {{ ch.label }}：{{ ch.old || '空' }} → <b style="color:var(--el-color-primary)">{{ ch.new || '空' }}</b>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无操作记录" />
      </el-tab-pane>
    </el-tabs>

    <!-- 场次对话框 -->
    <el-dialog v-model="shiftDlg.visible" :title="shiftDlg.editing ? '编辑场次' : '新建场次'" width="520">
      <el-form label-width="100px">
        <el-form-item label="名称" required><el-input v-model="groupForm.name" placeholder="如：上午场" /></el-form-item>
        <el-form-item label="场次模式" required>
          <el-radio-group v-model="groupForm.mode">
            <el-radio-button value="pick">报名即选岗位</el-radio-button>
            <el-radio-button value="assign">截止后统一分配</el-radio-button>
          </el-radio-group>
          <div class="dim" style="margin-top:4px;line-height:1.7">
            <template v-if="groupForm.mode === 'pick'">
              场次只管时间；<b>名额与候补都在各个岗位上设置</b>，这里不设总人数。
            </template>
            <template v-else>
              报名只锁时段、不选岗位，名额与候补记在场次上；
              报名截止后改为「报名即选岗位」并建好岗位，再运行一键分配。
            </template>
          </div>
        </el-form-item>
        <el-form-item label="开始时间" required>
          <el-date-picker v-model="groupForm.start_time" type="datetime" style="width:100%" />
        </el-form-item>
        <el-form-item label="结束时间" required>
          <el-date-picker v-model="groupForm.end_time" type="datetime" style="width:100%" />
        </el-form-item>
        <!-- 名额与候补只属于「截止后统一分配」：报名即选岗位时它们在岗位上 -->
        <template v-if="groupForm.mode === 'assign'">
          <el-form-item label="人数上限" required>
            <el-input-number v-model="groupForm.capacity" :min="1" />
            <span class="dim" style="margin-left:10px">报名截止前先占住的总名额</span>
          </el-form-item>
          <el-form-item label="候补名额">
            <el-input-number v-model="groupForm.waitlist_quota" :min="0" />
            <span class="dim" style="margin-left:10px">满员后可排队的人数，0 = 不设候补</span>
          </el-form-item>
        </template>
        <el-form-item label="备注"><el-input v-model="groupForm.note" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="shiftDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="savingGroup" @click="saveShift">保存</el-button>
      </template>
    </el-dialog>

    <!-- 岗位对话框 -->
    <el-dialog v-model="positionDlg.visible" :title="positionDlg.editing ? '编辑岗位' : '新建岗位'" width="560">
      <el-form label-width="100px">
        <el-form-item label="岗位名称" required><el-input v-model="stationForm.name" placeholder="如：东门引导" /></el-form-item>
        <el-form-item label="服务地点"><el-input v-model="stationForm.location" /></el-form-item>
        <el-form-item label="名额" required>
          <el-input-number v-model="stationForm.quota" :min="1" />
          <span class="dim" style="margin-left:10px">名额与候补都在岗位上，场次不设总数</span>
        </el-form-item>
        <el-form-item label="候补名额">
          <el-input-number v-model="stationForm.waitlist_quota" :min="0" />
          <span class="dim" style="margin-left:10px">满员后可排队的人数，0 = 不设候补</span>
        </el-form-item>
        <el-form-item label="组长">
          <el-checkbox v-model="stationForm.needs_leader">本岗位需要一名组长</el-checkbox>
          <div class="dim">报名时志愿者可表态愿意担任，按报名先后自动产生；
            组长退选或被调走时自动从其余有意愿的人里递补。</div>
        </el-form-item>
        <el-form-item label="条件要求">
          <el-select v-model="stationForm.genderReq" clearable placeholder="性别限制（可选）" style="width:180px;margin-right:8px">
            <el-option value="女" label="限女生" /><el-option value="男" label="限男生" />
          </el-select>
          <el-select v-model="stationForm.collegeReq" multiple clearable collapse-tags
                     placeholder="限定学院（可选）" style="width:260px">
            <el-option v-for="c in colleges" :key="c" :value="c" :label="c" />
          </el-select>
          <div class="dim">硬性条件：不满足的志愿者<b>报名时就会被拦下</b>（岗位在活动页显示为
            「不符合条件」并说明原因），一键分配时同样不会被排入。</div>
        </el-form-item>
        <el-form-item label="要求原文"><el-input v-model="stationForm.requirement_note" placeholder="自然语言描述，展示用" /></el-form-item>
        <el-form-item label="培训内容"><el-input v-model="stationForm.training_note" /></el-form-item>
        <el-form-item v-if="positionDlg.editing" label="学习材料">
          <MaterialManager owner-type="position" :owner-id="positionDlg.editing.id"
                           :items="positionDlg.editing.attachments || []"
                           @changed="loadAll" />
        </el-form-item>
        <el-form-item label="">
          <div class="dim">岗位没有时间：时段随场次；同场次的岗位互斥，一人只占一个。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="positionDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="savingStation" @click="saveStation">保存</el-button>
      </template>
    </el-dialog>

    <!-- 岗位批量复制到其它场次 -->
    <el-dialog v-model="copyPosDlg.visible" title="复制岗位到其它场次" width="520">
      <p class="dim" style="margin-top:0">
        从「{{ copyPosDlg.source?.name }}」复制岗位（含名额、条件、组长设置与学习材料）。
        目标场次已有同名岗位的会跳过。
      </p>
      <el-form label-width="88px">
        <el-form-item label="复制哪些">
          <el-select v-model="copyPosDlg.positionIds" multiple collapse-tags
                     placeholder="留空 = 全部岗位" style="width:100%">
            <el-option v-for="p in copyPosDlg.source?.positions || []" :key="p.id"
                       :value="p.id" :label="`${p.name}（${p.quota}人）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="复制到" required>
          <el-select v-model="copyPosDlg.targetIds" multiple style="width:100%"
                     placeholder="选择目标场次">
            <el-option v-for="g in shifts.filter(x => x.id !== copyPosDlg.source?.id)"
                       :key="g.id" :value="g.id" :label="`${g.name}（${g.time_text}）`" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="copyPosDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="copyPosDlg.busy" @click="doCopyPositions">
          复制
        </el-button>
      </template>
    </el-dialog>

    <!-- 岗位详情（点击名称） -->
    <el-dialog v-model="gdVisible" :title="(gdData?.name || '') + ' · 场次详情'" width="460">
      <el-descriptions v-if="gdData" :column="1" border>
        <el-descriptions-item label="场次模式">{{ gdData.mode_label }}</el-descriptions-item>
        <el-descriptions-item label="时间">{{ gdData.time_text }}</el-descriptions-item>
        <el-descriptions-item label="人数">已确认 {{ gdData.confirmed }} / {{ gdData.capacity }}
          <span v-if="gdData.held_seats" style="color:#e6a23c">
            · {{ gdData.held_seats }} 个名额因临近退选被扣留（不再释放）</span>
          <span class="dim">（候补 {{ gdData.waitlist_count }} 人）</span></el-descriptions-item>
        <el-descriptions-item label="说明">{{ gdData.note || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
    <el-dialog v-model="sdVisible" :title="(sdData?.name || '') + ' · 岗位详情'" width="460">
      <el-descriptions v-if="sdData" :column="1" border>
        <el-descriptions-item label="所属场次">{{ sdGroupName }}</el-descriptions-item>
        <el-descriptions-item label="服务地点">{{ sdData.location || '待定' }}</el-descriptions-item>
        <el-descriptions-item label="名额">{{ sdData.confirmed }}/{{ sdData.quota }}
          <span class="dim">（同场次岗位互斥）</span></el-descriptions-item>
        <el-descriptions-item label="条件要求">{{ sdData.requirement_desc }}</el-descriptions-item>
        <el-descriptions-item label="要求说明">{{ sdData.requirement_note || '—' }}</el-descriptions-item>
        <el-descriptions-item label="培训内容">{{ sdData.training_note || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 发送通知 -->
    <el-dialog v-model="notifyDlg.visible" title="发送通知" width="540">
      <el-form label-width="90px">
        <el-form-item label="发送范围" required>
          <el-radio-group v-model="notifyForm.scope">
            <el-radio-button value="all">全部志愿者</el-radio-button>
            <el-radio-button value="group">指定场次</el-radio-button>
            <el-radio-button value="not_checked_in">指定场次未到岗</el-radio-button>
          </el-radio-group>
          <div class="dim" style="margin-top:4px">
            收件人为已确认报名（status=confirmed）的志愿者；「未到岗」将排除该场次已签到的人。
          </div>
        </el-form-item>
        <el-form-item v-if="notifyForm.scope !== 'all'" label="选择场次" required>
          <el-select v-model="notifyForm.shift_id" style="width:100%" placeholder="请选择场次">
            <el-option v-for="g in shifts" :key="g.id" :value="g.id"
                       :label="`${g.name}（${g.start_time.slice(5,16).replace('T',' ')} ~ ${g.end_time.slice(11,16)}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="通知标题" required>
          <el-input v-model="notifyForm.title" maxlength="128" show-word-limit
                    placeholder="如：明日签到时间提醒" />
        </el-form-item>
        <el-form-item label="通知内容" required>
          <el-input v-model="notifyForm.content" type="textarea" :rows="4"
                    placeholder="请输入通知正文，将通过企业微信推送给志愿者" />
        </el-form-item>
        <el-form-item label="">
          <el-checkbox v-model="notifyForm.need_ack">要求志愿者签收</el-checkbox>
          <div class="dim">
            勾选后，志愿者的通知会显红并附「我已知悉」按钮；您可以在下方
            「签收情况」里看到谁还没签，并一键催办。<br>
            改集合地点、临时取消这类必须确认到人的通知才勾，别每条都勾——
            都要签收就等于都不要签收。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="notifyDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="notifyDlg.saving" @click="sendNotify">发送</el-button>
      </template>
    </el-dialog>

    <!-- 基本信息编辑 -->
    <el-dialog v-model="editBase" title="编辑活动基本信息" width="560">
      <el-form label-width="110px">
        <el-form-item label="名称"><el-input v-model="baseForm.title" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="baseForm.type">
            <el-radio-button v-for="(t, k) in types" :key="k" :value="k">{{ k }}类·{{ t.name }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="地点"><el-input v-model="baseForm.location" /></el-form-item>
        <el-form-item label="公示联系方式" required>
          <div class="contacts-box">
            <div v-for="(c, i) in baseForm.contacts" :key="i" class="contact-row">
              <el-input v-model="c.name" maxlength="32" placeholder="姓名" style="width:150px" />
              <el-input v-model="c.phone" maxlength="64" placeholder="电话/微信" style="flex:1" />
              <el-button type="danger" plain :disabled="baseForm.contacts.length <= 1"
                         @click="baseForm.contacts.splice(i, 1)">删除</el-button>
            </div>
            <el-button plain size="small"
                       @click="baseForm.contacts.push({ name: '', phone: '' })">
              <el-icon><Plus /></el-icon>&nbsp;添加联系人
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="简介"><el-input v-model="baseForm.intro" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="答疑群二维码">
          <div class="contacts-box">
            <el-upload :show-file-list="false" :http-request="uploadBaseQr" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp">
              <el-button type="primary" plain :loading="qrUploading">
                <el-icon><Upload /></el-icon>&nbsp;上传二维码图片
              </el-button>
            </el-upload>
            <div class="dim" style="margin-top:4px">支持 png/jpg/jpeg/webp，不超过 2MB</div>
            <div v-if="baseForm.qa_group_qr" class="qr-preview">
              <img :src="baseForm.qa_group_qr" alt="答疑群二维码" />
              <el-button link type="danger" size="small" @click="baseForm.qa_group_qr = ''">移除图片</el-button>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="报名窗口">
          <el-date-picker v-model="baseRange" type="datetimerange" style="width:100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editBase = false">取消</el-button>
        <el-button type="primary" :loading="savingBase" @click="saveBase">保存并通知变更</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, download as dl, fmtDT } from '../api'
import AssignPanel from '../components/AssignPanel.vue'
import CheckinPanel from '../components/CheckinPanel.vue'
import MaterialManager from '../components/MaterialManager.vue'
import { auth } from '../store'

const route = useRoute()
const act = ref(null)
const tab = ref(String(route.query.tab || 'tree'))   // 支持 ?tab=roster 直达页签
const types = ref({})
const colleges = ref([])
const loading = ref(false)
const notFound = ref(false)
const loadError = ref('')
const savingGroup = ref(false)
const savingStation = ref(false)
const savingBase = ref(false)
const managers = ref([])
const newMgrSid = ref('')
const moveSel = ref({})
const qrUploading = ref(false)

// —— 发送通知 ——
const notifyDlg = ref({ visible: false, saving: false })
const notifyForm = ref({ scope: 'all', shift_id: '', title: '', content: '',
                         need_ack: false })
const ackBatches = ref([])

// —— 签收情况 ——
async function loadBatches() {
  ackBatches.value = await api.get(
    `/api/staff/activities/${activityId}/notify-batches`)
}

async function remindBatch(b) {
  const d = await api.post(`/api/staff/notify-batches/${b.batch_id}/remind`)
  ElMessage.success(`已提醒 ${d.count} 位未签收志愿者`)
  loadBatches()
}

// —— 岗位树 ——
const shiftDlg = ref({ visible: false, editing: null })
const groupForm = ref({})
const positionDlg = ref({ visible: false, editing: null })
const lockBusy = ref(false)
const lockHours = ref(2)

// —— 编辑冻结 ——
async function startEditing() {
  const d = await api.post(`/api/staff/activities/${activityId}/edit-lock`)
  lockHours.value = d.auto_release_hours
  ElMessage.warning('已暂停报名，改完请点「保存并发布」')
  loadAll()
}

// 草稿 → 首次发布；编辑态 → 发布这批改动。对管理者都是「改完了，让学生看到」
const canPublish = computed(
  () => !!act.value && (act.value.status === 'draft' || act.value.editing))

const publishConfirm = computed(() => act.value?.status === 'draft'
  ? '发布后学生即可报名，确认？'
  : '恢复报名，并把这段时间的改动通知已报名的志愿者？')

async function saveAndPublish() {
  lockBusy.value = true
  try {
    if (act.value.status === 'draft') {
      await api.post(`/api/staff/activities/${activityId}/publish`)
      ElMessage.success('已发布，学生现在可以报名了')
    } else {
      const d = await api.post(`/api/staff/activities/${activityId}/publish-changes`)
      ElMessage.success(d.changes
        ? `已恢复报名，${d.changes} 项改动已通知已报名的志愿者`
        : '已恢复报名（本次没有实质改动）')
    }
    loadAll()
  } finally { lockBusy.value = false }
}
const copyPosDlg = ref({ visible: false, source: null, positionIds: [],
                         targetIds: [], busy: false })
const stationForm = ref({})
const editBase = ref(false)
const baseForm = ref({})
const baseRange = ref([])
const gdVisible = ref(false)
const gdData = ref(null)
const sdVisible = ref(false)
const sdData = ref(null)
const sdGroupName = ref('')

function showGroupDetail(g) { gdData.value = g; gdVisible.value = true }
function showStationDetail(g, s) {
  sdData.value = s
  sdGroupName.value = `${g.name}（${g.time_text}）`
  sdVisible.value = true
}

// —— 名单/调配 ——
const roster = ref({ confirmed: [], waitlist: [], cancelled: [] })
const currentGroupId = ref('')
const hours = ref([])
const hoursLoading = ref(false)
const hoursSel = ref([])                       // 时长表勾选行
const manualHours = ref([])                    // 手动添加的时长记录
const mh = ref({ userIds: [], hours: 0.5, note: '' })

// 手动加时长的候选人 = 本活动全部场次的已确认志愿者（含未签到的）
// pick 模式却一个岗位都没有 = 学生根本报不了名
// 报名已经结束的活动，再说「暂停报名」只会让人困惑
const editLabel = computed(() =>
  act.value?.status === 'published' ? '调整安排（暂停报名）' : '调整安排')

// 已发布的活动，不进编辑态就不能动结构
const structureLocked = computed(
  () => !!act.value && act.value.status !== 'draft' && !act.value.editing)

function needsPositions(g) {
  return g.mode === 'pick' && !g.positions.length
}
const shiftsNeedingPositions = computed(() => shifts.value.filter(needsPositions))

const rosterOptions = computed(() => {
  const seen = new Set()
  const out = []
  for (const g of shifts.value) {
    for (const row of g._roster?.confirmed || []) {
      if (seen.has(row.user.student_id)) continue
      seen.add(row.user.student_id)
      out.push({ name: row.user.name, student_id: row.user.student_id,
                 shift_name: `· ${g.name}` })
    }
  }
  return out
})
const logs = ref([])
const ACTION_LABELS = {
  create: '创建', update: '修改', delete: '删除', publish: '发布', close: '截止',
  finish: '结束', assign: '分配', assign_manual: '手动调岗', force_assign: '强制分配',
  export: '导出', hours_confirm: '确认时长', hours_upload: '回传时长',
}
const actionLabel = a => ACTION_LABELS[a] || a
const cancelType = x => ({ normal: '退选', late: '临近扣分', expired: '确认超时',
                           abandoned: '放弃候补', group_deleted: '场次取消' }[x.cancel_type] || '取消')

let activityId = ''

async function loadAll() {
  loading.value = true
  try {
    const list = await api.get('/api/staff/activities')
    const brief = list.find(a => a.id === activityId)
    if (!brief) {
      notFound.value = true
      return
    }
    activityId = brief.id
    const d = await api.get(`/api/activities/${activityId}`)
    act.value = d
    const rosters = await Promise.all(d.shifts.map(g => api.get(`/api/staff/shifts/${g.id}/roster`)))
    d.shifts.forEach((g, i) => {
      g._roster = rosters[i]
    })
    const logsResp = await api.get(`/api/staff/audit-logs?activity_id=${activityId}&size=50`)
    logs.value = logsResp.items || []
    managers.value = await api.get(`/api/staff/activities/${activityId}/managers`)
    if (!currentGroupId.value && shifts.value.length) {
      currentGroupId.value = shifts.value[0].id
    }
    loadRoster()   // 无条件加载：act 赋值时 watcher 可能已抢先跑过一次空加载
  } catch (e) {
    loadError.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const shifts = computed(() => act.value?.shifts || [])
const currentShift = computed(() => shifts.value.find(g => g.id === currentGroupId.value))
const unassignedCount = computed(() =>
  (roster.value.confirmed || []).filter(x => !x.position_id).length)
const pendingPromo = computed(() => roster.value.waitlist.some(w => w.status === 'pending_confirm'))
const confirmedCount = computed(() => hours.value.filter(h => h.status === 'confirmed').length)

function sumQuota(g) {
  return g.positions.reduce((s, x) => s + x.quota, 0)
}

// —— 场次 CRUD ——
function openShift(g) {
  shiftDlg.value.editing = g || null
  groupForm.value = g ? {
    name: g.name,
    start_time: new Date(g.start_time.replace(' ', 'T')),
    end_time: new Date(g.end_time.replace(' ', 'T')),
    capacity: g.capacity, mode: g.mode || 'pick', note: g.note,
    waitlist_quota: g.waitlist_quota ?? 3,
  } : { name: '', start_time: '', end_time: '', capacity: 10, mode: 'pick', note: '',
        waitlist_quota: 3 }
  shiftDlg.value.visible = true
}
async function saveShift() {
  const f = groupForm.value
  if (!f.name?.trim()) return ElMessage.warning('请填写场次名称')
  if (!f.start_time || !f.end_time) return ElMessage.warning('请选择起止时间')
  savingGroup.value = true
  try {
    const body = {
      name: f.name, capacity: f.capacity, mode: f.mode, note: f.note,
      waitlist_quota: f.waitlist_quota ?? 3,
      start_time: toISO(f.start_time), end_time: toISO(f.end_time),
    }
    if (shiftDlg.value.editing) await api.patch(`/api/staff/shifts/${shiftDlg.value.editing.id}`, body)
    else await api.post(`/api/staff/activities/${activityId}/shifts`, body)
    ElMessage.success('已保存（操作留痕）')
    shiftDlg.value.visible = false
    loadAll()
  } catch { /* 错误提示由 api 层统一弹出 */ } finally {
    savingGroup.value = false
  }
}
async function delShift(g) {
  try {
    await api.del(`/api/staff/shifts/${g.id}`)
  } catch (e) {
    await ElMessageBox.confirm(e.message + '，是否强制删除？', '确认', { type: 'warning' })
    await api.del(`/api/staff/shifts/${g.id}?force=1`)
  }
  ElMessage.success('已删除')
  loadAll()
}

// —— 岗位批量复制 ——
function openCopyPositions(g) {
  if (!g.positions.length) return ElMessage.warning('该场次下还没有岗位')
  copyPosDlg.value = { visible: true, source: g, positionIds: [],
                       targetIds: [], busy: false }
}

async function doCopyPositions() {
  const d = copyPosDlg.value
  if (!d.targetIds.length) return ElMessage.warning('请选择至少一个目标场次')
  d.busy = true
  try {
    const r = await api.post(`/api/staff/shifts/${d.source.id}/positions/copy`,
      { position_ids: d.positionIds, target_shift_ids: d.targetIds })
    ElMessage.success(`已复制 ${r.copied} 个岗位`
      + (r.skipped.length ? `，同名跳过 ${r.skipped.length} 个` : ''))
    d.visible = false
    loadAll()
  } finally { d.busy = false }
}


// —— 岗位 CRUD ——
function openStation(st, g) {
  positionDlg.value = { visible: true, editing: st, groupId: g.id }
  stationForm.value = st ? {
    name: st.name, location: st.location, quota: st.quota,
    waitlist_quota: st.waitlist_quota, needs_leader: st.needs_leader,
    genderReq: (st.requirement_tags?.性别) || '',
    collegeReq: st.requirement_tags?.学院 || [],
    requirement_note: st.requirement_note, training_note: st.training_note,
  } : {
    name: '', location: '', quota: 2, waitlist_quota: 3, needs_leader: false,
    genderReq: '', collegeReq: [],
    requirement_note: '', training_note: '',
  }
}
async function saveStation() {
  const f = stationForm.value
  if (!f.name?.trim()) return ElMessage.warning('请填写岗位名称')
  savingStation.value = true
  try {
    const tags = {}
    if (f.genderReq) tags['性别'] = f.genderReq
    if (f.collegeReq?.length) tags['学院'] = f.collegeReq
    const body = {
      name: f.name, quota: f.quota, location: f.location,
      waitlist_quota: f.waitlist_quota, needs_leader: f.needs_leader,
      requirement_tags: tags, requirement_note: f.requirement_note,
      training_note: f.training_note,
    }
    if (positionDlg.value.editing) await api.patch(`/api/staff/positions/${positionDlg.value.editing.id}`, body)
    else await api.post(`/api/staff/shifts/${positionDlg.value.groupId}/positions`, body)
    ElMessage.success('已保存')
    positionDlg.value.visible = false
    loadAll()
  } catch { /* toast by api */ } finally {
    savingStation.value = false
  }
}
async function delStation(st) {
  await api.del(`/api/staff/positions/${st.id}`)
  ElMessage.success('已删除')
  loadAll()
}

// —— 基本信息 ——
onMounted(async () => {
  activityId = route.params.id
  const c = await api.get('/api/auth/constants')
  types.value = c.activity_types
  colleges.value = c.colleges
  loadAll()
})

watch(editBase, (v) => {
  if (v && act.value) {
    const contacts = Array.isArray(act.value.contacts) && act.value.contacts.length
      ? act.value.contacts.map(c => ({ name: c.name || '', phone: c.phone || '' }))
      : [{ name: '', phone: '' }]
    baseForm.value = {
      title: act.value.title, type: act.value.type, location: act.value.location,
      contacts, intro: act.value.intro,
      qa_group_qr: act.value.qa_group_qr,
    }
    baseRange.value = [
      act.value.reg_start ? new Date(act.value.reg_start.replace(' ', 'T')) : '',
      act.value.reg_end ? new Date(act.value.reg_end.replace(' ', 'T')) : '',
    ]
  }
})

// 自定义上传：POST /api/staff/upload-image（FormData），成功后取 url 预览
async function uploadBaseQr({ file }) {
  const fd = new FormData()
  fd.append('file', file)
  qrUploading.value = true
  try {
    const headers = {}
    if (auth.token) headers.Authorization = `Bearer ${auth.token}`
    const resp = await fetch('/api/staff/upload-image', { method: 'POST', headers, body: fd })
    const d = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      throw new Error(typeof d.detail === 'string' ? d.detail : `上传失败(${resp.status})`)
    }
    baseForm.value.qa_group_qr = d.url
    ElMessage.success('二维码已上传')
  } catch (e) {
    ElMessage.error(e.message || '上传失败')
  } finally {
    qrUploading.value = false
  }
}

function contactError(contacts) {
  if (!contacts?.length) return '请至少添加一位公示联系人'
  for (const c of contacts) {
    if (!c.name?.trim()) return '请填写每位联系人的姓名'
    if (!c.phone?.trim()) return '请填写每位联系人的电话/微信'
  }
  return ''
}

async function saveBase() {
  const ce = contactError(baseForm.value.contacts)
  if (ce) return ElMessage.warning(ce)
  const body = { ...baseForm.value }
  body.contacts = baseForm.value.contacts.map(c => ({ name: c.name.trim(), phone: c.phone.trim() }))
  if (baseRange.value?.length === 2) {
    body.reg_start = toISO(baseRange.value[0])
    body.reg_end = toISO(baseRange.value[1])
  }
  savingBase.value = true
  try {
    await api.patch(`/api/staff/activities/${activityId}`, body)
    ElMessage.success('已保存，变更将推送提醒志愿者')
    editBase.value = false
    loadAll()
  } catch { /* toast by api */ } finally {
    savingBase.value = false
  }
}

// —— 发送通知 ——
function openNotify() {
  notifyForm.value = { scope: 'all', shift_id: currentGroupId.value || '',
                       title: '', content: '', need_ack: false }
  notifyDlg.value.visible = true
}

async function sendNotify() {
  const f = notifyForm.value
  if (!f.title.trim()) return ElMessage.warning('请填写通知标题')
  if (!f.content.trim()) return ElMessage.warning('请填写通知内容')
  if (f.scope !== 'all' && !f.shift_id) return ElMessage.warning('请选择场次')
  notifyDlg.value.saving = true
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/notify`, {
      scope: f.scope,
      shift_id: f.scope === 'all' ? '' : f.shift_id,
      title: f.title.trim(),
      content: f.content.trim(),
      need_ack: f.need_ack,
    })
    ElMessage.success(`已发送给 ${d.count} 人`
      + (f.need_ack ? '，可在「签收情况」查看进度' : ''))
    notifyDlg.value.visible = false
    loadBatches()
  } catch { /* toast by api */ } finally {
    notifyDlg.value.saving = false
  }
}

// —— 状态流转 ——
async function transition(action) {
  await api.post(`/api/staff/activities/${activityId}/${action}`)
  ElMessage.success('操作成功')
  loadAll()
}

// —— 导出与时长 ——
function doExport() { dl(`/api/staff/activities/${activityId}/export.xlsx`) }

// 加载时长预览（含手动添加的记录）；silent=true 时不弹「暂无签到数据」提示
async function hoursPreview(silent = false) {
  hoursLoading.value = true
  try {
    const d = await api.get(`/api/staff/activities/${activityId}/hours`)
    // editHours：可编辑时长，加载时初始化为后端计算值，勾选确认时按此值提交
    hours.value = (d.items || []).map(it => ({ ...it, editHours: Number(it.hours) || 0 }))
    manualHours.value = (d.manual || []).map(m => ({
      ...m,
      record_id: m.record_id || m.id,
      name: m.name || m.user?.name || '',
      student_id: m.student_id || m.user?.student_id || '',
    }))
    hoursSel.value = []
    if (!hours.value.length && !silent) ElMessage.info('暂无签到数据')
  } finally { hoursLoading.value = false }
}

function onHoursSelect(rows) { hoursSel.value = rows }

// 确认选中志愿者的时长（可先在表格里修改数值）
async function confirmSelectedHours() {
  if (!hoursSel.value.length) return ElMessage.warning('请先勾选志愿者')
  const items = hoursSel.value.map(r => ({
    student_id: r.user.student_id, hours: Number(r.editHours),
  }))
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/hours/confirm`, { items })
    ElMessage.success(`已确认 ${d.count} 名志愿者的时长`)
    hoursPreview(true)
  } catch { /* toast by api */ }
}

// 确认全部志愿者时长
async function confirmAllHours() {
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/hours/confirm`, {})
    ElMessage.success(d.message || `已确认 ${d.count} 名志愿者的时长`)
    hoursPreview(true)
  } catch { /* toast by api */ }
}

// 手动添加时长（不依赖签到，如场外支援/补录）。批量：一次给多人写同一条时长
async function addManualHours() {
  const { userIds, hours, note } = mh.value
  if (!userIds.length) return ElMessage.warning('请先选择志愿者')
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/hours/manual`,
      { items: userIds.map(sid => ({ student_id: sid, hours, note })) })
    ElMessage.success(`已为 ${d.count} 位志愿者添加时长`)
    mh.value = { userIds: [], hours: 0.5, note: '' }
    hoursPreview(true)
  } catch { /* toast by api */ }
}

// 删除手动时长（已回传的不允许删除）
async function delManualHours(row) {
  if (row.status === 'uploaded') return ElMessage.warning('已回传数智人大的记录不可删除')
  await api.del(`/api/staff/activities/${activityId}/hours/manual/${row.record_id}`)
  ElMessage.success('已删除')
  hoursPreview(true)
}
async function uploadHours() {
  const d = await api.post(`/api/staff/activities/${activityId}/hours/upload`)
  ElMessage.success(`已回传 ${d.count} 人，回执号 ${d.receipt_no}`)
  hoursPreview(true)
}

function loadRoster() {
  const g = currentShift.value
  if (!g?._roster) return
  roster.value = g._roster
  moveSel.value = {}
}

// 只有「岗位设了组长」的人才能被指定；没岗位的人后端也会拒
function canBeLeader(row) {
  const p = roster.value?.positions?.find(x => x.id === row.position_id)
  return !!p?.needs_leader
}

async function setLeader(row, on) {
  const d = await api.post(`/api/staff/enrollments/${row.enrollment_id}/leader`,
                           { is_leader: on })
  ElMessage.success(on ? `已指定 ${row.user.name} 为组长`
                       : `已取消 ${row.user.name} 的组长身份，由下一位有意愿者接任`)
  loadAll()
  return d
}

async function applyMove(row) {
  const target = moveSel.value[row.enrollment_id]
  if (target === undefined) return
  const send = (force) => api.post(
    `/api/staff/shifts/${currentGroupId.value}/assign/apply`,
    { plan: [{ enrollment_id: row.enrollment_id, position_id: target || '', force }],
      source: 'manual' })
  try {
    const d = await send(false)
    ElMessage.success(d.summary || '已调岗')
    loadAll()
  } catch (e) {
    if ((e.message || '').includes('名额')) {
      try {
        await ElMessageBox.confirm(e.message + '。是否强制调岗（计入留痕）？',
                                   '名额不足', { type: 'warning' })
        const d = await send(true)
        ElMessage.success(d.summary || '已强制调岗')
        loadAll()
      } catch { /* 取消 */ }
    }
  }
}

async function addManager() {
  try {
    await api.post(`/api/staff/activities/${activityId}/managers`,
                   { student_id: newMgrSid.value })
    ElMessage.success('已添加')
    newMgrSid.value = ''
    managers.value = await api.get(`/api/staff/activities/${activityId}/managers`)
  } catch { /* toast by api */ }
}
async function removeManager(row) {
  try {
    await api.del(`/api/staff/activities/${activityId}/managers/${row.id}`)
    ElMessage.success('已移除')
    managers.value = await api.get(`/api/staff/activities/${activityId}/managers`)
  } catch { /* toast by api */ }
}
// 进入「导出与时长」页签时自动加载时长预览（静默加载，不弹提示）
watch(tab, t => {
  if (t === 'export') hoursPreview(true)
  if (t === 'acks') loadBatches()
})
watch(currentGroupId, loadRoster)
watch(() => shifts.value.map(g => g.confirmed).join(','), () => {
  if (!currentGroupId.value && shifts.value.length) currentGroupId.value = shifts.value[0].id
  loadRoster()
})

function toISO(v) {
  if (!v) return null
  const d = v instanceof Date ? v : new Date(v)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
</script>

<style scoped>
.needs-positions { border-color: var(--el-color-danger) !important; }
.group-card { border:1px solid #eceef1; border-radius:12px; padding:12px 14px; margin-bottom:12px; background:#fff; }
.g-head { display:flex; gap:12px; align-items:center; margin-bottom:6px; font-size:13.5px; flex-wrap:wrap;}
.toolbar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.wl-card { margin-bottom:12px; border-radius:12px; }
.wl-row { display:flex; gap:6px; align-items:center; padding:3px 0; font-size:13px; }
button.dashed { border-style:dashed; }
.contacts-box { width: 100%; }
.contact-row { display: flex; gap: 8px; margin-bottom: 6px; align-items: center; }
.qr-preview { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.qr-preview img { max-width: 160px; max-height: 160px; border-radius: 8px; border: 1px solid #e4e7ed; }
</style>
