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
    <!-- 志愿者档案与违规登记：都从名单里点进来，限本活动的人 -->
    <ArchiveDrawer ref="archiveDrawer" />
    <ViolationDialog ref="violationDlg" :activity-id="activityId" @saved="loadAll" />

    <el-page-header @back="$router.push('/manage')" title="返回列表">
      <!-- 窄屏下标题与副信息各占一行：一行挤不下活动名+状态+时间地点 -->
      <template #content>
        <div class="hd-title">
          <b>{{ act.title }}</b>
          <el-tag size="small" :type="{published:'success',closed:'info',draft:'info',pending:'warning',approved:'primary',finished:'info',completed:'success'}[act.status]">
            {{ act.status_label }}
          </el-tag>
          <el-tag v-if="act.urgent" size="small" type="danger" effect="dark">急招</el-tag>
        </div>
        <div class="hd-meta dim">{{ act.time_text }} · {{ act.location }}</div>
      </template>
      <template #extra>
        <!-- 「调整岗位」只在岗位设置那一页出现一次，页头不重复放 -->
        <el-button @click="openPoster">活动招募</el-button>
        <!-- 急招置顶：全校层面的调度，只给会长团/管理员 -->
        <el-button v-if="canSee.pin() && act.status === 'published'"
                   :type="act.pinned ? 'danger' : 'default'" :plain="act.pinned"
                   :loading="pinBusy" @click="togglePin">
          {{ act.pinned ? '取消急招置顶' : '置顶为急招' }}
        </el-button>
        <el-button v-if="!isOver" @click="editBase = true">编辑基本信息</el-button>
        <el-popconfirm v-if="act.status === 'published'" title="将截止报名，确认截止？"
                       @confirm="transition('close')">
          <template #reference><el-button type="warning">截止报名</el-button></template>
        </el-popconfirm>
        <el-button v-else-if="act.status === 'closed'" type="warning" plain
                   @click="openReopen">恢复报名</el-button>
        <!-- 只在「已截止」时出现：报名还开着就宣布结束，等于让正在报名的人
             报进一个已经收摊的活动 -->
        <el-popconfirm v-if="act.status === 'closed'"
                       title="结束活动后不能再修改活动信息，确认结束？"
                       @confirm="transition('finish')">
          <template #reference><el-button type="danger">结束活动</el-button></template>
        </el-popconfirm>
        <el-button v-if="act.status === 'draft'" type="primary" @click="openSubmit">
          提交审批
        </el-button>
        <el-tag v-else-if="act.status === 'pending'" type="warning" size="large" effect="dark">
          待 {{ approval?.department || '青协' }} 审批
        </el-tag>
        <!-- 草稿是首次发布，编辑态是发布改动，对管理者是同一件事：改完了，让学生看到 -->
        <el-popconfirm v-if="canPublish" :title="publishConfirm"
                       @confirm="saveAndPublish">
          <template #reference>
            <el-button type="success" :loading="lockBusy">保存并发布</el-button>
          </template>
        </el-popconfirm>
      </template>
    </el-page-header>

    <!-- 审批状态：待审批看到审批人，被驳回看到意见 -->
    <el-alert v-if="act.status === 'pending'" type="warning" show-icon :closable="false"
              style="margin:12px 0"
              :title="`已提交${approval?.department || ''}审批，等待 ${approval?.assignee || ''} 处理`"
              :description="act.auto_publish
                ? '审批期间可以继续调整安排，审批通过后活动立即公开发布，到点准时开始报名。'
                : '审批期间可以继续调整安排，审批通过后由你点「发布」。'" />
    <el-alert v-else-if="act.status === 'approved'" type="success" show-icon :closable="false"
              style="margin:12px 0" title="审批已通过，可以发布了"
              :description="approval?.comment ? ('审批意见：' + approval.comment) : ''" />
    <el-alert v-else-if="act.status === 'draft' && approval?.comment" type="error" show-icon
              :closable="false" style="margin:12px 0" title="审批未通过"
              :description="'意见：' + approval.comment + '；修改后可重新提交。'" />

    <el-alert v-if="act.editing" type="warning" :closable="false" show-icon
              style="margin:12px 0"
              title="正在调整岗位，学生已暂停报名与退选"
              :description="`改完点右上角「保存并发布」恢复报名并通知志愿者；${lockHours} 小时未保存会自动退出编辑。`" />

    <el-tabs v-model="tab" style="margin-top:8px">
      <!-- ============ 岗位设置 ============ -->
      <el-tab-pane name="tree">
        <template #label>岗位设置
          <el-badge v-if="treeAlerts.length" value="!" type="danger" />
        </template>
        <el-alert v-if="act.recruit_mode === 'screened'" type="info" :closable="false"
                  style="margin-bottom:12px">
          报名后筛选：志愿者先勾意向岗位、再按岗位填报名问卷，报名截止后你在
          「志愿者管理」里录取并定岗位。没建岗位的场次，志愿者勾的是「待分配」，
          录取之后再排。
        </el-alert>
        <el-alert v-else type="info" :closable="false" style="margin-bottom:12px">
          先按照时间设置场次，再分配每场的具体岗位。
          <b>报名即选岗位</b>：发布前分配好各岗位名额。
          <b>截止后统一分配</b>：适用于未确定岗位的场次，志愿者先选择时间，后续分配岗位。
        </el-alert>
        <div class="toolbar" style="margin-bottom:12px">
          <el-button plain @click="formDlg = true">{{ formLabel }}</el-button>
          <span class="dim">
            {{ act.recruit_mode === 'screened'
               ? '志愿者申请时填写' : '选填，志愿者报名时填写' }}
          </span>
        </div>
        <!-- 结束之后是终态：时长按当时的岗位与名单结的账，再改就对不上，
             连「调整岗位」的入口都不给 -->
        <el-alert v-if="isOver" type="info" show-icon :closable="false"
                  style="margin-bottom:12px"
                  :title="`活动${act.status_label}，安排与信息均为只读`">
          <div style="line-height:1.8">
            时长已由活动管理者核准，不再接受修改，如确需更正请联系会长团。
          </div>
        </el-alert>
        <el-alert v-else-if="structureLocked" type="info" show-icon :closable="false"
                  style="margin-bottom:12px">
          <template #title>
            当前只读——改场次与岗位要先点击右侧按钮进入调整状态，期间将暂停报名
            <el-button type="warning" size="small" style="margin-left:10px"
                       @click="startEditing">{{ editLabel }}</el-button>
          </template>
        </el-alert>

        <el-alert v-if="treeAlerts.length" type="error" show-icon
                  :closable="false" style="margin-bottom:12px"
                  :title="`有 ${treeAlerts.length} 个场次还没排好`">
          <div v-for="a in treeAlerts" :key="a.id" style="line-height:1.9">
            <b>{{ a.name }}</b>：{{ a.text }}
          </div>
        </el-alert>

        <div v-for="g in shifts" :key="g.id" class="group-card"
             :class="{ 'needs-positions': !g.positions.length }">
          <div class="g-head">
            <el-link type="primary" underline="never" style="font-weight:600;font-size:14px"
                     @click="showGroupDetail(g)">{{ g.name }}</el-link>
            <el-tag size="small" effect="plain"
                    :type="g.mode === 'assign' ? 'warning' : 'info'">
              {{ g.mode_label }}
            </el-tag>
            <span class="dim">{{ g.start_time.slice(5,16).replace('T',' ') }}
              ~ {{ g.end_time.slice(5,16).replace('T',' ') }}</span>
            <span>{{ g.mode === 'assign' ? `上限 ${g.capacity}（待分配）` : `本场合计 ${g.capacity} 人` }}</span>
            <el-tag v-if="!g.positions.length"
                    size="small" :type="g.mode === 'assign' ? 'info' : 'danger'"
                    :effect="g.mode === 'assign' ? 'plain' : 'dark'">
              {{ g.mode === 'assign' ? '岗位后续再创建' : '未设岗位' }}
            </el-tag>
            <!-- 待分配和未设岗位是两件事，别互相遮住：
                 assign 模式截止前人人待分配是常态，截止后还剩人就是漏排 -->
            <el-tooltip v-if="g.unassigned_count" placement="top"
                        :content="unassignedHint(g)">
              <el-tag size="small" :type="regClosed ? 'danger' : 'warning'"
                      :effect="regClosed || g.mode === 'pick' ? 'dark' : 'plain'">
                {{ g.unassigned_count }} 人待分配
              </el-tag>
            </el-tooltip>
            <span style="margin-left:auto">
              <el-button size="small" :disabled="structureLocked"
                         @click="openShift(g)">编辑</el-button>
              <el-button size="small" plain :disabled="structureLocked"
                         @click="openCopyPositions(g)">复制岗位到其它场次</el-button>
              <el-button size="small" type="primary" plain :disabled="structureLocked"
                         @click="addPosition(g)">添加岗位</el-button>
              <el-popconfirm :title="`删除场次「${g.name}」？`" @confirm="delShift(g)"
                             :disabled="structureLocked">
                <template #reference>
                  <el-button size="small" type="danger" plain
                             :disabled="structureLocked">删除</el-button>
                </template>
              </el-popconfirm>
            </span>
          </div>

          <DataCards v-if="narrow && g.positions.length" :rows="g.positions"
                     title-key="name"
                     :fields="[{ key: 'group_name', label: '岗位组', hideEmpty: true },
                               { key: 'location', label: '地点', format: v => v || act.location },
                               { key: 'requirement_desc', label: '要求' },
                               { key: 'attire_text', label: '服装', hideEmpty: true },
                               { key: 'training_note', label: '培训' }]">
            <template #extra="{ row }">
              <el-tag size="small" :type="row.remaining > 0 ? 'success' : 'info'">
                {{ row.enrolled }}/{{ row.quota }}
              </el-tag>
              <el-tag v-if="row.needs_leader" size="small" type="warning" effect="plain">组长</el-tag>
            </template>
            <template #actions="{ row }">
              <el-button size="small" type="primary" plain :disabled="structureLocked"
                         @click="openStation(row, g)">编辑</el-button>
              <el-button size="small" plain :disabled="structureLocked"
                         @click="copyStation(row, g)">复制</el-button>
              <el-popconfirm title="删除该岗位？" @confirm="delStation(row)"
                             :disabled="structureLocked">
                <template #reference>
                  <el-button size="small" type="danger" plain
                             :disabled="structureLocked">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </DataCards>
          <el-table v-else-if="g.positions.length" :data="g.positions" size="small">
            <el-table-column label="岗位组" width="120">
              <template #default="{row}">
                <el-tag v-if="row.group_name" size="small" effect="plain" type="warning">
                  {{ row.group_name }}
                </el-tag>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="岗位" min-width="130">
              <template #default="{row}">
                <el-link type="primary" underline="never"
                         @click="showStationDetail(g, row)">{{ row.name }}</el-link>
                <el-tag v-if="row.is_placeholder" size="small" type="warning" effect="plain"
                        style="margin-left:6px">默认</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="服务地点" min-width="120">
              <template #default="{row}">{{ row.location || act.location }}</template>
            </el-table-column>
            <el-table-column label="已报名/名额" width="130">
              <template #default="{row}">{{ row.enrolled }} / {{ row.quota }}</template>
            </el-table-column>
            <el-table-column prop="requirement_desc" label="要求" min-width="140" />
            <el-table-column label="服装" width="90">
              <template #default="{row}">
                <span v-if="row.attire === 'none'" class="dim">—</span>
                <span v-else>{{ row.attire_label }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="training_note" label="培训内容" min-width="160" show-overflow-tooltip />
            <el-table-column label="操作" width="170">
              <template #default="{row}">
                <el-button size="small" link type="primary" :disabled="structureLocked"
                           @click="openStation(row, g)">编辑</el-button>
                <el-button size="small" link :disabled="structureLocked"
                           @click="copyStation(row, g)">复制</el-button>
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
               ? '截止后统一分配：学生报名只选时间，确定分工后将本场次改为「报名即选岗位」，建好岗位再一键分配。'
               : '还没有岗位，请先设置岗位及名额。' }}
          </p>

          <!-- 培训/彩排：本场次的附加时段，报名时连带、签到与时长照常 -->
          <div class="attached">
            <div v-for="s in g.attached" :key="s.id" class="att-row">
              <el-tag size="small" effect="dark"
                      :type="s.kind === 'training' ? 'success' : 'warning'">
                {{ s.kind_label }}
              </el-tag>
              <b>{{ s.name }}</b>
              <span class="dim">{{ s.time_text }} · {{ s.location || act.location || '地点待定' }}</span>
              <span class="dim">{{ s.hours_label }}</span>
              <el-tag size="small" effect="plain">
                {{ s.applies_to_positions.length
                   ? '适用：' + positionNames(g, s.applies_to_positions)
                   : '全场次参加' }}
              </el-tag>
              <span class="dim">已带上 {{ s.enrolled }} 人</span>
              <span v-if="s.note" class="dim">备注：{{ s.note }}</span>
              <span style="margin-left:auto">
                <el-button size="small" link type="primary" :disabled="structureLocked"
                           @click="openAttached(g, s)">编辑</el-button>
                <el-popconfirm :title="`删除「${s.name}」？相关签到记录会同时清除`"
                               @confirm="delShift(s)" :disabled="structureLocked">
                  <template #reference>
                    <el-button size="small" link type="danger"
                               :disabled="structureLocked">删除</el-button>
                  </template>
                </el-popconfirm>
              </span>
            </div>
            <el-button size="small" text type="primary" :disabled="structureLocked"
                       @click="openAttached(g, null)">
              <el-icon><Plus /></el-icon>&nbsp;添加培训 / 彩排
            </el-button>
          </div>
        </div>
        <el-button style="width:100%;margin-top:4px" plain dashed
                   :disabled="structureLocked" @click="openShift(null)">
          <el-icon><Plus /></el-icon>&nbsp;新建场次
        </el-button>
      </el-tab-pane>

      <!-- ============ 报名与调配 ============ -->
      <el-tab-pane v-if="isPublic" name="roster">
        <template #label>志愿者管理
          <el-badge v-if="pendingPromo" value="!" type="danger" />
        </template>

        <!-- 场次选择栏 -->
        <div class="toolbar" style="margin-bottom:12px">
          <span>场次</span>
          <el-select v-model="currentGroupId" style="width:260px">
            <el-option v-for="g in shifts" :key="g.id"
                       :value="g.id" :label="`${g.name}（${g.enrolled}/${g.capacity}）`" />
          </el-select>
          <el-button type="primary" plain @click="openNotify">
            <el-icon><Bell /></el-icon>&nbsp;发送通知
          </el-button>
          <el-button plain @click="doExport" style="margin-left:auto">
            <el-icon><Download /></el-icon>&nbsp;导出排班表
          </el-button>
        </div>

        <!-- 报名后筛选：先录取，录取的人才进下面的名单 -->
        <ApplicationList v-if="act.recruit_mode === 'screened'" :activity-id="act.id"
                         style="margin-bottom:14px"
                         @archive="openArchive" @changed="loadAll" />

        <template v-if="currentShift">
          <!-- 已报名名单：满宽；动作按钮都在这一行 -->
          <div class="section-title roster-head" style="margin-top:0">
            <span>已报名名单（{{ roster.enrolled.length }} 人）</span>
            <span class="roster-actions">
              <el-button type="primary" size="small" :loading="assignPanel?.solving"
                         :disabled="isOver" @click="assignPanel.runPreview()">
                <el-icon><MagicStick /></el-icon>&nbsp;一键分配岗位
              </el-button>
              <RefreshBar ref="rosterRefresh" :load="refreshRoster" />
              <el-button size="small" type="success" plain :disabled="hoursFrozen"
                         @click="assignPanel.openWalkin()">补招志愿者</el-button>
            </span>
          </div>
          <DataCards v-if="narrow" :rows="roster.enrolled" title-key="user.name"
                     row-key="enrollment_id" style="margin-bottom:14px"
                     :fields="[{ key: 'user.student_id', label: '学号' },
                               { key: 'user.college', label: '学院' },
                               { key: 'user.phone', label: '手机号' },
                               { key: 'position_name', label: '岗位',
                                 format: v => v || '待分配' }]"
                     empty="还没有志愿者报名">
            <template v-if="roster.questions?.length" #body="{ row }">
              <AnswerList :answers="row.answers" />
            </template>
            <template #extra="{ row }">
              <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">组长</el-tag>
              <el-tag v-if="row.checked_in" size="small" type="success" effect="light">已到岗</el-tag>
              <el-tag size="small" effect="plain"
                      :type="row.allow_reassign ? 'success' : 'info'">
                {{ row.allow_reassign ? '接受调剂' : '不接受' }}
              </el-tag>
            </template>
            <template #actions="{ row }">
              <el-select v-model="moveSel[row.enrollment_id]" size="small" clearable
                         :disabled="isOver" placeholder="调至岗位…" style="width:150px">
                <el-option v-for="s in roster.positions" :key="s.id" :value="s.id"
                           :label="`${s.name}（余${Math.max(0, s.quota - s.enrolled)}）`" />
              </el-select>
              <el-button size="small" type="primary" plain
                         :disabled="isOver || moveSel[row.enrollment_id] === undefined"
                         @click="applyMove(row)">调岗</el-button>
              <el-button v-if="row.is_leader" size="small" link type="warning"
                         :disabled="isOver" @click="setLeader(row, false)">取消组长</el-button>
              <el-button v-else-if="canBeLeader(row)" size="small" link
                         :disabled="isOver" @click="setLeader(row, true)">设为组长</el-button>
              <ScoreAlert :user="row.user" />
              <el-button size="small" link @click="openArchive(row.user)">档案</el-button>
              <el-button size="small" link type="danger"
                         @click="violationDlg.open(row.user)">登记违规</el-button>
            </template>
          </DataCards>
          <el-table v-else :data="roster.enrolled" size="small" max-height="300" style="margin-bottom:14px">
            <el-table-column v-if="roster.questions?.length" type="expand" width="30">
              <template #default="{ row }"><AnswerList :answers="row.answers" /></template>
            </el-table-column>
            <el-table-column label="姓名" width="110">
          <template #default="{row}">
            <ScoreAlert :user="row.user" />
            <el-link type="primary" underline="never" @click="openArchive(row.user)">
              {{ row.user.name }}
            </el-link>
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
            <el-table-column label="岗位" min-width="150">
              <template #default="{row}">
                <el-tag v-if="row.position_group" size="small" effect="plain"
                        type="warning" style="margin-right:4px">
                  {{ row.position_group }}
                </el-tag>
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
                           :disabled="isOver" @click="setLeader(row, false)">取消</el-button>
                <el-tooltip v-else-if="canBeLeader(row)" placement="top"
                            content="设为本岗位组组长">
                  <el-button size="small" link :disabled="isOver"
                             @click="setLeader(row, true)">
                    指定
                  </el-button>
                </el-tooltip>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="违规" width="70">
              <template #default="{row}">
                <el-button size="small" link type="danger"
                           @click="violationDlg.open(row.user)">登记</el-button>
              </template>
            </el-table-column>
            <el-table-column v-if="roster.positions.length" label="调岗" width="210">
              <template #default="{row}">
                <el-select v-model="moveSel[row.enrollment_id]" size="small"
                           clearable :disabled="isOver" placeholder="调至岗位…"
                           style="width:140px">
                  <el-option v-for="s in roster.positions" :key="s.id" :value="s.id"
                             :label="`${s.name}（余${Math.max(0, s.quota - s.enrolled)}）`" />
                </el-select>
                <el-button size="small" link type="primary"
                           :disabled="isOver || moveSel[row.enrollment_id] === undefined"
                           @click="applyMove(row)">应用</el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-row :gutter="14" style="margin-top:14px">
            <!-- 左栏（宽）：分配程序计算结果、人员变动 -->
            <el-col :xs="24" :md="16">
              <div class="section-title" style="margin-top:0">一键分配结果</div>
              <AssignPanel ref="assignPanel" :shift="currentShift"
                           :unassigned-count="unassignedCount" @reload="loadAll" />
            <!-- 人员变动流水 -->
            <template v-if="roster.roster_changes?.length">
              <div class="section-title">人员变动</div>
              <el-timeline>
                <el-timeline-item v-for="(c, i) in roster.roster_changes" :key="i"
                                  :timestamp="fmtDT(c.created_at)" placement="top"
                                  :type="['cancel','abandon'].includes(c.action)
                                         ? 'danger' : 'primary'">
                  <el-tag size="small" effect="plain"
                          :type="['cancel','abandon'].includes(c.action)
                                 ? 'danger' : 'success'">
                    {{ c.action_label }}
                  </el-tag>
                  <span style="margin-left:6px">{{ c.summary }}</span>
                </el-timeline-item>
              </el-timeline>
            </template>
            </el-col>

            <!-- 右栏（窄）：扣留的名额、候补队列、退选记录 -->
            <el-col :xs="24" :md="8">
            <!-- 临期退选扣留的名额：不列出来老师根本找不到它 -->
            <template v-if="roster.held?.length">
              <div class="section-title">
                临期退选扣留的名额（{{ roster.held.length }}）
                <span class="dim" style="font-weight:400">
                  防止影响活动安排，临期退选占用名额，可手动释放递补
                </span>
              </div>
              <DataCards :rows="roster.held" title-key="user.name"
                         row-key="enrollment_id"
                         :fields="[{ key: 'user.student_id', label: '学号' },
                                   { key: 'position_name', label: '岗位',
                                     format: v => v || '未分配' }]">
                <template #actions="{ row }">
                  <el-popconfirm title="确认释放该名额？" :disabled="isOver"
                                 @confirm="releaseSeat(row)">
                    <template #reference>
                      <el-button size="small" type="primary" plain
                                 :disabled="isOver">释放名额</el-button>
                    </template>
                  </el-popconfirm>
                </template>
              </DataCards>
            </template>
            <el-card shadow="never" class="wl-card">
              <template #header><b>候补队列</b></template>
              <el-alert v-if="roster.auto_promote_over" type="warning" show-icon
                        :closable="false" style="margin-bottom:10px">
                <template #title>
                  自动递补已停止（{{ fmtDT(roster.auto_promote_deadline) }} 起）
                </template>
                <div style="line-height:1.7">
                  离活动开始不足 24 小时，且报名已截止，停止自动递补，确需补人请手动「转正」通知本人确认
                </div>
              </el-alert>
              <div v-for="w in roster.waitlist" :key="w.enrollment_id" class="wl-row">
                <el-tag size="small" circle>{{ w.queue_position }}</el-tag>
                {{ w.user.name }}
                <span class="dim">{{ w.user.student_id }}</span>
                <el-tag v-if="w.position_name" size="small" effect="plain">
                  {{ w.position_name }}
                </el-tag>
                <el-tag v-if="w.status === 'pending_confirm'" size="small" type="danger">
                  待确认 {{ w.confirm_deadline?.slice(11,16) }} 前
                </el-tag>
                <el-popconfirm v-if="w.status === 'waitlisted'"
                    :title="`通知 ${w.user.name} 确认转正？超时未确认名额将顺延`"
                    @confirm="promoteManually(w)">
                  <template #reference>
                    <el-button size="small" link type="primary" :disabled="isOver"
                               style="margin-left:auto">转正</el-button>
                  </template>
                </el-popconfirm>
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
            </el-col>
          </el-row>
        </template>
      </el-tab-pane>

      <!-- ============ 签到管理 ============ -->
      <el-tab-pane v-if="isPublic" label="签到管理" name="checkin" lazy>
        <!-- 场次由面板自己选：这一页在盯现场，跟「报名与调配」那边排人是两件事 -->
        <CheckinPanel v-if="allShifts.length" :activity-id="act.id"
                      :all-shifts="allShifts" :over="isOver"
                      :hours-frozen="hoursFrozen" />
        <el-empty v-else description="请先创建场次" />
      </el-tab-pane>

      <!-- ============ 志愿服装 ============ -->
      <el-tab-pane v-if="isPublic && act.attire_issued" label="志愿服装" name="attire" lazy>
        <AttirePanel :activity-id="act.id" />
      </el-tab-pane>

      <!-- ============ 时长管理 ============ -->
      <el-tab-pane v-if="isPublic" name="hours" lazy>
        <template #label>时长管理
          <el-badge v-if="pendingAppeals" :value="pendingAppeals" type="danger" />
        </template>
        <div class="toolbar">
          <el-button type="primary" :disabled="!confirmedCount" @click="doExportHours">
            <el-icon><Download /></el-icon>&nbsp;导出时长记录
          </el-button>
          <!-- 说明文字要能换行：窄屏下 toolbar 的子项不收缩，不给宽度就会被裁掉 -->
          <span class="dim" style="flex:1 1 260px">用于志愿北京时长录入，选择劳务的人不在其中，包含身份证号，下载留痕</span>
        </div>

        <!-- 时长申诉：出了岔子的那一条才走这里，不是全员自报 -->
        <template v-if="appeals.length">
          <el-divider />
          <div class="toolbar">
            <b>时长申诉</b>
          </div>
          <el-table :data="appeals" size="small" style="margin-top:6px">
            <el-table-column label="志愿者" width="150">
              <template #default="{ row }">
                {{ row.name }}<span class="dim mono"> {{ row.student_id }}</span>
              </template>
            </el-table-column>
            <el-table-column label="原时长 → 申报时长" width="140">
              <template #default="{ row }">
                {{ row.old_hours }}h → <b>{{ row.claimed_hours }}h</b>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="理由" min-width="220" show-overflow-tooltip />
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag size="small" effect="plain"
                        :type="{ pending: 'warning', accepted: 'success',
                                 rejected: 'info' }[row.status]">
                  {{ row.label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160">
              <template #default="{ row }">
                <template v-if="row.status === 'pending'">
                  <el-button size="small" type="primary" plain :disabled="hoursFrozen"
                             @click="openResolve(row, true)">采纳</el-button>
                  <el-button size="small" plain :disabled="hoursFrozen"
                             @click="openResolve(row, false)">不采纳</el-button>
                </template>
                <span v-else class="dim">{{ row.reply || '—' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </template>
        <el-divider />
        <div class="toolbar">
          <el-button @click="hoursPreview()" :loading="hoursLoading"
                     :disabled="hoursFrozen">基于签到记录计算时长</el-button>
          <el-button type="primary" plain :disabled="hoursFrozen || !hoursSel.length"
                     @click="confirmSelectedHours">
            {{ hoursSel.length ? `确认选中时长（${hoursSel.length} 人）` : '确认选中时长' }}
          </el-button>
          <el-tooltip content="确认正常签退的记录；未签退/自动兜底请逐条核对后手动确认" placement="top">
            <el-button type="primary" :disabled="hoursFrozen || !hours.length"
                       @click="confirmAllHours">
              确认正常签退的
            </el-button>
          </el-tooltip>
          <el-tooltip :disabled="!!confirmedCount" placement="top"
                      content="请先确认时长">
            <span>
              <el-button v-if="!hoursFrozen" type="success"
                         :disabled="!confirmedCount" @click="openApprove">
                确认核准时长
              </el-button>
            </span>
          </el-tooltip>
          <el-button v-if="hoursState.approved_at && !hoursFrozen"
                     type="warning" plain @click="submitLockNow">提交时长审核</el-button>
          <el-button v-if="hoursState.locked_at" type="warning" plain
                     @click="openUnlock">申请解锁</el-button>
        </div>

        <!-- 核准之后这一段就是这场活动的账本状态 -->
        <el-alert v-if="hoursState.locked_at" type="success" show-icon :closable="false"
                  style="margin:10px 0"
                  :title="`时长已于 ${fmtDT(hoursState.locked_at)} 锁定，活动已完成`">
          <div style="line-height:1.8">
            已通过校青协审核，时长不再改动。确需更正请点「申请解锁」，同样由校青协审批
            <template v-if="hoursState.request">
              <br />最近一条{{ hoursState.request.kind_label }}申请：
              {{ hoursState.request.status_label }} · {{ hoursState.request.reason }}
              <template v-if="hoursState.request.reply">
                （意见：{{ hoursState.request.reply }}）
              </template>
            </template>
          </div>
        </el-alert>
        <el-alert v-else-if="hoursState.lock_pending" type="warning" show-icon
                  :closable="false" style="margin:10px 0"
                  title="时长已提交校青协审核，审核期间本页只读">
          <div style="line-height:1.8">
            通过即锁定并把活动标记为「已完成」；驳回会退回给你，改完重新核准再提交一次。
            <template v-if="hoursState.request">
              <br />提交说明：{{ hoursState.request.reason }}
            </template>
          </div>
        </el-alert>
        <el-alert v-else-if="hoursState.approved_at" type="warning" show-icon
                  :closable="false" style="margin:10px 0"
                  :title="`已核准，申诉截止 ${fmtDT(hoursState.appeal_deadline)}`">
          <div style="line-height:1.8">
            志愿者已收到时长通知，将于截止前确认或提出申诉，超时未确认自动按「无误 + 志愿时长」处理。
            截止时没有申诉将自动提交校青协审核，<b>有申诉就不会自动提交</b>——改完再核准一次
            （只会再通知 申诉过或数值变过的人），或直接「提交时长审核」。
            <template v-if="hoursState.pending_appeals">
              <br />当前有 <b>{{ hoursState.pending_appeals }}</b> 条申诉待处理
            </template>
            <template v-if="hoursState.request?.kind === 'lock'
                            && hoursState.request.status === 'rejected'">
              <br />上次审核未通过：{{ hoursState.request.reply }}——改完重新核准再提交一次。
            </template>
          </div>
        </el-alert>
        <el-alert v-else type="info" show-icon :closable="false" style="margin:10px 0"
                  title="时长未核准，志愿者无法查看确认"
                  description="先算好、核对完，再点「确认核准时长」，志愿者将收到时长通知" />
        <div v-if="hours.length" class="scroll-x">
        <el-table :data="hoursSorted" size="small" style="margin-top:10px;min-width:660px"
                  max-height="360" @selection-change="onHoursSelect">
          <el-table-column type="selection" width="40" :selectable="() => !hoursFrozen" />
          <el-table-column type="expand" width="30">
            <template #default="{row}"><HoursParts :parts="row.parts" /></template>
          </el-table-column>
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
              <el-input-number v-model="row.editHours" size="small" :disabled="hoursFrozen"
                               :min="0" :step="0.5" style="width:130px" />
            </template>
          </el-table-column>
          <el-table-column label="标记" width="150">
            <template #default="{row}">
              <el-tooltip v-if="row.pending" content="未签退，暂按当前时间估算">
                <el-tag size="small" type="warning" effect="plain">待签退</el-tag>
              </el-tooltip>
              <el-tooltip v-else-if="row.auto_checkout"
                          content="超时未签退，已按场次结束时间兜底">
                <el-tag size="small" type="danger" effect="plain">待核·自动兜底</el-tag>
              </el-tooltip>
              <span v-else class="dim">正常签退</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{row}">
              <el-tag v-if="row.confirmed" type="success" effect="light">已确认</el-tag>
              <el-tag v-else effect="info">待确认</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="hoursState.approved_at" label="本人表态" width="170">
            <template #default="{row}"><VolunteerSide :row="row" /></template>
          </el-table-column>
        </el-table>
        </div>

        <!-- 手动添加时长 -->
        <div class="section-title" style="margin-top:18px">手动添加时长（场外支援/补录/通勤等情况）</div>
        <el-card shadow="never">
          <div class="toolbar" style="margin-bottom:8px;align-items:flex-start">
            <el-select v-model="mh.userIds" multiple filterable collapse-tags
                       collapse-tags-tooltip placeholder="选择志愿者（可多选）"
                       :disabled="hoursFrozen" style="width:320px">
              <el-option v-for="v in rosterOptions" :key="v.student_id"
                         :value="v.student_id"
                         :label="`${v.name}（${v.student_id}）· ${v.where}`" />
            </el-select>
            <el-input-number v-model="mh.hours" :min="0.5" :step="0.5"
                             :disabled="hoursFrozen" style="width:120px" />
            <el-input v-model="mh.note" placeholder="备注（如：场外支援）"
                      :disabled="hoursFrozen" style="width:220px" />
            <el-button type="primary" :disabled="hoursFrozen || !mh.userIds.length"
                       @click="addManualHours">
              批量添加{{ mh.userIds.length ? `（${mh.userIds.length} 人）` : '' }}
            </el-button>
          </div>
          <el-table v-if="manualHours.length" :data="manualHours" size="small">
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="student_id" label="学号" width="120" />
            <el-table-column prop="hours" label="时长(h)" width="90" />
            <el-table-column prop="note" label="备注" min-width="160" />
            <el-table-column v-if="hoursState.approved_at" label="本人表态" width="170">
              <template #default="{row}"><VolunteerSide :row="row" /></template>
            </el-table-column>
            <el-table-column label="操作" width="80">
              <template #default="{row}">
                <el-button v-if="!hoursFrozen" size="small" link type="danger"
                           @click="delManualHours(row)">删除</el-button>
                <span v-else class="dim">{{ hoursState.locked_at ? '已锁定' : '审核中' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ============ 管理者 ============ -->
      <el-tab-pane v-if="isPublic" label="签收情况" name="acks" lazy>
        <div class="toolbar" style="margin-bottom:10px">
          <RefreshBar ref="ackRefresh" :load="loadBatches" />
        </div>
        <div class="dim" style="margin-bottom:10px">
          只列要求签收的通知。签收 = 本人点过「我已知悉」。
        </div>
        <el-empty v-if="!ackBatches.length"
                  description="还没有要求签收的通知，通知时勾选「要求签收」即可" />
        <el-card v-for="b in ackBatches" :key="b.batch_id" shadow="never"
                 style="margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <b>{{ b.title }}</b>
            <span class="dim">{{ fmtDT(b.created_at) }}</span>
            <el-tag size="small" :type="b.acked === b.total ? 'success' : 'warning'">
              已签收 {{ b.acked }}/{{ b.total }}
            </el-tag>
            <span v-if="b.reminded" class="dim">
              已催办 {{ b.reminded }} 次 · 最近 {{ fmtDT(b.last_remind_at) }}
            </span>
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
          一个活动可由多人共同管理：创建者自动成为管理者，管理者之间权限相同，
          管理者须为<b>活动发起方/青协</b>。
        </el-alert>
        <el-card shadow="never">
          <div class="toolbar" style="margin-bottom:10px">
            <el-input v-model="newMgrSid" placeholder="输入学工号/教工号添加管理者" style="width:220px" />
            <el-button type="primary" @click="addManager">添加管理者</el-button>
          </div>
          <DataCards v-if="narrow" :rows="managers" title-key="name"
                     :fields="[{ key: 'student_id', label: '学号' },
                               { key: 'college', label: '单位' },
                               { key: 'role_label', label: '身份' }]">
            <template #extra="{ row }">
              <el-tag v-if="row.is_creator" size="small" type="warning" effect="plain">创建者</el-tag>
            </template>
            <template #body="{ row }">
              <el-select :model-value="row.duties" multiple collapse-tags size="small"
                         style="width:100%" placeholder="接收全部通知"
                         @update:model-value="v => saveDuties(row, v)">
                <el-option v-for="(label, key) in dutyOptions" :key="key"
                           :value="key" :label="label" />
              </el-select>
            </template>
            <template #actions="{ row }">
              <el-popconfirm v-if="!row.is_creator && row.id !== auth.user.id"
                             title="移除管理权限？" @confirm="removeManager(row)">
                <template #reference>
                  <el-button size="small" link type="danger">移除</el-button>
                </template>
              </el-popconfirm>
              <span v-else class="dim">{{ row.is_creator ? '创建者不可被移除' : '不能移除自己' }}</span>
            </template>
          </DataCards>
          <el-table v-else :data="managers" size="small">
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="student_id" label="学号" width="120" />
            <el-table-column prop="role_label" label="身份" min-width="140" />
            <el-table-column label="角色" width="110">
              <template #default="{row}">
                <el-tag v-if="row.is_creator" size="small" type="warning" effect="light">创建者</el-tag>
                <el-tag v-else size="small" effect="plain">管理者</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="通知分工" min-width="240">
              <template #default="{row}">
                <el-select :model-value="row.duties" multiple collapse-tags
                           size="small" style="width:100%" placeholder="接收全部通知"
                           @update:model-value="v => saveDuties(row, v)">
                  <el-option v-for="(label, key) in dutyOptions" :key="key"
                             :value="key" :label="label" />
                </el-select>
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
            <template v-if="screened">
              报名后筛选：建好岗位的场次，志愿者按岗位勾意向；没建岗位的场次
              勾的是「待分配」，录取之后再排岗位。
            </template>
            <template v-else-if="groupForm.mode === 'pick'">
              提前设置岗位分工，学生报名时自己挑岗位。
            </template>
            <template v-else>
              适用于未敲定岗位分工的情况，学生报名时不挑岗位，
              敲定后把本场次改回「报名即选岗位」再给志愿者分配岗位。
            </template>
          </div>
          <!-- 改成 assign 会作废学生已经选好的岗位，先把代价摆出来 -->
          <el-alert v-if="modeSwitchCost" type="warning" show-icon :closable="false"
                    style="margin-top:8px" :title="modeSwitchCost" />
        </el-form-item>
        <el-alert type="info" :closable="false" style="margin-bottom:14px">
          这里只填本场次的<b>服务时间</b>。培训、彩排等在场次建好后用「添加培训/彩排」单独添加。
        </el-alert>
        <el-form-item label="开始时间" required>
          <DateTimeField v-model="groupForm.start_time" :default-time="DAY_START"
                         @change="onShiftStartPicked" />
        </el-form-item>
        <el-form-item label="结束时间" required>
          <DateTimeField v-model="groupForm.end_time" :default-time="DAY_END" />
        </el-form-item>
        <!-- 名额与候补只属于「截止后统一分配」：报名即选岗位时它们在岗位上 -->
        <template v-if="groupForm.mode === 'assign'">
          <el-form-item label="人数上限" required>
            <el-input-number v-model="groupForm.capacity" :min="1" />
          </el-form-item>
          <el-form-item label="候补名额">
            <el-input-number v-model="groupForm.waitlist_quota" :min="0" />
            <span class="dim" style="margin-left:10px">0 = 不设候补</span>
          </el-form-item>
        </template>
        <el-form-item label="条件要求">
          <div class="two-col">
            <el-select v-model="groupForm.campusReq" multiple clearable collapse-tags
                       placeholder="校区（不限）">
              <el-option v-for="c in campuses" :key="c" :value="c" :label="c" />
            </el-select>
            <el-select v-model="groupForm.stageReq" multiple clearable collapse-tags
                       placeholder="学段（不限）">
              <el-option v-for="s in stages" :key="s" :value="s" :label="s" />
            </el-select>
          </div>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="groupForm.note" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="shiftDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="savingGroup" @click="saveShift">保存</el-button>
      </template>
    </el-dialog>

    <!-- 培训 / 彩排（附属场次） -->
    <el-dialog v-model="attDlg.visible"
               :title="(attDlg.editing ? '编辑' : '添加') + '培训 / 彩排'" width="560">
      <p class="dim" style="margin-top:0">
        挂在「{{ attDlg.parent?.name }}」下面：学生报该场次时连带报上、退选时连带退。
        自己不设名额，但有独立的签到码与时长。
      </p>
      <el-form label-width="100px">
        <el-form-item label="类型" required>
          <el-radio-group v-model="attForm.kind">
            <el-radio-button value="training">培训</el-radio-button>
            <el-radio-button value="rehearsal">彩排</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="attForm.name" placeholder="如：集中培训" />
        </el-form-item>
        <el-form-item label="开始时间" required>
          <DateTimeField v-model="attForm.start_time" :default-time="DAY_START" />
        </el-form-item>
        <el-form-item label="结束时间" required>
          <DateTimeField v-model="attForm.end_time" :default-time="DAY_END" />
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="attForm.location" placeholder="如：明德楼 0201 、线上等（留空则用活动地点）" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="attForm.note" placeholder="如：请自带笔记本；会议链接活动前一天发群里" />
        </el-form-item>
        <el-form-item label="需参与岗位">
          <el-select v-model="attForm.applies_to_positions" multiple collapse-tags
                     placeholder="留空 = 本场次所有人" style="width:100%">
            <el-option v-for="p in attDlg.parent?.positions || []" :key="p.id"
                       :value="p.id" :label="p.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="时长认定">
          <el-radio-group v-model="attForm.hours_mode">
            <el-radio value="fixed">固定计时</el-radio>
            <el-radio value="checkin">扫码计时</el-radio>
            <el-radio value="none">不计时长</el-radio>
          </el-radio-group>
          <div class="dim" style="line-height:1.7">
            {{ HOURS_HINT[attForm.hours_mode] }}
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="attDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="attDlg.busy" @click="saveAttached">保存</el-button>
      </template>
    </el-dialog>

    <!-- 核准时长：定下账、通知本人，并给一段申诉时间 -->
    <el-dialog v-model="approveDlg.visible" title="确认核准时长" width="480">
      <p class="dim" style="margin-top:0;line-height:1.8">
        核准之后，志愿者将会收到确认通知，截止前确认无误或提出申诉。截止后若无申诉，系统自动锁定并把活动标记为「已完成」。
      </p>
      <el-form label-width="100px">
        <el-form-item label="申诉截止" required>
          <DateTimeField v-model="approveDlg.deadline" :default-time="DAY_END" />
          <div class="dim">超时将默认无异议 + 选择志愿时长</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approveDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="approveDlg.busy" @click="doApprove">
          核准并通知
        </el-button>
      </template>
    </el-dialog>

    <!-- 报名问卷：题目不常改，收进弹窗，页签上只留一个按钮 -->
    <el-dialog v-model="formDlg" title="报名问卷" width="760">
      <FormBuilder :key="act.status" :activity-id="act.id" :editable="!isPublic"
                   :screened="act.recruit_mode === 'screened'"
                   @count="n => formCount = n" />
    </el-dialog>

    <!-- 提交审批 -->
    <el-dialog v-model="submitDlg.visible" title="提交校青协审批" width="500">
      <el-form label-width="88px">
        <el-form-item label="审批部门" required>
          <el-select v-model="submitDlg.deptId" placeholder="选择归属部门" style="width:100%">
            <el-option v-for="d in depts" :key="d.id" :value="d.id" :disabled="!d.has_head"
                       :label="d.name + (d.note ? ' ' + d.note : '')
                               + (d.head_name ? `（部长 ${d.head_name}）` : '（未设部长）')" />
          </el-select>
        </el-form-item>
        <el-form-item label="补充说明">
          <el-input v-model="submitDlg.note" type="textarea" :rows="2"
                    placeholder="选填" />
        </el-form-item>
        <el-form-item label="通过之后">
          <div>
            <el-radio-group v-model="submitDlg.autoPublish">
              <el-radio :value="true">通过即发布</el-radio>
              <el-radio :value="false">通过后自行发布</el-radio>
            </el-radio-group>
            <div class="dim">
              发布只是在活动广场面向志愿者公开，开放报名仍由「报名开始时间」决定。
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="submitDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="submitDlg.busy" @click="doSubmit">提交</el-button>
      </template>
    </el-dialog>

    <!-- 恢复报名：必须重设截止时间，否则定时任务下一分钟又给截了 -->
    <el-dialog v-model="reopenDlg.visible" title="恢复报名" width="460">
      <p class="dim" style="margin-top:0;line-height:1.8">
        重新开放报名，并延后报名截止时间。<b>必须填新的截止时间</b>。
      </p>
      <el-form label-width="110px">
        <el-form-item label="截止时间" required>
          <DateTimeField v-model="reopenDlg.regEnd" :default-time="DAY_END"
                         placeholder="晚于此刻，且不晚于最后一个场次开始" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reopenDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="reopenDlg.busy" @click="doReopen">恢复报名</el-button>
      </template>
    </el-dialog>

    <!-- 处理时长申诉 -->
    <el-dialog v-model="resolveDlg.visible"
               :title="resolveDlg.accept ? '采纳申诉' : '不采纳申诉'" width="480">
      <p class="dim" style="margin-top:0;line-height:1.8">
        {{ resolveDlg.row?.name }}：{{ resolveDlg.row?.old_hours }}h →
        申报 {{ resolveDlg.row?.claimed_hours }}h<br />
        理由：{{ resolveDlg.row?.reason }}
      </p>
      <el-form label-width="88px">
        <el-form-item v-if="resolveDlg.accept" label="改为">
          <el-input-number v-model="resolveDlg.hours" :min="0.5" :max="24" :step="0.5" />
          <span class="dim" style="margin-left:10px">小时</span>
        </el-form-item>
        <el-form-item label="答复">
          <el-input v-model="resolveDlg.reply" type="textarea" :rows="2" maxlength="500"
                    :placeholder="resolveDlg.accept ? '选填，会发给本人'
                                                    : '建议说明理由'" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resolveDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="resolveDlg.busy" @click="doResolve">
          确认{{ resolveDlg.accept ? '采纳' : '不采纳' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 活动招募：文案与二维码在一处，发群、贴海报都从这里拿 -->
    <el-dialog v-model="posterDlg.visible" title="活动招募" width="760">
      <div class="recruit">
        <div class="recruit-text">
          <el-input v-model="posterDlg.text" type="textarea" :rows="16" readonly />
          <el-button type="primary" style="margin-top:10px" @click="copyPoster">
            复制文案
          </el-button>
        </div>
        <div class="recruit-qr">
          <img :src="`/api/activities/${activityId}/qr.svg?base=${qrBase()}`"
               width="200" height="200" alt="活动二维码" />
          <div class="dim">扫码直达活动详情页</div>
          <el-input size="small" readonly :model-value="shareUrl" style="margin-top:8px">
            <template #append><el-button @click="copyShare">复制链接</el-button></template>
          </el-input>
        </div>
      </div>
      <template #footer>
        <el-button @click="posterDlg.visible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 岗位对话框 -->
    <el-dialog v-model="positionDlg.visible" :title="positionDlg.editing ? '编辑岗位' : '新建岗位'" width="560">
      <el-form label-width="100px">
        <el-form-item label="岗位名称" required><el-input v-model="stationForm.name" placeholder="如：东门引导" /></el-form-item>
        <el-form-item label="服务地点"><el-input v-model="stationForm.location"
                    placeholder="如：世纪馆北门（留空则用活动地点）" /></el-form-item>
        <el-form-item label="名额" required>
          <el-input-number v-model="stationForm.quota" :min="1" />
        </el-form-item>
        <el-form-item label="候补名额">
          <el-input-number v-model="stationForm.waitlist_quota" :min="0" />
          <span class="dim" style="margin-left:10px">满员后可排队的人数，0 = 不设候补</span>
        </el-form-item>
        <el-form-item label="岗位组">
          <el-input v-model="stationForm.group_name" style="width:220px"
                    placeholder="如：东门片区（选填）" />
          <div class="dim">岗位组共用一名组长，留空则本岗位自成一组</div>
        </el-form-item>
        <el-form-item label="志愿服装">
          <el-radio-group v-model="stationForm.attire">
            <el-radio value="none">不需要</el-radio>
            <el-radio value="issued">统一发放</el-radio>
            <el-radio value="own">志愿者自备</el-radio>
          </el-radio-group>
          <el-input v-if="stationForm.attire !== 'none'" v-model="stationForm.attire_note"
                    style="margin-top:6px"
                    :placeholder="stationForm.attire === 'issued'
                      ? '领取/归还方式（必填）：如 活动前一天 8:00 在 学活124 领取'
                      : '服装备注（必填）：如 白色上衣、深色长裤、运动鞋'" />
          <div class="dim">统一发放可在「志愿服装」页扫码或手动登记领取与归还</div>
        </el-form-item>
        <el-form-item label="报酬方式">
          <el-checkbox v-model="stationForm.pay_optional">本岗位可选劳务</el-checkbox>
          <el-input v-if="stationForm.pay_optional" v-model="stationForm.pay_note"
                    style="margin-top:6px"
                    placeholder="劳务备注（必填）：如 60元/小时，活动后一周内发放" />
          <div class="dim">按需勾选。若勾选，志愿者确认时长时选择报酬方式，劳务与时长二选一</div>
        </el-form-item>
        <el-form-item label="组长">
          <el-checkbox v-model="stationForm.needs_leader">本组需要一名组长</el-checkbox>
          <div class="dim">在「志愿者管理」页面指定，报名时志愿者可表态供你参考。</div>
        </el-form-item>
        <el-form-item label="条件要求">
          <div class="two-col">
            <el-select v-model="stationForm.genderReq" clearable placeholder="性别（不限）">
              <el-option value="女" label="限女生" /><el-option value="男" label="限男生" />
            </el-select>
            <el-select v-model="stationForm.collegeReq" multiple clearable collapse-tags
                       placeholder="学院（不限）">
              <el-option v-for="c in colleges" :key="c" :value="c" :label="c" />
            </el-select>
          </div>
        </el-form-item>
        <el-form-item label="服务任务" required><el-input v-model="stationForm.service_task"
                    type="textarea" :rows="2"
                    placeholder="这个岗位做什么，如：在北门引导来访家长，协助检录。" /></el-form-item>
        <!-- 岗位要求是给人看的文字，与上面「条件要求」那种系统拿去拦人的硬条件不同 -->
        <el-form-item label="岗位要求"><el-input v-model="stationForm.position_requirement"
                    type="textarea" :rows="2"
                    placeholder="选填。对志愿者的要求，如：需参与培训等" /></el-form-item>
        <el-form-item label="培训内容"><el-input v-model="stationForm.training_note"
                    type="textarea" :rows="2"
                    placeholder="选填，可后续添加" /></el-form-item>
        <el-form-item label="学习材料">
          <!-- 新建时也能传：先攒在本地，保存建出岗位后再一并上传 -->
          <MaterialManager ref="materialMgr" owner-type="position"
                           :owner-id="positionDlg.editing?.id || ''"
                           :items="positionDlg.editing?.attachments || []"
                           @changed="onMaterialChanged" />
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
        从「{{ copyPosDlg.source?.name }}」复制岗位，含名额、条件、组长与材料；同名的跳过。
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
        <el-descriptions-item label="人数">已报名 {{ gdData.enrolled }} / {{ gdData.capacity }}
          <span v-if="gdData.held_seats" style="color:#e6a23c">
            · {{ gdData.held_seats }} 个名额因临期退选被扣留（不再释放）</span>
          <span class="dim">（候补 {{ gdData.waitlist_count }} 人）</span></el-descriptions-item>
        <el-descriptions-item label="说明">{{ gdData.note || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
    <el-dialog v-model="sdVisible" :title="(sdData?.name || '') + ' · 岗位详情'" width="460">
      <el-descriptions v-if="sdData" :column="1" border>
        <el-descriptions-item label="所属场次">{{ sdGroupName }}</el-descriptions-item>
        <el-descriptions-item label="服务地点">{{ sdData.location || act.location || '待定' }}</el-descriptions-item>
        <el-descriptions-item label="名额">{{ sdData.enrolled }}/{{ sdData.quota }}</el-descriptions-item>
        <el-descriptions-item label="条件要求">{{ sdData.requirement_desc }}</el-descriptions-item>
        <el-descriptions-item label="服务任务"><span class="pre">{{ sdData.service_task || '—' }}</span></el-descriptions-item>
        <el-descriptions-item label="岗位要求"><span class="pre">{{ sdData.position_requirement || '—' }}</span></el-descriptions-item>
        <el-descriptions-item label="志愿服装">{{ sdData.attire_text || '不需要' }}</el-descriptions-item>
        <el-descriptions-item label="报酬方式">{{ sdData.pay_text || '志愿时长' }}</el-descriptions-item>
        <el-descriptions-item label="培训内容"><span class="pre">{{ sdData.training_note || '—' }}</span></el-descriptions-item>
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
                    placeholder="请输入通知正文" />
        </el-form-item>
        <el-form-item label="">
          <el-checkbox v-model="notifyForm.need_ack">要求志愿者签收</el-checkbox>
          <div class="dim">
            勾选后，志愿者的通知会显红并附「我已知悉」按钮，可在「签收情况」查看并一键提醒。
            仅重要通知勾选。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="notifyDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="notifyDlg.saving" @click="sendNotify">发送</el-button>
      </template>
    </el-dialog>

    <!-- 基本信息编辑 -->
    <el-dialog v-model="editBase" title="编辑活动基本信息" width="620">
      <ActivityForm v-model="baseForm" v-model:range="baseRange" :modes="modes" />
      <template #footer>
        <el-button @click="editBase = false">取消</el-button>
        <el-button type="primary" :loading="savingBase" @click="saveBase()">保存并通知变更</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, h, nextTick, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox, ElTag } from 'element-plus'
import { api, download as dl, fmtDT, pageUrl, qrBase } from '../api'
import AssignPanel from '../components/AssignPanel.vue'
import CheckinPanel from '../components/CheckinPanel.vue'
import RefreshBar from '../components/RefreshBar.vue'
import AttirePanel from '../components/AttirePanel.vue'
import MaterialManager from '../components/MaterialManager.vue'
import DataCards from '../components/DataCards.vue'
import ActivityForm from '../components/ActivityForm.vue'
import ArchiveDrawer from '../components/ArchiveDrawer.vue'
import DateTimeField from '../components/DateTimeField.vue'
import ScoreAlert from '../components/ScoreAlert.vue'
import ViolationDialog from '../components/ViolationDialog.vue'
import FormBuilder from '../components/FormBuilder.vue'
import AnswerList from '../components/AnswerList.vue'
import HoursParts from '../components/HoursParts.vue'
import ApplicationList from '../components/ApplicationList.vue'
import { activityBody, activityToForm, validateActivity } from '../composables/activityForm'
import { useNarrow } from '../composables/useNarrow'
import { DAY_END, DAY_START, auth, canSee } from '../store'

const route = useRoute()
const act = ref(null)
const narrow = useNarrow()
const tab = ref(String(route.query.tab || 'tree'))   // 支持 ?tab=roster 直达页签
// 未发布（草稿/待审批/待发布）只留 岗位设置 / 管理者 / 变更记录：那时一个报名的人
// 都没有，名单、签到、服装、导出、签收全是空页
const isPublic = computed(() =>
  ['published', 'closed', 'finished', 'completed'].includes(act.value?.status))
const formDlg = ref(false)
const formCount = ref(0)
const formLabel = computed(() => (isPublic.value ? '查看报名问卷'
  : formCount.value ? `报名问卷（${formCount.value} 题）` : '设置报名问卷'))
const modes = ref({})
const colleges = ref([])
const campuses = ref([])
const stages = ref([])

/** {校区: [...], 学段: [...]}，空的那项不写进去——写了空数组等于「限定为空」。 */
function scopeTags(campusReq, stageReq) {
  const tags = {}
  if (campusReq?.length) tags['校区'] = campusReq
  if (stageReq?.length) tags['学段'] = stageReq
  return tags
}
const loading = ref(false)
const notFound = ref(false)
const loadError = ref('')
const savingGroup = ref(false)
const savingStation = ref(false)
const savingBase = ref(false)
const managers = ref([])
const newMgrSid = ref('')
const moveSel = ref({})

// —— 发送通知 ——
const notifyDlg = ref({ visible: false, saving: false })
const notifyForm = ref({ scope: 'all', shift_id: '', title: '', content: '',
                         need_ack: false })
const ackBatches = ref([])
const ackRefresh = ref(null)
const rosterRefresh = ref(null)

// —— 签收情况 ——
async function loadBatches() {
  ackBatches.value = await api.get(
    `/api/staff/activities/${activityId}/notify-batches`)
}

async function remindBatch(b) {
  const d = await api.post(`/api/staff/notify-batches/${b.batch_id}/remind`)
  ElMessage.success(`已催 ${d.count} 位未签收志愿者`)
  loadBatches()
}

// —— 岗位设置 ——
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
// 草稿不能直接发布：要先过审批。approved 才轮到「保存并发布」
const canPublish = computed(
  () => !!act.value && (act.value.status === 'approved' || act.value.editing))

const publishConfirm = computed(() => act.value?.status === 'approved'
  ? '发布后学生即可报名，确认？'
  : '恢复报名，并把改动通知志愿者？')

async function saveAndPublish() {
  lockBusy.value = true
  try {
    if (act.value.status === 'approved') {
      await api.post(`/api/staff/activities/${activityId}/publish`)
      ElMessage.success('已发布，学生现在可以报名了')
    } else {
      const d = await api.post(`/api/staff/activities/${activityId}/publish-changes`)
      ElMessage.success((d.changes
        ? `已恢复报名，${d.changes} 项改动已通知志愿者`
        : '已恢复报名（本次没有实质改动）')
        + (d.promoted ? `；已通知 ${d.promoted} 位候补确认转正` : ''))
    }
    loadAll()
  } finally { lockBusy.value = false }
}

// 编辑态下离开本页：改动确实存下来了，但学生还在「报名暂停」里等着，
// 而这件事离开这一页就再也看不见——所以走之前必须拦一下。
onBeforeRouteLeave(async () => {
  if (!act.value?.editing) return true
  try {
    await ElMessageBox.confirm(
      `「${act.value.title}」还在调整状态，`
      + `报名暂停（${lockHours.value} 小时后系统才会自动恢复）。`,
      '改动还没发布',
      { type: 'warning', distinguishCancelAndClose: true,
        confirmButtonText: '保存并发布', cancelButtonText: '暂时离开' })
  } catch (e) {
    return e === 'cancel'      // 暂时离开=放行；点 X / Esc = 留在本页
  }
  await saveAndPublish()
  return true
})

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
const roster = ref({ enrolled: [], waitlist: [], cancelled: [] })
const currentGroupId = ref('')
const hours = ref([])
// 要人过目的排前面：待核（未签退/自动兜底）→ 未确认 → 已确认。
// 组内保持后端顺序，行对象引用不变，编辑框与勾选都不受影响。
const hoursRank = r => (r.pending || r.auto_checkout ? 0 : r.confirmed ? 2 : 1)
const hoursSorted = computed(
  () => [...hours.value].sort((a, b) => hoursRank(a) - hoursRank(b)))
const hoursLoading = ref(false)
const hoursSel = ref([])                       // 时长表勾选行
const manualHours = ref([])                    // 手动添加的时长记录
const hoursState = ref({})                     // 核准/锁定状态，这一页按它决定给哪些按钮
const approveDlg = ref({ visible: false, deadline: '', busy: false })
const mh = ref({ userIds: [], hours: 0.5, note: '' })

// 手动加时长的候选人 = 本活动全部场次的已报名志愿者（含未签到的）
// pick 模式却一个岗位都没有 = 学生根本报不了名
// 报名已经结束的活动，再说「暂停报名」只会让人困惑
const editLabel = computed(() =>
  act.value?.status === 'published' ? '调整岗位（暂停报名）' : '调整岗位')

// 已发布的活动，不进编辑态就不能动结构
const structureLocked = computed(
  () => !!act.value && act.value.status !== 'draft' && !act.value.editing)

// 报名是否已经截止：截止后还有人没岗位，就不再是「等着分」而是漏排了
const regClosed = computed(
  () => ['closed', 'finished', 'completed'].includes(act.value?.status))
// 活动办完了：结构与基本信息都是只读
const isOver = computed(
  () => ['finished', 'completed'].includes(act.value?.status))

function unassignedHint(g) {
  if (g.mode === 'pick') {
    return '这些人报了名却没有岗位（岗位被删或管理员补录）；到「志愿者管理」手动指派'
  }
  return regClosed.value
    ? '报名已截止，这些人还没分到岗位；到「志愿者管理」分配'
    : '本场次报名只锁时段，岗位手动分配'
}

// 岗位设置页上的红色感叹号：这里列出的都是「不处理就会出事」的场次。
// assign 场次报名期没有岗位是设计如此，不报警；截止后还没建才是漏排。
const treeAlerts = computed(() => shifts.value.flatMap(g => {
  if (!g.positions.length) {
    if (g.mode === 'pick') {
      return [{ id: g.id, name: g.name,
        text: '还没有岗位，学生无法报名' }]
    }
    return regClosed.value
      ? [{ id: g.id, name: g.name,
           text: '报名已截止，还没建岗位。改为「报名即选岗位」并建好岗位，再一键分配。' }]
      : []
  }
  if (g.unassigned_count && (regClosed.value || g.mode === 'pick')) {
    return [{ id: g.id, name: g.name,
      text: `还有 ${g.unassigned_count} 人没分到岗位`
        + (sumQuota(g) < g.enrolled
           ? `，而岗位名额合计只有 ${sumQuota(g)}、已报名 ${g.enrolled} 人——`
             + '先加名额再分配。'
           : '，到「报名与调配」运行一键分配。') }]
  }
  return []
}))

// 手动补时长时要认得出人：同名同姓靠学号，排在哪儿靠场次与岗位。
// 一个人报了多个场次也只出现一次，把几段拼在同一行。
const rosterOptions = computed(() => {
  const byStudent = new Map()
  for (const g of shifts.value) {
    for (const row of g._roster?.enrolled || []) {
      const where = `${g.name}·${row.position_name || '待分配'}`
              + (row.is_leader ? '·组长' : '')
      const hit = byStudent.get(row.user.student_id)
      if (hit) hit.places.push(where)
      else byStudent.set(row.user.student_id, {
        name: row.user.name, student_id: row.user.student_id, places: [where] })
    }
  }
  return [...byStudent.values()].map(v => ({ ...v, where: v.places.join('、') }))
})
const logs = ref([])
const ACTION_LABELS = {
  create: '创建', update: '修改', delete: '删除', publish: '发布', close: '截止',
  finish: '结束', assign: '分配', assign_manual: '手动调岗', force_assign: '强制分配',
  export: '导出', hours_confirm: '时长核准',
  view_team: '查看组员',
}
const actionLabel = a => ACTION_LABELS[a] || a
const cancelType = x => ({ normal: '退选', late: '临近扣分', expired: '确认超时',
                           abandoned: '放弃候补', shift_deleted: '场次取消' }[x.cancel_type] || '取消')

let activityId = ''

async function loadAll() {
  loading.value = true
  try {
    // 活动不存在、或者这个人不共管它：两种情况给同一张「找不到」页，
    // 别让后面每个接口各弹一次 403
    let d = null
    try {
      d = await api.get(`/api/activities/${activityId}`, { silent: true })
    } catch { /* 下面统一处理 */ }
    if (!d?.manager) {
      notFound.value = true
      return
    }
    act.value = d
    const rosters = await Promise.all(d.shifts.map(g => api.get(`/api/staff/shifts/${g.id}/roster`)))
    d.shifts.forEach((g, i) => {
      g._roster = rosters[i]
    })
    const logsResp = await api.get(`/api/staff/audit-logs?activity_id=${activityId}&size=50`)
    logs.value = logsResp.items || []
    // 弹窗里的表要等打开才加载，题数得自己先问一句，按钮上要写
    formCount.value = (await api.get(
      `/api/staff/activities/${activityId}/form`)).questions.length
    await loadManagers()
    loadAppeals()      // 标签上的红点要一进页面就在，不能等切到那一页才知道
    // ?tab=hours 直达：watch(tab) 不会触发，时长这一页要在这里加载，
    // 否则核准按钮拿不到申诉截止时间的默认值
    if (tab.value === 'hours') hoursPreview(true)
    loadApproval()
    if (!currentGroupId.value && shifts.value.length) {
      currentGroupId.value = shifts.value[0].id
    }
    loadRoster()   // 无条件加载：act 赋值时 watcher 可能已抢先跑过一次空加载
  } catch (e) {
    loadError.value = e.message || '加载失败'
  } finally {
    loading.value = false
    // 记「更新于」必须等在这之后：loading 为真时整页是骨架屏，页签根本没渲染，
    // 刷新条的 ref 还是 null，写在 try 里等于没写。
    await nextTick()
    rosterRefresh.value?.markLoaded()
  }
}

const shifts = computed(() => act.value?.shifts || [])
const currentShift = computed(() => shifts.value.find(g => g.id === currentGroupId.value))

/** 拉平的场次清单：主场次 + 各自挂着的培训/彩排。
 *
 * 详情接口只在顶层给主场次，培训/彩排嵌在 attached 里。签到码要能给培训场次
 * 生成——hours_mode 是「按签到计」的培训，签不了到就没时长。
 */
const allShifts = computed(() => shifts.value.flatMap(g => [
  { id: g.id, name: g.name, kind: g.kind, kind_label: g.kind_label,
    time_text: g.time_text, start_time: g.start_time, end_time: g.end_time,
    positions: g.positions || [] },
  ...(g.attached || []).map(a => ({
    id: a.id, name: a.name, kind: a.kind, kind_label: a.kind_label,
    time_text: a.time_text, start_time: a.start_time, end_time: a.end_time,
    // 培训/彩排自己不设岗位（用 applies_to_positions 圈人），只能统一签到
    positions: [], parent_name: g.name })),
]))
const unassignedCount = computed(() =>
  (roster.value.enrolled || []).filter(x => !x.position_id).length)
const pendingPromo = computed(() => roster.value.waitlist.some(w => w.status === 'pending_confirm'))
// 已经确认下来的时长条数（含手动补录）：有账才谈得上核准
const confirmedCount = computed(
  () => hours.value.filter(h => h.confirmed).length + manualHours.value.length)

function sumQuota(g) {
  return g.positions.reduce((s, x) => s + x.quota, 0)
}

// —— 场次 CRUD ——
// 选完开始时间，结束时间自动落到当天最后一刻；已经填过就不动
function onShiftStartPicked(v) {
  if (!v || groupForm.value.end_time) return
  const end = new Date(v)
  end.setHours(23, 59, 59, 0)
  groupForm.value.end_time = end
}

function openShift(g) {
  shiftDlg.value.editing = g || null
  groupForm.value = g ? {
    name: g.name,
    start_time: new Date(g.start_time.replace(' ', 'T')),
    end_time: new Date(g.end_time.replace(' ', 'T')),
    capacity: g.capacity, mode: g.mode || 'pick', note: g.note,
    waitlist_quota: g.waitlist_quota ?? 3,
    campusReq: g.requirement_tags?.校区 || [],
    stageReq: g.requirement_tags?.学段 || [],
  } : { name: '', start_time: '', end_time: '', capacity: 10, note: '',
        mode: act.value.recruit_mode === 'screened' ? 'assign' : 'pick',   // 筛选活动多半先不定岗
        waitlist_quota: 3, campusReq: [], stageReq: [] }
  shiftDlg.value.visible = true
}
const screened = computed(() => act.value?.recruit_mode === 'screened')
/** 切模式的后果说明。反向切（pick → assign）只在没人报名时允许，
 *  有人之后后端直接 409——那会作废他们已经选定的岗位。 */
const modeSwitchCost = computed(() => {
  const g = shiftDlg.value.editing
  if (!g || g.mode === groupForm.value.mode) return ''
  if (groupForm.value.mode === 'assign') {
    return g.enrolled
      ? `本场次已有 ${g.enrolled} 人报名，不能改回「截止后统一分配」——`
        + '那会作废他们已经选定的岗位。如确需改，请新建场次。'
      : '改为「截止后统一分配」后，学生报名时不再挑岗位，'
        + '全部落在自动生成的「待分配」岗位上。'
  }
  return '改为「报名即选岗位」后可以开始建岗位。「待分配」岗位会留下来，'
    + '里面是还没安排的人——一键分配会把他们挪进新岗位，排不下的留在里面；'
    + '空了之后可以删掉它。'
})

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
      requirement_tags: scopeTags(f.campusReq, f.stageReq),
    }
    if (shiftDlg.value.editing) {
      await api.patch(`/api/staff/shifts/${shiftDlg.value.editing.id}`, body)
    } else {
      await api.post(`/api/staff/activities/${activityId}/shifts`, body)
    }
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
// 保存后从新数据里找回这个岗位，好让材料上传框接着用
function findPosition(id) {
  for (const g of shifts.value) {
    const hit = (g.positions || []).find(p => p.id === id)
    if (hit) return hit
  }
  return null
}

async function onMaterialChanged() {
  const id = positionDlg.value.editing?.id
  await loadAll()
  if (id) positionDlg.value.editing = findPosition(id)
}

// —— 培训 / 彩排 ——
const HOURS_HINT = {
  fixed: '到场即按本场时长计，不要求签退。彩排和集中培训人就在那儿，不值得卡分钟，也就没有「忘记签退」这回事。',
  checkin: '签退 − 签到，与服务场次一致。适合来去时间确实不一样的场合。',
  none: '只记出勤，不产生志愿时长。适合线上培训。',
}
const attDlg = ref({ visible: false, editing: null, parent: null, busy: false })
const attForm = ref({})

function positionNames(g, ids) {
  return (g.positions || []).filter(p => ids.includes(p.id))
    .map(p => p.name).join('、') || '（岗位已删除）'
}

function openAttached(g, s) {
  attDlg.value = { visible: true, editing: s, parent: g, busy: false }
  attForm.value = s ? {
    kind: s.kind, name: s.name, location: s.location, note: s.note || '',
    start_time: new Date(s.start_time.replace(' ', 'T')),
    end_time: new Date(s.end_time.replace(' ', 'T')),
    applies_to_positions: [...s.applies_to_positions],
    hours_mode: s.hours_mode,
  } : { kind: 'rehearsal', name: '', location: '', note: '', start_time: '', end_time: '',
        applies_to_positions: [], hours_mode: 'fixed' }
}

async function saveAttached() {
  const f = attForm.value
  if (!f.name?.trim()) return ElMessage.warning('请填写名称')
  if (!f.start_time || !f.end_time) return ElMessage.warning('请选择起止时间')
  attDlg.value.busy = true
  try {
    const body = {
      name: f.name, kind: f.kind, location: f.location, note: f.note || '',
      parent_id: attDlg.value.parent.id,
      applies_to_positions: f.applies_to_positions,
      hours_mode: f.hours_mode,
      start_time: toISO(f.start_time), end_time: toISO(f.end_time),
    }
    if (attDlg.value.editing) {
      await api.patch(`/api/staff/shifts/${attDlg.value.editing.id}`, body)
    } else {
      await api.post(`/api/staff/activities/${activityId}/shifts`, body)
    }
    ElMessage.success('已保存；已报名的志愿者会自动带上这一场')
    attDlg.value.visible = false
    loadAll()
  } finally { attDlg.value.busy = false }
}

// 时长表里志愿者本人那一格：确认了没、选的志愿时长还是劳务
const VolunteerSide = (props) => {
  const r = props.row
  if (!r.volunteer_confirmed_at) return h('span', { class: 'dim' }, '未表态')
  return h(ElTag, { size: 'small', effect: 'plain',
                    type: r.reward === 'pay' ? 'warning' : 'success' },
           () => `已确认 · ${r.reward_label}`)
}

// —— 急招置顶 ——
const pinBusy = ref(false)
async function togglePin() {
  pinBusy.value = true
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/pin`,
                             { pinned: !act.value.pinned })
    ElMessage.success(d.pinned ? '已置顶，广场上会标「急招」' : '已取消置顶')
    await loadAll()
  } finally { pinBusy.value = false }
}

// —— 活动招募：文案 + 二维码 ——
const shareUrl = computed(() => pageUrl(`/activity/${activityId}`))

async function copyShare() {
  try {
    await navigator.clipboard.writeText(shareUrl.value)
    ElMessage.success('链接已复制')
  } catch {
    ElMessage.warning('浏览器不允许自动复制，请手动选中复制')
  }
}

// —— 招募文案 ——
const posterDlg = ref({ visible: false, text: '' })

async function openPoster() {
  const d = await api.get(`/api/staff/activities/${activityId}/poster`)
  posterDlg.value = { visible: true, text: d.text }
}

async function copyPoster() {
  try {
    await navigator.clipboard.writeText(posterDlg.value.text)
    ElMessage.success('已复制，可以直接粘到群里')
  } catch {
    // 非 https 环境拿不到剪贴板：退回选中文本，让用户自己 Ctrl+C
    document.querySelector('.recruit-text textarea')?.select()
    ElMessage.warning('浏览器不允许自动复制，已全选，请按 Ctrl+C')
  }
}

// assign 场次不能建岗位。点按钮的当下就说清楚，并给一步到位的切换——
// 让人填完一整个表单再被后端 409 拦下，是这里以前最招骂的地方。
async function addPosition(g) {
  if (g.mode === 'assign') {
    const enrolled = g.enrolled || 0
    try {
      await ElMessageBox.confirm(
        `「${g.name}」现在是「截止后统一分配」：报名只锁时段，名额记在场次上（上限 ${g.capacity}），`
        + `岗位要改成「报名即选岗位」之后才能建。\n\n`
        + `切换后名额口径转到各个岗位，`
        + (enrolled ? `已报上名的 ${enrolled} 人不受影响，但都是「待分配」，`
                    + `需要你手动指派或在「报名与调配」里一键分配。` : '本场次目前还没有人报名。'),
        '需要先改岗位模式',
        { type: 'warning', confirmButtonText: '改为「报名即选岗位」并继续',
          cancelButtonText: '取消' })
    } catch { return }
    await api.patch(`/api/staff/shifts/${g.id}`, { mode: 'pick' })
    await loadAll()
    g = shifts.value.find(x => x.id === g.id) || g
    ElMessage.success('已改为「报名即选岗位」，现在可以建岗位了')
  }
  openStation(null, g)
}

const materialMgr = ref(null)

function openStation(st, g) {
  positionDlg.value = { visible: true, editing: st, groupId: g.id, copyFrom: '' }
  stationForm.value = st ? {
    name: st.name, location: st.location, quota: st.quota,
    waitlist_quota: st.waitlist_quota, needs_leader: st.needs_leader,
    attire: st.attire || 'none', attire_note: st.attire_note || '',
    pay_optional: !!st.pay_optional, pay_note: st.pay_note || '',
    group_name: st.group_name || '',
    genderReq: (st.requirement_tags?.性别) || '',
    collegeReq: st.requirement_tags?.学院 || [],
    service_task: st.service_task, position_requirement: st.position_requirement || '',
    training_note: st.training_note,
  } : {
    name: '', location: '', quota: 2, waitlist_quota: 3, needs_leader: false,
    attire: 'none', attire_note: '',
    pay_optional: false, pay_note: '',
    group_name: '',
    genderReq: '', collegeReq: [],
    service_task: '', position_requirement: '', training_note: '',
  }
}
/** 复制一个岗位：把字段预填进「新建岗位」弹窗，改完再保存。
 *
 * 不直接落库生成副本：同场次不允许重名，而且刚复制出来的那份多半要改，
 * 先落库等于在名单上先摆一个半成品。
 */
function copyStation(st, g) {
  openStation(st, g)
  positionDlg.value.editing = null
  positionDlg.value.copyFrom = st.id      // 学习材料由后端跟着带过来
  stationForm.value.name = `${st.name} 副本`
}

async function saveStation() {
  const f = stationForm.value
  if (!f.name?.trim()) return ElMessage.warning('请填写岗位名称')
  if (!f.service_task?.trim()) return ElMessage.warning('请填写服务任务')
  if (f.attire !== 'none' && !f.attire_note?.trim()) {
    return ElMessage.warning('请填写服装备注')
  }
  if (f.pay_optional && !f.pay_note?.trim()) return ElMessage.warning('请填写劳务备注')
  savingStation.value = true
  try {
    const tags = {}
    if (f.genderReq) tags['性别'] = f.genderReq
    if (f.collegeReq?.length) tags['学院'] = f.collegeReq
    const body = {
      name: f.name, quota: f.quota, location: f.location,
      waitlist_quota: f.waitlist_quota, needs_leader: f.needs_leader,
      attire: f.attire, attire_note: f.attire === 'none' ? '' : f.attire_note,
      pay_optional: !!f.pay_optional, pay_note: f.pay_optional ? f.pay_note : '',
      group_name: f.group_name || '',
      requirement_tags: tags, service_task: f.service_task,
      position_requirement: f.position_requirement || '',
      training_note: f.training_note,
    }
    if (positionDlg.value.editing) {
      await api.patch(`/api/staff/positions/${positionDlg.value.editing.id}`, body)
      ElMessage.success('已保存')
      positionDlg.value.visible = false
      await loadAll()
    } else {
      const d = await api.post(
        `/api/staff/shifts/${positionDlg.value.groupId}/positions`,
        { ...body, copy_from: positionDlg.value.copyFrom || '' })
      // 建出岗位后才有 id 可挂，这时把弹窗里攒的材料一并传上去
      const failed = await materialMgr.value?.flush(d.id)
      if (failed) {
        ElMessage.warning(`岗位已创建，但有 ${failed} 个材料没传上，请在编辑里重试`)
      } else {
        ElMessage.success('岗位已创建')
      }
      positionDlg.value.visible = false
      await loadAll()
    }
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
  modes.value = c.recruit_modes
  colleges.value = c.colleges
  campuses.value = c.campuses || []
  stages.value = c.stages || []
  loadAll()
})

// 同一路由换 id 时组件会被复用，不重新拉数据的话页面还停在上一个活动上
watch(() => route.params.id, (id) => {
  if (!id || id === activityId) return
  activityId = id
  notFound.value = false
  loadError.value = ''
  currentGroupId.value = ''
  loadAll()
})

watch(editBase, (v) => {
  if (v && act.value) {
    baseForm.value = activityToForm(act.value)
    baseRange.value = [
      act.value.reg_start ? new Date(act.value.reg_start.replace(' ', 'T')) : '',
      act.value.reg_end ? new Date(act.value.reg_end.replace(' ', 'T')) : '',
    ]
  }
})

async function saveBase(keepOutsiders = false) {
  const err = validateActivity(baseForm.value, baseRange.value)
  if (err) return ElMessage.warning(err)
  savingBase.value = true
  try {
    await api.patch(`/api/staff/activities/${activityId}`,
                    { ...activityBody(baseForm.value, baseRange.value),
                      keep_outsiders: keepOutsiders },
                    { skipErrorToast: true })
    ElMessage.success('已保存，变更将推送提醒志愿者')
    editBase.value = false
    loadAll()
  } catch (e) {
    if (e.data?.code === 'OUTSIDERS_ENROLLED') return askKeepOutsiders(e.data.outsiders)
    ElMessage.error(e.message)
  } finally {
    savingBase.value = false
  }
}

// 缩小招募范围碰到已报名的范围外志愿者：不自动退任何人，让管理者决定
async function askKeepOutsiders(outsiders) {
  const names = outsiders.map(o => `${o.name}（${o.student_id}，${o.reason}）`).join('\n')
  try {
    await ElMessageBox.confirm(
      `以下 ${outsiders.length} 位已报名的志愿者不在新的招募范围内：\n${names}\n\n`
      + '选「仍然修改」会保留他们的报名；或先到「志愿者管理」处理后再改。',
      '有志愿者在范围之外',
      { type: 'warning', confirmButtonText: '仍然修改，保留这些人',
        cancelButtonText: '先去名单页处理', customStyle: { whiteSpace: 'pre-line' } })
  } catch { tab.value = 'roster'; return }
  await saveBase(true)
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

// —— 导出 ——
// 排班表：按场次分 Sheet，含签到状态，交付现场；报名问卷有文件答案时是 zip
function doExport() { dl(`/api/staff/activities/${activityId}/export.xlsx`) }
// 时长记录：姓名、身份证号、岗位、时长、备注
function doExportHours() { dl(`/api/staff/activities/${activityId}/hours.xlsx`) }

// —— 提交审批 ——
const approval = ref(null)
const depts = ref([])
const submitDlg = ref({ visible: false, deptId: '', note: '', busy: false })

async function loadApproval() {
  approval.value = await api.get(
    `/api/staff/activities/${activityId}/approval`, { silent: true })
}

async function openSubmit() {
  // 先验结构再开窗：让人选完部门、写完备注才被告知「场次没岗位」，等于白填
  try {
    await api.get(`/api/staff/activities/${activityId}/submit-check`)
  } catch { return }        // 错误提示由 api 层统一弹出
  depts.value = await api.get('/api/staff/departments')
  // 青协的人建的活动已经按所在部门预填了归口，这里带出来，可以改
  submitDlg.value = { visible: true, deptId: act.value?.approval_dept_id || '',
                      note: '', autoPublish: true, busy: false }
}

async function doSubmit() {
  const d = submitDlg.value
  if (!d.deptId) return ElMessage.warning('请选择审批部门')
  d.busy = true
  try {
    const r = await api.post(`/api/staff/activities/${activityId}/submit`,
                             { department_id: d.deptId, note: d.note,
                               auto_publish: d.autoPublish })
    ElMessage.success(`已提交，等待 ${r.assignee} 审批`)
    d.visible = false
    loadAll()
  } finally { d.busy = false }
}

// —— 恢复报名 ——
const reopenDlg = ref({ visible: false, regEnd: '', busy: false })
const openReopen = () => { reopenDlg.value = { visible: true, regEnd: '', busy: false } }

async function doReopen() {
  const d = reopenDlg.value
  if (!d.regEnd) return ElMessage.warning('请选择新的报名截止时间')
  d.busy = true
  try {
    const r = await api.post(`/api/staff/activities/${activityId}/reopen`,
                             { reg_end: toISO(d.regEnd) })
    ElMessage.success('已恢复报名'
      + (r.promoted ? `；已通知 ${r.promoted} 位候补确认转正` : ''))
    d.visible = false
    loadAll()
  } finally { d.busy = false }
}

// —— 时长申诉 ——
const appeals = ref([])
const pendingAppeals = computed(
  () => appeals.value.filter(a => a.status === 'pending').length)
const resolveDlg = ref({ visible: false, row: null, accept: true,
                         hours: 1, reply: '', busy: false })

async function loadAppeals() {
  appeals.value = await api.get(
    `/api/staff/activities/${activityId}/hours-appeals`)
}

function openResolve(row, accept) {
  resolveDlg.value = { visible: true, row, accept, hours: row.claimed_hours,
                       reply: '', busy: false }
}

async function doResolve() {
  const d = resolveDlg.value
  d.busy = true
  try {
    await api.post(`/api/staff/hours-appeals/${d.row.id}/resolve`,
      { accept: d.accept, hours: d.accept ? d.hours : null, reply: d.reply })
    ElMessage.success(d.accept ? '已采纳，时长已更新' : '已答复本人')
    d.visible = false
    loadAppeals()
    hoursPreview(true)
  } finally { d.busy = false }
}

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
    hoursState.value = d
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
    ElMessage.success(`已报名 ${d.count} 名志愿者的时长`)
    hoursPreview(true)
  } catch { /* toast by api */ }
}

// 确认全部志愿者时长
async function confirmAllHours() {
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/hours/confirm`, {})
    ElMessage.success(d.message || `已报名 ${d.count} 名志愿者的时长`)
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

async function delManualHours(row) {
  await api.del(`/api/staff/activities/${activityId}/hours/manual/${row.record_id}`)
  ElMessage.success('已删除')
  hoursPreview(true)
}

// 核准：定下这场活动的时长，通知每个人，并给一段申诉时间
function openApprove() {
  approveDlg.value = {
    visible: true, busy: false,
    deadline: new Date(hoursState.value.default_deadline.replace(' ', 'T')),
  }
}
async function doApprove() {
  const d = approveDlg.value
  if (!d.deadline) return ElMessage.warning('请选择申诉截止时间')
  d.busy = true
  try {
    const r = await api.post(`/api/staff/activities/${activityId}/hours/approve`,
                             { appeal_deadline: toISO(d.deadline) })
    ElMessage.success(r.message)
    d.visible = false
    hoursPreview(true)
    loadAll()
  } finally { d.busy = false }
}

// 时长要由校青协审核过才算数：提交给归口部长，通过之后才锁定
async function submitLockNow() {
  const pending = hoursState.value.pending_appeals
  await ElMessageBox.confirm(
    (pending ? `还有 ${pending} 条申诉没处理。` : '')
    + '提交后时长冻结、不能再改，等校青协审核；通过即锁定并把活动标记为「已完成」，'
    + '驳回则退回你修改。确定提交吗？',
    '提交时长审核', { type: 'warning' })
  await api.post(`/api/staff/activities/${activityId}/hours/lock-request`, {})
  ElMessage.success('已提交，等校青协审核')
  hoursPreview(true)
  loadAll()
}

// 锁定之后要改，报归口部长审批
async function openUnlock() {
  const { value } = await ElMessageBox.prompt(
    '写清要改什么、为什么改，归口部长看到的就是这句话。', '申请解锁时长',
    { inputType: 'textarea', inputPlaceholder: '如：漏算了两位同学的加班时长',
      inputValidator: v => (v || '').trim().length >= 5 || '至少 5 个字' })
  await api.post(`/api/staff/activities/${activityId}/hours/unlock-request`,
                 { reason: value.trim() })
  ElMessage.success('已提交，等部长审批')
  hoursPreview(true)
}

// 时长是不是被冻着：锁定后不能改，报了审核等结果的时候也不能改
const hoursFrozen = computed(
  () => !!hoursState.value.locked_at || !!hoursState.value.lock_pending)

function loadRoster() {
  const g = currentShift.value
  if (!g?._roster) return
  roster.value = g._roster
  moveSel.value = {}
}

/** 重新拉当前场次的名单。loadRoster 只是把缓存搬到视图上，不发请求。 */
async function refreshRoster() {
  const g = currentShift.value
  if (!g) return
  g._roster = await api.get(`/api/staff/shifts/${g.id}/roster`)
  loadRoster()
}

// 只有「岗位设了组长」的人才能被指定；没岗位的人后端也会拒
function canBeLeader(row) {
  const p = roster.value?.positions?.find(x => x.id === row.position_id)
  return !!p?.needs_leader
}

async function releaseSeat(row) {
  const d = await api.post(
    `/api/staff/enrollments/${row.enrollment_id}/release-seat`)
  ElMessage.success(d.message)
  loadAll()
}

const assignPanel = ref(null)
const archiveDrawer = ref(null)
const violationDlg = ref(null)
// 只看得到报了本活动的人的档案；接口会按活动校验
function openArchive(u) {
  archiveDrawer.value.open(`/api/staff/activities/${activityId}/volunteers/${u.id}/archive`)
}

async function setLeader(row, on) {
  const d = await api.post(`/api/staff/enrollments/${row.enrollment_id}/leader`,
                           { is_leader: on })
  ElMessage.success(on ? `已指定 ${row.user.name} 为组长`
                       : `已取消 ${row.user.name} 的组长身份`)
  loadAll()
  return d
}

// 自动递补停了之后手动补人；名额不够时后端会拦，提示去调岗位名额
async function promoteManually(w) {
  const d = await api.post(`/api/staff/enrollments/${w.enrollment_id}/promote`)
  ElMessage.success(`已通知 ${w.user.name} 确认，${fmtDT(d.confirm_deadline)} 前有效`)
  loadAll()
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

const dutyOptions = ref({})

async function loadManagers() {
  const d = await api.get(`/api/staff/activities/${activityId}/managers`)
  managers.value = d.items
  dutyOptions.value = d.duties
}

// 分工只影响通知发给谁，改了立刻生效，不用另点保存
async function saveDuties(row, duties) {
  row.duties = duties
  await api.patch(
    `/api/staff/activities/${activityId}/managers/${row.id}/duties`, { duties })
  ElMessage.success(duties.length ? '已更新通知分工' : '已改为接收全部通知')
}

async function addManager() {
  try {
    await api.post(`/api/staff/activities/${activityId}/managers`,
                   { student_id: newMgrSid.value })
    ElMessage.success('已添加')
    newMgrSid.value = ''
    await loadManagers()
  } catch { /* toast by api */ }
}
async function removeManager(row) {
  try {
    await api.del(`/api/staff/activities/${activityId}/managers/${row.id}`)
    ElMessage.success('已移除')
    await loadManagers()
  } catch { /* toast by api */ }
}
// 切到「时长管理」页签时自动加载时长预览（静默加载，不弹提示）。
// ?tab=hours 直达时 tab 一开始就是它，这个 watch 不会触发——那一路由
// loadAll 负责补上。
watch(tab, async t => {
  if (t === 'hours') { hoursPreview(true); loadAppeals() }
  if (t === 'acks') {
    await loadBatches()
    await nextTick()                 // lazy 页签这一轮才渲染，ref 要等一帧
    ackRefresh.value?.markLoaded()   // 首次由这里加载，时刻也要记上
  }
})
watch(currentGroupId, loadRoster)
watch(() => shifts.value.map(g => g.enrolled).join(','), () => {
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
/* 名单标题行：左边标题，右边一排动作；窄屏各占一行 */
.roster-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.roster-actions { display: inline-flex; gap: 10px; align-items: center; flex-wrap: wrap; }
/* 两个条件选择框并排占满一行；窄屏自动叠成两行 */
.two-col { display: flex; gap: 8px; width: 100%; flex-wrap: wrap; }
.two-col > * { flex: 1 1 180px; min-width: 0; }
/* 招募弹窗：宽屏文案与二维码并排，窄屏竖排 */
.recruit { display: flex; gap: 18px; align-items: flex-start; }
.recruit-text { flex: 1; min-width: 0; }
.recruit-qr { width: 210px; flex-shrink: 0; text-align: center; }
.recruit-qr img { border: 1px solid #eceef1; border-radius: 8px; }
@media (max-width: 860px) {
  .recruit { flex-direction: column; }
  .recruit-qr { width: 100%; }
}

/* 页头：宽屏一行排开，窄屏由 styles.css 的媒体查询拆成多行 */
.hd-title { display: inline-flex; align-items: center; gap: 8px; vertical-align: middle; }
.hd-meta { display: inline; margin-left: 10px; }
@media (max-width: 860px) {
  .hd-title { display: flex; flex-wrap: wrap; font-size: 16px; }
  .hd-meta { display: block; margin: 4px 0 0; }
}

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
