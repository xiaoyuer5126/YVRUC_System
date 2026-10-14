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
      <!-- 标题与副信息各占一行：一行挤不下活动名+状态+时间地点 -->
      <template #content>
        <div class="hd-title">
          <b>{{ act.title }}</b>
          <el-tag size="small" :type="{published:'success',closed:'info',draft:'info',pending:'warning',approved:'primary',finished:'info',vb_pending:'warning',completed:'success'}[act.status]">
            {{ act.status_label }}
          </el-tag>
          <el-tag v-if="act.urgent" size="small" type="danger" effect="dark">急招</el-tag>
          <el-tag v-if="isRecord" size="small" type="info" effect="plain">不公开·补记录</el-tag>
        </div>
        <div class="hd-meta dim">{{ act.time_text }} · {{ act.location || '待定' }}</div>
      </template>
      <template #extra>
        <!-- 「调整岗位」只在岗位设置那一页出现一次，页头不重复放 -->
        <el-button v-if="!isRecord" @click="openPoster">活动招募</el-button>
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
        <!-- 审批人自己打开：直接去审批页办，别的管理者只看到在等谁 -->
        <el-button v-else-if="act.status === 'pending' && approval?.can_decide" type="warning"
                   @click="$router.push({ path: '/approvals', query: { activity: act.id } })">
          去审批
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
              :title="`已提交${approval?.department || ''}审批，等待 ${approval?.on_duty
                ? approval.on_duty + '（值班）' : (approval?.assignee || '')} 处理`"
              :description="isRecord
                ? '补记录活动不公开发布：审批通过后在「名单导入」里导入学号与时长。'
                : act.auto_publish
                  ? '审批期间可以继续调整安排，审批通过后活动立即公开发布，到点准时开始报名。'
                  : '审批期间可以继续调整安排，审批通过后由你点「发布」。'" />
    <el-alert v-else-if="act.status === 'approved'" type="success" show-icon :closable="false" title="审批已通过，可以发布了"
              :description="approval?.comment ? ('审批意见：' + approval.comment) : ''" />
    <el-alert v-else-if="act.status === 'draft' && approval?.comment" type="error" show-icon
              :closable="false" title="审批未通过"
              :description="'意见：' + approval.comment + '；修改后可重新提交。'" />

    <el-alert v-if="act.editing" type="warning" :closable="false" show-icon
              title="正在调整岗位，学生已暂停报名与退选"
              :description="`改完点页面顶部的「保存并发布」恢复报名并通知志愿者；${lockHours} 小时未保存会自动退出编辑。`" />

    <el-tabs v-model="tab" style="margin-top:8px">
      <!-- ============ 概览：发布之后才有报名、签到、时长可看 ============ -->
      <el-tab-pane v-if="isPublic" label="概览" name="overview" lazy>
        <ActivityOverviewCard ref="overviewCard" :activity-id="activityId" />
      </el-tab-pane>

      <!-- ============ 岗位设置 ============ -->
      <el-tab-pane v-if="!isRecord" name="tree">
        <template #label>岗位设置
          <el-badge v-if="treeAlerts.length" value="!" type="danger" />
        </template>
        <el-alert v-if="act.recruit_mode === 'screened'" type="info" :closable="false">
          报名后筛选：志愿者先勾意向岗位、再按岗位填报名问卷，报名截止后你在
          「志愿者管理」里录取并定岗位。没建岗位的场次，志愿者勾的是「待分配」，
          录取之后再排。
        </el-alert>
        <el-alert v-else type="info" :closable="false">
          先按照时间设置场次，再分配每场的具体岗位。
          <b>报名即选岗位</b>：发布前分配好各岗位名额。
          <b>截止后统一分配</b>：适用于未确定岗位的场次，志愿者先选择时间，后续分配岗位。
        </el-alert>
        <div class="toolbar">
          <el-button plain @click="formDlg = true">{{ formLabel }}</el-button>
          <!-- 筛选活动没有问卷也能发，但筛的时候只剩档案可看：提醒，不拦 -->
          <el-text v-if="screened && !formCount && !isPublic" type="warning" size="small">
            报名后筛选的活动建议设置报名问卷，不然筛选时只能看志愿者档案
          </el-text>
          <span v-else class="dim">
            {{ screened ? '志愿者申请时填写' : '选填，志愿者报名时填写' }}
          </span>
        </div>
        <!-- 结束之后是终态：时长按当时的岗位与名单结的账，再改就对不上，
             连「调整岗位」的入口都不给 -->
        <el-alert v-if="isOver" type="info" show-icon :closable="false"
                  :title="`活动${act.status_label}，安排与信息均为只读`">
          <div style="line-height:1.6">
            时长已由活动管理者核准，不再接受修改，如确需更正请联系会长团。
          </div>
        </el-alert>
        <el-alert v-else-if="structureLocked" type="info" show-icon :closable="false">
          <template #title>
            当前只读——改场次与岗位要先点击右侧按钮进入调整状态，期间将暂停报名
            <el-button type="warning" size="small" style="margin-left:12px"
                       @click="startEditing">{{ editLabel }}</el-button>
          </template>
        </el-alert>

        <el-alert v-if="treeAlerts.length" type="error" show-icon
                  :closable="false"
                  :title="`有 ${treeAlerts.length} 个场次还没排好`">
          <div v-for="a in treeAlerts" :key="a.id" style="line-height:1.6">
            <b>{{ a.name }}</b>：{{ a.text }}
          </div>
        </el-alert>

        <div class="toolbar">
          <el-radio-group v-model="treeView" size="small">
            <el-radio-button value="shift">按场次看</el-radio-button>
            <el-radio-button value="role">按岗位看</el-radio-button>
          </el-radio-group>
          <span class="dim">{{ treeView === 'role'
            ? '同名岗位合成一张卡，列出它的各个时段；各岗位时间不同的活动看这个更直观'
            : '每个场次一张卡，列出这段时间里的各个岗位' }}</span>
        </div>
        <template v-if="treeView === 'shift'">
        <div v-for="g in shifts" :key="g.id" class="group-card"
             :class="{ 'needs-positions': !g.positions.length }">
          <div class="g-head">
            <el-link type="primary" underline="never" style="font-weight:600;font-size:14px"
                     @click="showGroupDetail(g)">{{ g.name }}</el-link>
            <el-tag size="small" effect="plain"
                    :type="g.mode === 'assign' ? 'warning' : 'info'">
              {{ g.mode_label }}
            </el-tag>
            <span class="dim">{{ g.time_text }}</span>
            <span>{{ g.mode === 'assign' ? `上限 ${g.capacity}（待分配）` : `本场合计 ${g.capacity} 人` }}</span>
            <el-tag v-if="!realPositions(g).length"
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
            <el-tag v-if="g.enroll_paused" size="small" type="danger" effect="plain">
              暂停报名
            </el-tag>
            <el-tag v-if="g.locked" size="small" type="info">名单已锁定</el-tag>
            <span v-else-if="g.lock_hours" class="dim">开场前 {{ g.lock_hours }} 小时锁定名单</span>
            <span class="btn-row" style="margin-left:auto">
              <!-- 暂停、锁定都不用进编辑态：只管收不收新报名、候补顶不顶上，不改任何人的安排 -->
              <el-button v-if="isPublic" size="small" :disabled="isOver"
                         @click="togglePause('shifts', g)">
                {{ g.enroll_paused ? '恢复报名' : '暂停报名' }}
              </el-button>
              <el-button v-if="isPublic" size="small" :disabled="isOver"
                         @click="toggleLock('shifts', g, !g.locked)">
                {{ g.locked ? '解锁名单' : '锁定名单' }}
              </el-button>
              <!-- 开放选岗同样不用进编辑态：已报名的人都已在真实岗位上，安排不变 -->
              <el-tooltip v-if="g.mode === 'assign'" placement="top"
                          :disabled="!openPickBlock(g)" :content="openPickBlock(g)">
                <span>
                  <el-popconfirm :width="320" :disabled="!!openPickBlock(g)"
                                 :title="`「${g.name}」改为报名即选岗位：「待分配」删除，之后新报名的人按各岗位剩余名额自己选岗，已分好岗位的人不动。`"
                                 @confirm="openPick(g)">
                    <template #reference>
                      <el-button size="small" type="warning" plain
                                 :disabled="!!openPickBlock(g)">开放选岗</el-button>
                    </template>
                  </el-popconfirm>
                </span>
              </el-tooltip>
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
              <el-tag v-if="row.enroll_paused" size="small" type="danger" effect="plain">暂停报名</el-tag>
              <el-tag v-if="row.list_locked" size="small" type="info">名单已锁定</el-tag>
            </template>
            <template #actions="{ row }">
              <el-button v-if="isPublic && g.mode !== 'assign'" size="small" plain
                         :disabled="isOver" @click="togglePause('positions', row)">
                {{ row.enroll_paused ? '恢复报名' : '暂停' }}
              </el-button>
              <el-button v-if="isPublic && g.mode !== 'assign'" size="small" plain
                         :disabled="isOver || g.locked"
                         @click="toggleLock('positions', row, !row.list_locked)">
                {{ row.list_locked ? '解锁' : '锁定' }}
              </el-button>
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
                        style="margin-left:8px">默认</el-tag>
                <el-tag v-if="row.list_locked" size="small" type="info"
                        style="margin-left:8px">名单已锁定</el-tag>
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
            <el-table-column label="操作" width="250">
              <template #default="{row}">
                <div class="btn-row">
                  <el-button v-if="isPublic && g.mode !== 'assign'" size="small" link
                             :type="row.enroll_paused ? 'danger' : ''" :disabled="isOver"
                             @click="togglePause('positions', row)">
                    {{ row.enroll_paused ? '恢复报名' : '暂停' }}
                  </el-button>
                  <el-button v-if="isPublic && g.mode !== 'assign'" size="small" link
                             :disabled="isOver || g.locked"
                             @click="toggleLock('positions', row, !row.list_locked)">
                    {{ row.list_locked ? '解锁' : '锁定' }}
                  </el-button>
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
                </div>
              </template>
            </el-table-column>
          </el-table>
          <p v-else class="dim" style="margin:8px 0 0">
            {{ g.mode === 'assign'
               ? '截止后统一分配：学生报名只选时间；分工定下来后直接在本场次下建岗位，再到「报名与调配」一键分配或导入排班；排完后可点「开放选岗」，剩余名额让大家按岗位自选。'
               : '还没有岗位，请先设置岗位及名额。' }}
          </p>

        </div>
        </template>
        <!-- 按岗位看：同名岗位合成一张卡，列它的各个时段。数据还是「场次下挂岗位」，
             只是换个排法；加时段 = 在某个场次下复制出同名岗位 -->
        <template v-else>
          <div v-for="r in roles" :key="r.name" class="group-card">
            <div class="g-head">
              <b style="font-size:14px">{{ r.name }}</b>
              <span class="dim">{{ r.slots.length }} 个时段 · 共 {{ r.quota }} 人</span>
              <el-tag v-if="r.needs_leader" size="small" type="warning" effect="plain">设组长</el-tag>
              <span class="btn-row" style="margin-left:auto">
                <el-button size="small" :disabled="structureLocked"
                           @click="editRole(r)">改岗位信息</el-button>
                <el-button size="small" type="primary" plain :disabled="structureLocked"
                           @click="openAddSlot(r)">加时段</el-button>
                <el-button size="small" plain :disabled="structureLocked"
                           @click="openAttached(null, r.slots.map(x => x.p.id))">加培训</el-button>
              </span>
            </div>
            <div class="dim pre">{{ r.slots[0].p.service_task }}</div>
            <div v-for="x in r.slots" :key="x.p.id" class="slot-row">
              <span class="slot-time">{{ x.g.name }} · {{ x.g.time_text }}</span>
              <span>{{ x.p.enrolled }}/{{ x.p.quota }}</span>
              <el-tag v-if="x.g.mode === 'assign'" size="small" type="warning" effect="plain">
                截止后分配
              </el-tag>
              <el-tag v-if="x.g.enroll_paused || x.p.enroll_paused" size="small" type="danger"
                      effect="plain">暂停报名</el-tag>
              <el-tag v-if="x.g.locked || x.p.list_locked" size="small" type="info">名单已锁定</el-tag>
              <span class="slot-acts">
                <el-button v-if="isPublic && x.g.mode !== 'assign'" size="small" link
                           :disabled="isOver" @click="togglePause('positions', x.p)">
                  {{ x.p.enroll_paused ? '恢复报名' : '暂停' }}
                </el-button>
                <el-button v-if="isPublic && x.g.mode !== 'assign'" size="small" link
                           :disabled="isOver || x.g.locked"
                           @click="toggleLock('positions', x.p, !x.p.list_locked)">
                  {{ x.p.list_locked ? '解锁' : '锁定' }}
                </el-button>
                <el-button size="small" link type="primary" :disabled="structureLocked"
                           @click="openStation(x.p, x.g)">编辑这个时段</el-button>
                <el-popconfirm title="删除这个时段的岗位？" @confirm="delStation(x.p)"
                               :disabled="structureLocked">
                  <template #reference>
                    <el-button size="small" link type="danger"
                               :disabled="structureLocked">删除</el-button>
                  </template>
                </el-popconfirm>
              </span>
            </div>
            <div v-if="trainingsOf(r).length" class="dim">
              培训/彩排：{{ trainingsOf(r).map(t => `${t.kind_label}「${t.name}」`).join('、') }}
            </div>
          </div>
          <!-- 截止后统一分配的场次：报名只锁时段，「待分配」是报名入口，不归到哪个岗位下 -->
          <div v-for="g in shifts.filter(x => x.mode === 'assign')" :key="g.id" class="group-card">
            <div class="g-head">
              <b style="font-size:14px">{{ g.name }}</b>
              <el-tag size="small" type="warning" effect="plain">截止后统一分配</el-tag>
              <span class="dim">{{ g.time_text }} · 上限 {{ g.capacity }}</span>
              <el-tag v-if="g.unassigned_count" size="small" type="warning">
                {{ g.unassigned_count }} 人待分配
              </el-tag>
              <el-tag v-if="g.enroll_paused" size="small" type="danger" effect="plain">暂停报名</el-tag>
              <el-tag v-if="g.locked" size="small" type="info">名单已锁定</el-tag>
              <span class="btn-row" style="margin-left:auto">
                <el-button v-if="isPublic" size="small" :disabled="isOver"
                           @click="togglePause('shifts', g)">
                  {{ g.enroll_paused ? '恢复报名' : '暂停报名' }}
                </el-button>
                <el-button v-if="isPublic" size="small" :disabled="isOver"
                           @click="toggleLock('shifts', g, !g.locked)">
                  {{ g.locked ? '解锁名单' : '锁定名单' }}
                </el-button>
                <el-button size="small" :disabled="structureLocked"
                           @click="openShift(g)">编辑场次</el-button>
                <el-button size="small" type="primary" plain :disabled="structureLocked"
                           @click="openStation(null, g)">添加岗位</el-button>
              </span>
            </div>
          </div>
          <el-empty v-if="!roles.length && !shifts.length" description="还没有场次" :image-size="60" />
        </template>
        <el-button style="width:100%" plain dashed
                   :disabled="structureLocked" @click="openShift(null)">
          <el-icon><Plus /></el-icon>&nbsp;新建场次
        </el-button>

        <!-- 培训与彩排：挂在整个活动上，按岗位圈人（可以跨场次），空 = 全体 -->
        <div class="section-title">
          培训与彩排
          <span class="dim">报上被圈到的岗位就连带报上；有独立的签到码与时长</span>
        </div>
        <div v-for="t in act.trainings || []" :key="t.id" class="att-row">
          <el-tag size="small" effect="dark" :type="t.kind === 'training' ? 'success' : 'warning'">
            {{ t.kind_label }}
          </el-tag>
          <b>{{ t.name }}</b>
          <span class="dim">{{ t.time_text }} · {{ t.location || act.location || '地点待定' }}</span>
          <span class="dim">{{ t.hours_label }}</span>
          <el-tag size="small" effect="plain">{{ t.applies_text }}参加</el-tag>
          <span class="dim">已带上 {{ t.enrolled }} 人</span>
          <span v-if="t.note" class="dim">备注：{{ t.note }}</span>
          <span class="btn-row" style="margin-left:auto">
            <el-button size="small" link type="primary" :disabled="structureLocked"
                       @click="openAttached(t)">编辑</el-button>
            <el-popconfirm :title="`删除「${t.name}」？相关签到记录会同时清除`"
                           @confirm="delShift(t)" :disabled="structureLocked">
              <template #reference>
                <el-button size="small" link type="danger" :disabled="structureLocked">删除</el-button>
              </template>
            </el-popconfirm>
          </span>
        </div>
        <el-button size="small" text type="primary" :disabled="structureLocked"
                   @click="openAttached(null)">
          <el-icon><Plus /></el-icon>&nbsp;添加培训 / 彩排
        </el-button>
      </el-tab-pane>

      <!-- ============ 名单导入（补记录活动）============ -->
      <!-- 补记录活动没有场次与报名：志愿者名单由管理者导进来，每行就是一笔时长 -->
      <el-tab-pane v-if="isRecord" label="名单导入" name="records">
        <RecordRosterPanel :activity-id="activityId" @changed="onRecordChanged" />
      </el-tab-pane>

      <!-- ============ 报名与调配 ============ -->
      <el-tab-pane v-if="isPublic && !isRecord" name="roster">
        <template #label>志愿者管理
          <el-badge v-if="promoShiftIds.size" value="!" type="danger" />
        </template>

        <!-- 场次选择栏 -->
        <div class="toolbar">
          <span>场次</span>
          <el-select v-model="currentGroupId" style="width:260px">
            <el-option v-for="g in shifts" :key="g.id"
                       :value="g.id"
                       :label="`${g.name}（${g.enrolled}/${g.capacity}）${promoShiftIds.has(g.id) ? '· 有转正待确认' : ''}`" />
          </el-select>
          <el-button type="primary" plain @click="openNotify">
            <el-icon><Bell /></el-icon>&nbsp;发送通知
          </el-button>
          <!-- 用主题色（人大红）而不是 danger：平台把 primary 覆盖成了 #ae0b2a，
               danger 那个亮红在这一屏里显得突兀 -->
          <el-button v-if="formCount" type="primary" plain @click="openAnswersExport"
                     style="margin-left:auto">
            <el-icon><Download /></el-icon>&nbsp;导出问卷结果
          </el-button>
          <el-button type="primary" @click="doExport"
                     :style="formCount ? '' : 'margin-left:auto'">
            <el-icon><Download /></el-icon>&nbsp;导出排班表
          </el-button>
        </div>

        <!-- 报名后筛选：先录取，录取的人才进下面的名单 -->
        <ApplicationList v-if="act.recruit_mode === 'screened'" :activity-id="act.id"
                         style="margin-bottom:12px"
                         @archive="openArchive" @changed="loadAll" />

        <template v-if="currentShift">
          <!-- 已报名名单：满宽；动作按钮都在这一行 -->
          <div class="section-title roster-head" style="margin-top:0">
            <span>已报名名单（{{ roster.enrolled.length }} 人）</span>
            <span class="roster-actions">
              <el-button type="primary" :loading="assignPanel?.solving"
                         :disabled="isOver" @click="assignPanel.runPreview()">
                <el-icon><MagicStick /></el-icon>&nbsp;一键分配岗位
              </el-button>
              <el-button plain :disabled="isOver" @click="assignPanel.openImport()">
                导入排班
              </el-button>
              <RefreshBar :load="refreshRoster" :at="rosterAt" />
              <el-button type="success" plain :disabled="hoursFrozen"
                         @click="assignPanel.openWalkin()">补招志愿者</el-button>
            </span>
          </div>
          <DataCards v-if="narrow" :rows="roster.enrolled" title-key="user.name"
                     row-key="enrollment_id" style="margin-bottom:12px"
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
              <el-tag v-if="row.source === 'party_school'" size="small" effect="plain">党校</el-tag>
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
          <el-table v-else :data="roster.enrolled" size="small" max-height="300" style="margin-bottom:12px">
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
            <el-tag v-if="row.source === 'party_school'" size="small" effect="plain"
                    style="margin-left:4px">党校</el-tag>
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

          <el-row :gutter="16" style="margin-top:12px">
            <!-- 左栏（宽）：分配程序计算结果、人员变动 -->
            <el-col :xs="24" :md="16">
              <div class="section-title" style="margin-top:0">分配方案</div>
              <AssignPanel ref="assignPanel" :shift="currentShift" @reload="loadAll" />
            <!-- 人员变动流水 -->
            <template v-if="roster.roster_changes?.length">
              <div class="section-title">人员变动</div>
              <el-timeline>
                <el-timeline-item v-for="(c, i) in roster.roster_changes" :key="i"
                                  :timestamp="fmtDT(c.created_at)" placement="top"
                                  :type="['cancel','abandon','expire'].includes(c.action)
                                         ? 'danger' : 'primary'">
                  <el-tag size="small" effect="plain"
                          :type="['cancel','abandon','expire'].includes(c.action)
                                 ? 'danger' : 'success'">
                    {{ c.action_label }}
                  </el-tag>
                  <span style="margin-left:8px">{{ c.summary }}</span>
                </el-timeline-item>
              </el-timeline>
            </template>
            </el-col>

            <!-- 右栏（窄）：候补队列、退选记录，块与块之间统一 12px -->
            <el-col :xs="24" :md="8" class="side-stack">
            <el-card shadow="never" class="wl-card">
              <template #header><b>候补队列</b></template>
              <el-alert v-if="roster.auto_promote_stop" type="warning" show-icon
                        :closable="false" :title="roster.auto_promote_stop" />
              <div v-for="w in roster.waitlist" :key="w.enrollment_id" class="wl-row">
                <el-tag size="small" circle>{{ w.queue_position }}</el-tag>
                {{ w.user.name }}
                <span class="dim">{{ w.user.student_id }}</span>
                <el-tag v-if="w.position_name" size="small" effect="plain">
                  {{ w.position_name }}
                </el-tag>
                <el-tag v-if="w.status === 'pending_confirm'" size="small" type="danger">
                  待确认 {{ fmtDT(w.confirm_deadline) }} 前
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
              <el-empty v-if="!roster.waitlist.length" description="暂无候补" />
            </el-card>
            <el-card shadow="never" class="wl-card" v-if="roster.cancelled.length">
              <template #header><b>退选记录（近20条）</b></template>
              <div v-for="x in roster.cancelled" :key="x.enrollment_id" class="wl-row dim">
                {{ x.user.name }}
                <el-tag size="small" :type="x.cancel_type === 'locked' ? 'warning' : 'info'"
                        effect="plain">{{ cancelType(x) }}</el-tag>
              </div>
            </el-card>
            </el-col>
          </el-row>
        </template>
      </el-tab-pane>

      <!-- ============ 签到管理 ============ -->
      <el-tab-pane v-if="isPublic && !isRecord" label="签到管理" name="checkin" lazy>
        <!-- 场次由面板自己选：这一页在盯现场，跟「报名与调配」那边排人是两件事 -->
        <CheckinPanel v-if="allShifts.length" :activity-id="act.id"
                      :all-shifts="allShifts" :over="isOver"
                      :hours-frozen="hoursFrozen" />
        <el-empty v-else description="请先创建场次" />
      </el-tab-pane>

      <!-- ============ 志愿服装 ============ -->
      <el-tab-pane v-if="isPublic && !isRecord && act.attire_issued" label="志愿服装" name="attire" lazy>
        <AttirePanel :activity-id="act.id" :title="act.title"
                     :all-shifts="allShifts" />
      </el-tab-pane>

      <!-- ============ 时长管理 ============ -->
      <el-tab-pane v-if="isPublic" name="hours" lazy>
        <template #label>时长管理
          <el-badge v-if="pendingAppeals" :value="pendingAppeals" type="danger" />
        </template>
        <div class="toolbar">
          <!-- 核准之后志愿者陆续确认、提申诉，这一页不刷新看到的就是旧的 -->
          <RefreshBar :load="refreshHours" :at="hoursAt" />
          <el-button type="primary" :disabled="!confirmedCount" @click="doExportHours">
            <el-icon><Download /></el-icon>&nbsp;导出时长记录
          </el-button>
          <!-- 说明文字要能换行：窄屏下 toolbar 的子项不收缩，不给宽度就会被裁掉 -->
          <span class="dim" style="flex:1 1 260px">用于志愿北京时长录入，选择劳务的人和党校同学不在其中，包含身份证号，下载留痕</span>
        </div>

        <!-- 时长申诉：出了岔子的那一条才走这里，不是全员自报 -->
        <template v-if="appeals.length">
          <el-divider />
          <div class="toolbar">
            <b>时长申诉</b>
          </div>
          <el-table :data="appeals" size="small" style="margin-top:8px">
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
                  <div class="btn-row">
                    <el-button size="small" type="primary" plain :disabled="hoursFrozen"
                               @click="openResolve(row, true)">采纳</el-button>
                    <el-button size="small" plain :disabled="hoursFrozen"
                               @click="openResolve(row, false)">不采纳</el-button>
                  </div>
                </template>
                <span v-else class="dim pre">{{ row.reply || '—' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </template>
        <el-divider />
        <div class="toolbar">
          <span v-if="isRecord" class="dim" style="margin-left:auto">
            时长以「名单导入」为准，改人改数请到那一页
          </span>
          <el-button v-if="!isRecord" type="primary" plain
                     :disabled="hoursFrozen || !hoursSel.length"
                     @click="confirmSelectedHours" style="margin-left:auto">
            {{ hoursSel.length ? `确认选中时长（${hoursSel.length} 人）` : '确认选中时长' }}
          </el-button>
          <el-tooltip v-if="!isRecord" content="确认正常签退的记录；未签退/自动兜底请逐条核对后手动确认" placement="top">
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
          <!-- 对外活动凭盖章材料报批：缺了先说，别等点了才被拦 -->
          <el-alert v-if="hoursState.stamped_missing?.length && !hoursState.locked_at"
                    type="warning" :closable="false" show-icon style="flex:1 1 100%"
                    :title="`对外联络部归口的活动须先上传${hoursState.stamped_missing.join('和')}，才能提交时长审核（到点也不会自动提交）。点「提交时长审核」或到「审核」页签上传`" />
          <el-button v-if="hoursState.locked_at" type="warning" plain
                     @click="openUnlock">申请解锁</el-button>
        </div>

        <!-- 核准之后这一段就是这场活动的账本状态 -->
        <el-alert v-if="hoursState.locked_at" type="success" show-icon :closable="false"
                  :title="`时长已于 ${fmtDT(hoursState.locked_at)} 锁定，${act.status === 'vb_pending' ? '待外联确认' : '活动已完成'}`">
          <div style="line-height:1.6">
            已通过校青协审核，时长不再改动。确需更正请点「申请解锁」，同样由校青协审批
            <template v-if="act.status === 'vb_pending'">
              <br />志愿北京活动名称已推给对外联络部，外联在志愿北京核对确认后活动标记为「已完成」。
              <template v-if="hoursState.vb_confirm?.status === 'returned'">
                <br /><b class="pre">外联退回了名称：{{ hoursState.vb_confirm.reply }}</b>——请到
                <el-link type="primary" @click="tab = 'review'">「审核」页签</el-link>修改后重新提交。
              </template>
              <template v-else>
                进度见 <el-link type="primary" @click="tab = 'review'">「审核」页签</el-link>。
              </template>
            </template>
            <template v-if="hoursState.request">
              <br />最近一条{{ hoursState.request.kind_label }}申请：
              {{ hoursState.request.status_label }} · {{ hoursState.request.reason }}
              <span v-if="hoursState.request.reply" class="pre">（意见：{{ hoursState.request.reply }}）</span>
            </template>
          </div>
        </el-alert>
        <el-alert v-else-if="hoursState.lock_pending" type="warning" show-icon
                  :closable="false"
                  title="时长已提交校青协审核，审核期间本页只读">
          <div style="line-height:1.6">
            通过即锁定时长，志愿北京活动名称推给对外联络部，外联确认后活动标记为「已完成」；驳回会退回给你，改完重新核准再提交一次。
            审核进度与历史见 <el-link type="primary" @click="tab = 'review'">「审核」页签</el-link>。
            <template v-if="hoursState.request">
              <br />提交说明：{{ hoursState.request.reason }}
            </template>
          </div>
        </el-alert>
        <el-alert v-else-if="hoursState.approved_at" type="warning" show-icon
                  :closable="false"
                  :title="`已核准，申诉截止 ${fmtDT(hoursState.appeal_deadline)}`">
          <div style="line-height:1.6">
            志愿者已收到时长通知，将于截止前确认或提出申诉，超时未确认自动按「无误 + 志愿时长」处理。
            截止时没有申诉将自动提交校青协审核，<b>有申诉就不会自动提交</b>——改完再核准一次
            （只会再通知 申诉过或数值变过的人），或直接「提交时长审核」。
            <br />这期间手动加扣、删手动时长、改核定数会<b>撤销核准</b>，并清掉那个人选的劳务，改完要重新核准；处理申诉不撤销核准。
            <template v-if="hoursState.pending_appeals">
              <br />当前有 <b>{{ hoursState.pending_appeals }}</b> 条申诉待处理
            </template>
            <template v-if="hoursState.request?.kind === 'lock'
                            && hoursState.request.status === 'rejected'">
              <br />上次审核未通过：<span class="pre">{{ hoursState.request.reply }}</span>——改完重新核准再提交一次。
            </template>
          </div>
        </el-alert>
        <el-alert v-else type="info" show-icon :closable="false"
                  title="时长未核准，志愿者无法查看确认"
                  description="先算好、核对完，再点「确认核准时长」，志愿者将收到时长通知" />
        <!-- 手机上这张表有八列，横滑着改时长没法用：改成卡片，
             时长输入框和明细放在卡片正文里，勾选走卡片自己的多选 -->
        <DataCards v-if="narrow && hours.length" :rows="hoursSorted" v-loading="hoursLoading"
                   title-key="user.name" row-key="user.id" style="margin-top:12px"
                   :selectable="!hoursFrozen" @selection-change="onHoursSelect"
                   :fields="[{ key: 'user.student_id', label: '学号' },
                             { key: 'user.college', label: '学院' }]">
          <template #extra="{ row }">
            <el-tag v-if="row.party_school" size="small" effect="plain">党校</el-tag>
            <el-tag v-if="row.pending" size="small" type="warning" effect="plain">待签退</el-tag>
            <el-tag v-else-if="row.auto_checkout" size="small" type="danger" effect="plain">
              待核·自动兜底
            </el-tag>
            <el-tag :type="row.confirmed ? 'success' : 'info'"
                    :effect="row.confirmed ? 'light' : 'plain'" size="small">
              {{ row.confirmed ? '已确认' : '待确认' }}
            </el-tag>
          </template>
          <template #body="{ row }">
            <div class="mh-row">
              <span class="dim">时长</span>
              <!-- 只读：这个数是从签到签退算出来的，当场改一个数字既没有依据、
                   也会在下次重算时被覆盖。要动账去下面的「手动加/扣」 -->
              <b>{{ row.hours }}h</b>
            </div>
            <div v-if="hoursState.approved_at" class="mh-row">
              <span class="dim">本人表态</span><VolunteerSide :row="row" />
            </div>
            <HoursParts :parts="row.parts" removable
                        @remove="p => delManualPart(row, p)" />
          </template>
        </DataCards>
        <div v-else-if="hours.length" class="scroll-x">
        <el-table :data="hoursSorted" size="small" style="margin-top:12px;min-width:660px"
                  v-loading="hoursLoading" max-height="360" @selection-change="onHoursSelect">
          <el-table-column type="selection" width="40" :selectable="() => !hoursFrozen" />
          <el-table-column type="expand" width="30">
            <template #default="{row}">
              <HoursParts :parts="row.parts" removable
                          @remove="p => delManualPart(row, p)" />
            </template>
          </el-table-column>
          <el-table-column label="姓名" width="110">
              <template #default="{row}">
                {{ row.user.name }}
                <!-- 党校同学的时长不进导出表，核准时一眼看得出来 -->
                <el-tag v-if="row.party_school" size="small" effect="plain"
                        style="margin-left:4px">党校</el-tag>
              </template>
            </el-table-column>
          <el-table-column prop="user.student_id" label="学号" width="110" />
          <el-table-column prop="user.college" label="学院" width="130" />
          <!-- 只读：这个数是从签到签退算出来的，当场改一个数字既没有依据、
               也会在下次重算时被覆盖。要动账去下面的「手动加/扣」 -->
          <el-table-column label="时长(h)" width="100">
            <template #default="{row}"><b>{{ row.hours }}</b></template>
          </el-table-column>
          <el-table-column label="标记" width="150">
            <template #default="{row}">
              <el-tooltip v-if="row.pending" content="未签退，签到码还开着，暂按当前时间估算">
                <el-tag size="small" type="warning" effect="plain">待签退</el-tag>
              </el-tooltip>
              <el-tooltip v-else-if="row.auto_checkout"
                          content="签到码已过期或关闭时仍未签退，已按场次结束时间兜底">
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

        <!-- 手动添加时长（补记录活动的名单与时长都在「名单导入」里改） -->
        <div v-if="!isRecord" class="section-title" style="margin-top:16px">
          手动加 / 扣时长（签到漏扫、中途离场等情况）
        </div>
        <el-card v-if="!isRecord" shadow="never">
          <div class="toolbar" style="align-items:flex-start">
            <el-select v-model="mh.userIds" multiple filterable collapse-tags
                       collapse-tags-tooltip placeholder="选择志愿者（可多选）"
                       :disabled="hoursFrozen" style="width:300px">
              <el-option v-for="v in rosterOptions" :key="v.student_id"
                         :value="v.student_id"
                         :label="`${v.name}（${v.student_id}）· ${v.where}`" />
            </el-select>
            <el-radio-group v-model="mh.sign" :disabled="hoursFrozen">
              <el-radio-button value="add">加</el-radio-button>
              <el-radio-button value="deduct">扣</el-radio-button>
            </el-radio-group>
            <el-input-number v-model="mh.hours" :min="0.5" :step="0.5"
                             :disabled="hoursFrozen" style="width:120px" />
            <!-- 服务日期：这一段时长对应的服务发生在哪一天，不是今天录的日期 -->
            <el-date-picker v-model="mh.date" type="date" value-format="YYYY-MM-DD"
                            placeholder="服务日期" :disabled="hoursFrozen"
                            style="width:150px" />
            <el-input v-model="mh.note" placeholder="备注（必填，写明为什么）"
                      :disabled="hoursFrozen" style="width:220px" />
            <el-button type="primary"
                       :disabled="hoursFrozen || !mh.userIds.length || !mh.note.trim()
                                  || !mh.date"
                       @click="addManualHours">
              {{ mh.sign === 'deduct' ? '批量扣减' : '批量添加' }}{{ mh.userIds.length ? `（${mh.userIds.length} 人）` : '' }}
            </el-button>
          </div>
          <div class="dim">
            只列这场活动已确认报名的志愿者；没报名的人请在补记录活动里记。
          </div>
          <div class="dim">
            加进这个人这场活动的总时长里，在表格中展开他的明细可以看到那一段，
            要删也在那里删。本人只会收到一条核准通知、只见得到一笔账。
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ============ 管理者 ============ -->
      <el-tab-pane v-if="isPublic && !isRecord" label="签收情况" name="acks" lazy>
        <div class="toolbar">
          <RefreshBar :load="loadBatches" :at="ackAt" />
        </div>
        <div class="dim" style="margin-bottom:12px">
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
                       style="margin-top:12px" />
          <div v-if="b.pending.length" style="margin-top:12px">
            <span class="dim">未签收：</span>
            <el-tag v-for="p in b.pending" :key="p.student_id" size="small"
                    type="info" effect="plain" style="margin:4px 4px 4px 0">
              {{ p.name }}（{{ p.student_id }}）
            </el-tag>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ============ 审核：立项、时长、志愿北京外联确认三道审核的进度与材料 ============ -->
      <el-tab-pane label="审核" name="review" lazy>
        <ReviewPanel :activity-id="activityId" :act="act" @status-changed="loadAll" />
      </el-tab-pane>

      <el-tab-pane label="管理者" name="managers" lazy>
        <el-alert type="info" :closable="false">
          一个活动可由多人共同管理：创建者自动成为管理者，管理者之间权限相同，
          管理者须为<b>活动发起方/青协</b>。
        </el-alert>
        <el-card shadow="never">
          <div class="toolbar">
            <!-- 常加的就那几个人：会长团、部长、自己手下的骨干与成员。
                 名单里没有的直接敲学工号，同一个框都能用 -->
            <el-select v-model="newMgrSid" filterable allow-create default-first-option
                       placeholder="选择或输入学工号/教工号" style="width:300px">
              <el-option-group v-for="g in mgrCandidates" :key="g.label" :label="g.label">
                <el-option v-for="u in g.users" :key="u.id" :value="u.student_id"
                           :label="`${u.name}（${u.student_id}）${u.post_label}`" />
              </el-option-group>
            </el-select>
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
        <div v-loading="logsLoading">
        <el-timeline v-if="logs.length">
          <el-timeline-item v-for="l in logs" :key="l.id" placement="top"
                            :timestamp="fmtDT(l.created_at)"
                            :type="l.action === 'delete' ? 'danger' : 'primary'">
            <b>{{ l.actor_name }}</b>
            <el-tag size="small" effect="plain" style="margin:0 8px">{{ l.action_label }}</el-tag>
            <span>{{ l.summary }}</span>
            <AuditChanges :changes="l.changes" strong line-style="margin-top:4px" />
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无操作记录" />
        </div>
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
          <div class="dim" style="margin-top:4px">
            <template v-if="screened">
              报名后筛选：建好岗位的场次，志愿者按岗位勾意向；没建岗位的场次
              勾的是「待分配」，录取之后再排岗位。
            </template>
            <template v-else-if="groupForm.mode === 'pick'">
              提前设置岗位分工，学生报名时自己挑岗位。
            </template>
            <template v-else>
              适用于未敲定岗位分工的情况，学生报名时不挑岗位。分工定下来后直接在
              本场次下建岗位，再一键分配或导入排班。
            </template>
            有人报名之后不能再切换模式。
          </div>
          <el-alert v-if="modeSwitchCost" type="warning" show-icon :closable="false" :title="modeSwitchCost" />
        </el-form-item>
        <el-alert type="info" :closable="false">
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
            <span class="dim" style="margin-left:12px">0 = 不设候补</span>
          </el-form-item>
        </template>
        <el-form-item label="锁定名单">
          <span class="dim" style="margin-right:8px">开场前</span>
          <el-input-number v-model="groupForm.lock_hours" :min="0" :max="72" />
          <span class="dim" style="margin-left:8px">小时自动锁定</span>
          <div class="dim" style="margin-top:4px">
            锁定后不再接受新报名，停止候补自动转正，此时退出的志愿者会被记录；
            如需补人请在志愿者管理页面手动转正。
            0 = 不自动锁定，随时可以在岗位设置里手动锁定、解锁。
          </div>
        </el-form-item>
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

    <!-- 培训 / 彩排：挂在整个活动上 -->
    <el-dialog v-model="attDlg.visible"
               :title="(attDlg.editing ? '编辑' : '添加') + '培训 / 彩排'" width="560">
      <p class="dim">
        报上被圈到的岗位就连带报上，被圈到的报名全退了才连带退；可以跨场次。
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
          <div style="width:100%">
            <el-select v-model="attForm.applies_to_positions" multiple collapse-tags
                       collapse-tags-tooltip placeholder="留空 = 本活动所有报上名的人"
                       style="width:100%">
              <el-option-group v-for="g in shifts" :key="g.id" :label="`${g.name}（${g.time_text}）`">
                <el-option v-for="p in g.positions" :key="p.id" :value="p.id"
                           :label="p.is_placeholder ? `${g.name}·待分配` : `${p.name}（${g.name}）`" />
              </el-option-group>
            </el-select>
            <div v-if="roles.length" class="att-quick">
              <span class="dim">按岗位勾上所有时段：</span>
              <el-check-tag v-for="r in roles" :key="r.name" :checked="roleChecked(r)"
                            @change="toggleRole(r)">{{ r.name }}</el-check-tag>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="时长认定">
          <el-radio-group v-model="attForm.hours_mode">
            <el-radio value="fixed">固定计时</el-radio>
            <el-radio value="checkin">扫码计时</el-radio>
            <el-radio value="none">不计时长</el-radio>
          </el-radio-group>
          <div class="dim">
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
      <p class="dim">
        核准之后，志愿者将会收到确认通知，截止前确认无误或提出申诉。截止后若无申诉，系统自动提交校青协审核（须先在「审核」页签填好志愿北京活动名称）；通过即锁定时长，对外联络部确认志愿北京后活动标记为「已完成」。
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

    <!-- 提交时长审核：一并填志愿北京活动名称，锁定后推给对外联络部核对 -->
    <el-dialog v-model="lockDlg.visible" title="提交时长审核" width="520">
      <p v-if="lockDlg.early" class="dim">
        申诉期还没结束（截止 {{ fmtDT(hoursState.appeal_deadline) }}）。现在提交，还没确认时长的志愿者就没机会提申诉了；
        到点没有申诉的话系统会自动提交，不必手动来这一下。
      </p>
      <p v-if="hoursState.pending_appeals" class="dim">还有 {{ hoursState.pending_appeals }} 条申诉没处理。</p>
      <p class="dim">
        提交后时长冻结、不能再改，等校青协审核；通过即锁定时长，志愿北京活动名称推给对外联络部，
        外联确认后活动标记为「已完成」；驳回则退回你修改。
      </p>
      <el-form label-position="top">
        <!-- 对外活动的盖章材料就地上传，不用再去「审核」页签找 -->
        <el-form-item v-if="lockFiles?.external" label="盖章材料（PDF 或图片，多页的请合成一个 PDF）"
                      required>
          <div style="width:100%">
            <ProjectFileSlot v-for="s in STAMPED_SLOTS" :key="s.kind" :activity-id="activityId"
                             :kind="s.kind" :label="s.label" :file="lockFiles[s.kind]"
                             :can-upload="lockFiles[`can_upload_${s.kind}`]"
                             :block="lockFiles[`${s.kind}_block`]"
                             :exts="lockFiles[`${s.kind}_exts`]"
                             :template="templates[s.template]"
                             :required="!lockFiles[s.kind]" @changed="onLockFileChanged" />
          </div>
        </el-form-item>
        <el-form-item label="志愿北京上的活动名称（至少一个）" required>
          <VbNamesInput v-model="lockDlg.names" />
        </el-form-item>
        <el-form-item label="说明（选填）">
          <el-input v-model="lockDlg.note" maxlength="200" placeholder="给审核人看的一句话" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="lockDlg.visible = false">取消</el-button>
        <el-button type="warning" :loading="lockDlg.busy" @click="doSubmitLock">
          {{ lockDlg.early ? '仍然提交' : '提交' }}
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
          <!-- label 只放短的那截：选中后它显示在输入框里，塞进备注就只剩省略号。
               备注改在下拉的 slot 里单独一行——有的部门备注是好几行字，手机上
               必须能换行，不然会把下拉撑破，右边直接看不见 -->
          <el-select v-model="submitDlg.deptId" placeholder="选择归属部门"
                     popper-class="dept-popper" style="width:100%">
            <el-option v-for="d in depts" :key="d.id" :value="d.id" :disabled="!d.has_head"
                       :label="d.name + (d.head_name ? `（部长 ${d.head_name}）` : '（未设部长）')">
              <div class="dept-opt">
                <div>
                  {{ d.name }}
                  <span class="dim">
                    {{ d.head_name ? `部长 ${d.head_name}` : '未设部长' }}
                  </span>
                </div>
                <div v-if="d.note" class="dept-opt-note">{{ d.note }}</div>
              </div>
            </el-option>
          </el-select>
        </el-form-item>
        <!-- 交对外联络部（二级团体的活动）要带立项书，在这里就能传 -->
        <el-form-item v-if="submitToLiaison" label="立项书" required>
          <ProjectFileSlot v-if="submitFiles" :activity-id="activityId" kind="proposal"
                           label="立项书" :file="submitFiles.proposal"
                           :can-upload="submitFiles.can_upload_proposal"
                           :block="submitFiles.proposal_block"
                           :exts="submitFiles.proposal_exts" :template="templates.proposal"
                           :required="!submitFiles.proposal" @changed="loadSubmitFiles" />
        </el-form-item>
        <el-alert v-if="screened && !formCount" type="warning" :closable="false"
                  title="还没有设置报名问卷"
                  description="报名后筛选的活动，没有问卷的话筛选时只能看志愿者档案。需要的话先取消，到「岗位设置」页签设置；不需要可以直接提交。" />
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
      <p class="dim">
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
      <p class="dim">
        {{ resolveDlg.row?.name }}：{{ resolveDlg.row?.old_hours }}h →
        申报 {{ resolveDlg.row?.claimed_hours }}h<br />
        理由：{{ resolveDlg.row?.reason }}
      </p>
      <el-form label-width="88px">
        <el-form-item v-if="resolveDlg.accept" label="改为">
          <el-input-number v-model="resolveDlg.hours" :min="0.5" :max="24" :step="0.5" />
          <span class="dim" style="margin-left:12px">小时</span>
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
          <el-button type="primary" style="margin-top:12px" @click="copyPoster">
            复制文案
          </el-button>
        </div>
        <div class="recruit-qr">
          <img :src="`/api/activities/${activityId}/qr.svg?base=${qrBase()}`"
               width="200" height="200" alt="活动二维码" />
          <div class="dim">扫码直达活动详情页</div>
          <!-- 文案里写的是「扫描下方二维码」：发群时要配这张图 -->
          <el-button size="small" style="margin-top:8px" @click="downloadQrPng">
            下载二维码图片
          </el-button>
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
        <!-- maxlength 跟着库里这几列的长度：不在这儿拦，超长要到保存那一下
             才被后端打回来 -->
        <el-form-item label="岗位名称" required><el-input v-model="stationForm.name"
                    maxlength="128" placeholder="如：东门引导" /></el-form-item>
        <el-form-item label="服务地点"><el-input v-model="stationForm.location"
                    maxlength="128"
                    placeholder="如：世纪馆北门（留空则用活动地点）" /></el-form-item>
        <el-form-item label="名额" required>
          <el-input-number v-model="stationForm.quota" :min="1" />
        </el-form-item>
        <el-form-item label="候补名额">
          <el-input-number v-model="stationForm.waitlist_quota" :min="0" />
          <span class="dim" style="margin-left:12px">满员后可排队的人数，0 = 不设候补</span>
        </el-form-item>
        <el-form-item label="岗位组">
          <el-input v-model="stationForm.group_name" style="width:220px"
                    maxlength="64" placeholder="如：东门片区（选填）" />
          <div class="dim">岗位组共用一名组长，留空则本岗位自成一组</div>
        </el-form-item>
        <el-form-item label="志愿服装">
          <el-radio-group v-model="stationForm.attire">
            <el-radio value="none">不需要</el-radio>
            <el-radio value="issued">统一发放</el-radio>
            <el-radio value="own">志愿者自备</el-radio>
          </el-radio-group>
          <div class="dim">统一发放可在「志愿服装」页扫码或手动登记领取与归还</div>
        </el-form-item>
        <!-- 三处备注出现即必填：左侧红星，saveStation 与后端各拦一道 -->
        <el-form-item v-if="stationForm.attire !== 'none'" label="服装备注" required>
          <el-input v-model="stationForm.attire_note" maxlength="256" show-word-limit
                    :placeholder="stationForm.attire === 'issued'
                      ? '领取/归还方式：如 活动前一天 8:00 在 学活124 领取'
                      : '样式要求：如 白色上衣、深色长裤、运动鞋'" />
        </el-form-item>
        <el-form-item label="报酬方式">
          <el-checkbox v-model="stationForm.pay_optional">本岗位可选劳务</el-checkbox>
          <div class="dim">按需勾选。若勾选，志愿者确认时长时选择报酬方式，劳务与时长二选一</div>
        </el-form-item>
        <el-form-item v-if="stationForm.pay_optional" label="劳务备注" required>
          <el-input v-model="stationForm.pay_note" maxlength="256" show-word-limit
                    placeholder="标准与发放方式：如 60元/小时，活动后一周内发放" />
        </el-form-item>
        <el-form-item label="组长">
          <el-checkbox v-model="stationForm.needs_leader">本组需要一名组长</el-checkbox>
          <div class="dim">在「志愿者管理」页面指定，报名时志愿者可表态供你参考。</div>
        </el-form-item>
        <el-form-item v-if="stationForm.needs_leader" label="组长任务" required>
          <el-input v-model="stationForm.leader_task" maxlength="256" show-word-limit
                    placeholder="组长要带队做什么：如 带队进场、清点人数、结束后对账" />
          <div v-if="stationForm.group_name?.trim()" class="dim">
            一个岗位组共用一名组长，也共用这一份任务：保存时会同步到同组其他岗位
          </div>
        </el-form-item>
        <el-form-item label="条件要求">
          <div class="two-col">
            <el-select v-model="stationForm.genderReq" clearable placeholder="性别（不限）">
              <el-option value="女" label="限女生" /><el-option value="男" label="限男生" />
            </el-select>
            <el-select v-model="stationForm.collegeReq" multiple clearable collapse-tags
                       filterable placeholder="学院（不限）">
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
        <!-- 同一个岗位分在好几个时段：改一处，别的时段跟着改；名额各管各的 -->
        <el-form-item v-if="positionDlg.editing && sameNameOthers.length" label="其他时段">
          <el-checkbox v-model="positionDlg.syncSame">
            同时修改其他时段的「{{ positionDlg.editing.name }}」（{{ sameNameOthers.length }} 个，名额各自保留）
          </el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="positionDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="savingStation" @click="saveStation">保存</el-button>
      </template>
    </el-dialog>

    <!-- 按岗位看：给一个岗位加时段 = 在某个场次下复制出同名岗位 -->
    <el-dialog v-model="slotDlg.visible" :title="`给「${slotDlg.role?.name || ''}」加时段`" width="480">
      <el-radio-group v-model="slotDlg.mode">
        <el-radio-button value="existing" :disabled="!slotTargets.length">放进已有场次</el-radio-button>
        <el-radio-button value="new">新建一个场次</el-radio-button>
      </el-radio-group>
      <el-form label-width="88px" style="margin-top:12px">
        <el-form-item v-if="slotDlg.mode === 'existing'" label="场次" required>
          <el-select v-model="slotDlg.shiftId" style="width:100%" placeholder="选一个还没有这个岗位的场次">
            <el-option v-for="g in slotTargets" :key="g.id" :value="g.id"
                       :label="`${g.name}（${g.time_text}）`" />
          </el-select>
        </el-form-item>
        <template v-else>
          <el-form-item label="场次名" required><el-input v-model="slotDlg.name" /></el-form-item>
          <el-form-item label="开始时间" required>
            <DateTimeField v-model="slotDlg.start" :default-time="DAY_START" />
          </el-form-item>
          <el-form-item label="结束时间" required>
            <DateTimeField v-model="slotDlg.end" :default-time="DAY_END" />
          </el-form-item>
        </template>
      </el-form>
      <div class="dim">
        下一步打开岗位表单，内容照「{{ slotDlg.role?.name }}」填好，改名额等这个时段不一样的地方就行；
        叫这个岗位的培训/彩排也会叫这个时段的人。
      </div>
      <template #footer>
        <el-button @click="slotDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="slotDlg.busy" @click="saveSlot">下一步</el-button>
      </template>
    </el-dialog>

    <!-- 岗位批量复制到其它场次 -->
    <el-dialog v-model="copyPosDlg.visible" title="复制岗位到其它场次" width="520">
      <p class="dim">
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
                       :label="`${g.name}（${g.time_text}）`" />
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

    <!-- 问卷结果：按场次、岗位挑人导出，附件按「场次/岗位」分文件夹 -->
    <el-dialog v-model="answersDlg.visible" title="导出问卷结果" width="480">
      <el-form label-width="56px">
        <el-form-item label="场次">
          <el-select v-model="answersDlg.shifts" multiple clearable collapse-tags
                     collapse-tags-tooltip placeholder="全部场次" style="width:100%">
            <el-option v-for="g in shifts" :key="g.id" :value="g.id" :label="g.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="岗位">
          <el-select v-model="answersDlg.positions" multiple clearable collapse-tags
                     collapse-tags-tooltip placeholder="全部岗位" style="width:100%">
            <el-option-group v-for="g in answersShifts" :key="g.id" :label="g.name">
              <el-option v-for="p in g.positions" :key="p.id" :value="p.id" :label="p.name" />
            </el-option-group>
          </el-select>
        </el-form-item>
      </el-form>
      <div class="dim">
        导出名单里已录取的人，每人每个岗位一行；不选就是全部。选了岗位时，还没分到岗位的人不在其中。
        问卷里有上传的文件时，会再问要不要打包下载，附件按「场次/岗位」分文件夹。
      </div>
      <template #footer>
        <el-button @click="answersDlg.visible = false">取消</el-button>
        <el-button type="primary" @click="doAnswersExport">导出</el-button>
      </template>
    </el-dialog>

    <!-- 智能起草场次岗位：只在「岗位设置」页签、能改结构的时候出现（草稿或调整中） -->
    <AiStructureAssist v-if="tab === 'tree' && !isRecord && !isOver && !structureLocked"
                       :activity-id="activityId" @created="loadAll" />

    <!-- 基本信息编辑 -->
    <el-dialog v-model="editBase" title="编辑活动基本信息" width="620">
      <ActivityForm v-model="baseForm" v-model:range="baseRange" :modes="modes"
                    :status="act.status" />
      <template #footer>
        <el-button @click="editBase = false">取消</el-button>
        <el-button type="primary" :loading="savingBase" @click="saveBase()">保存并通知变更</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, h, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox, ElTag } from 'element-plus'
import { api, download as dl, downloadExport, fmtDT, pageUrl, qrBase } from '../api'
import AiStructureAssist from '../components/AiStructureAssist.vue'
import AssignPanel from '../components/AssignPanel.vue'
import CheckinPanel from '../components/CheckinPanel.vue'
import RefreshBar from '../components/RefreshBar.vue'
import AttirePanel from '../components/AttirePanel.vue'
import MaterialManager from '../components/MaterialManager.vue'
import AuditChanges from '../components/AuditChanges.vue'
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
import ReviewPanel from '../components/ReviewPanel.vue'
import RecordRosterPanel from '../components/RecordRosterPanel.vue'
import ActivityOverviewCard from '../components/ActivityOverviewCard.vue'
import VbNamesInput from '../components/VbNamesInput.vue'
import ProjectFileSlot from '../components/ProjectFileSlot.vue'
import { activityBody, activityToForm, validateActivity } from '../composables/activityForm'
import { useNarrow } from '../composables/useNarrow'
import { DAY_END, DAY_START, auth, canSee } from '../store'

const route = useRoute()
const act = ref(null)
const narrow = useNarrow()
// 支持 ?tab=roster 直达页签；没带时等活动加载完再定（见 loadAll）
const tab = ref(String(route.query.tab || ''))
// 未发布（草稿/待审批/待发布）只留 岗位设置 / 管理者 / 变更记录：那时一个报名的人
// 都没有，名单、签到、服装、导出、签收全是空页
const isPublic = computed(() =>
  ['published', 'closed', 'finished', 'vb_pending', 'completed'].includes(act.value?.status))
// 不公开招募（补记录）：没有场次、报名、签到，只有名单导入与时长结账
const isRecord = computed(() => act.value?.recruit_mode === 'record')
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
const mgrCandidates = ref([])
const moveSel = ref({})

// —— 发送通知 ——
const notifyDlg = ref({ visible: false, saving: false })
const notifyForm = ref({ scope: 'all', shift_id: '', title: '', content: '',
                         need_ack: false })
const ackBatches = ref([])
// 各块数据上次拉成功的时刻，给刷新条显示（见 RefreshBar）
const ackAt = ref(0)
const rosterAt = ref(0)
const hoursAt = ref(0)

// —— 签收情况 ——
async function loadBatches() {
  ackBatches.value = await api.get(
    `/api/staff/activities/${activityId}/notify-batches`)
  ackAt.value = Date.now()
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
      warnBusy(d.busy)
    }
    // 等刷完再返回：离开本页的拦截里会先 await 它，编辑态还没刷掉的话，
    // 那一跳要是又走一遍拦截，人就被问第二遍
    await loadAll()
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
const hoursState = ref({})                     // 核准/锁定状态，这一页按它决定给哪些按钮
const approveDlg = ref({ visible: false, deadline: '', busy: false })
const lockDlg = ref({ visible: false, early: false, names: [], note: '', busy: false })
// sign：add 加 / deduct 扣。扣减传负数（见后端 hours_manual），备注两边都必填。
// date：这段时长算哪一天的服务（不是今天录的日期），一次批量共用一个
const mh = ref({ userIds: [], sign: 'add', hours: 0.5, note: '', date: '' })

/** 这场活动服务的那一天，给手动时长当默认服务日期。
 *
 * 场次与补记录活动的开始时间都是本地裸时间串（2026-09-27T16:00），前 10 位就是
 * 本地日期；绕一遍 Date 再格式化，会随浏览器时区把日子串到前后一天去。
 */
function serviceDateOf(a) {
  const first = a?.shifts?.[0]
  const raw = first?.start_time || a?.held_start || ''
  return String(raw).slice(0, 10)
}

// 默认服务日期是按活动填的：这场活动刷新多少次都不再动它（核准、确认报名都会
// 重新 loadAll，把管理者选过的日子刷回去等于白选），换活动才按新活动重填
let mhDateFor = ''

// 手动加时长的候选人 = 本活动全部场次的已报名志愿者（含未签到的）
// pick 模式却一个岗位都没有 = 学生根本报不了名
// 报名已经结束的活动，再说「暂停报名」只会让人困惑
const editLabel = computed(() =>
  act.value?.status === 'published' ? '调整岗位（暂停报名）' : '调整岗位')

// 已发布的活动，不进编辑态就不能动结构；没发布的（草稿、待审批、待发布）没人能报名，直接改
const structureLocked = computed(
  () => !!act.value && !['draft', 'pending', 'approved'].includes(act.value.status)
    && !act.value.editing)

// 报名是否已经截止：截止后还有人没岗位，就不再是「等着分」而是漏排了
const regClosed = computed(
  () => ['closed', 'finished', 'vb_pending', 'completed'].includes(act.value?.status))
// 活动办完了：结构与基本信息都是只读
const isOver = computed(
  () => ['finished', 'vb_pending', 'completed'].includes(act.value?.status))

function unassignedHint(g) {
  if (g.mode === 'pick') {
    return '这些人报了名却没有岗位（岗位被删或管理员补录）；到「志愿者管理」手动调岗'
  }
  return regClosed.value
    ? '报名已截止，这些人还在待分配；到「志愿者管理」一键分配或导入排班'
    : '本场次报名只锁时段，截止后再分配岗位'
}

// 「截止后统一分配」场次里的「待分配」不是岗位，是装还没安排的人的地方
const realPositions = g => g.positions.filter(p => !p.is_placeholder)

// 岗位设置页上的红色感叹号：这里列出的都是「不处理就会出事」的场次。
// assign 场次报名期没有岗位是设计如此，不报警；截止后还没建、还有人没分才是漏排。
const treeAlerts = computed(() => shifts.value.flatMap(g => {
  const real = realPositions(g)
  const alert = text => [{ id: g.id, name: g.name, text }]
  if (g.mode === 'pick') {
    if (!real.length) return alert('还没有岗位，学生无法报名')
    return g.unassigned_count
      ? alert(`还有 ${g.unassigned_count} 人没有岗位，到「报名与调配」调岗`)
      : []
  }
  if (!regClosed.value) return []
  if (!real.length) {
    return alert('报名已截止，还没建岗位。在本场次下建好岗位，再到「报名与调配」一键分配或导入排班。')
  }
  if (!g.unassigned_count) return []
  const room = real.reduce((n, p) => n + Math.max(0, p.quota - p.enrolled), 0)
  return alert(`还有 ${g.unassigned_count} 人在待分配`
    + (room < g.unassigned_count
       ? `，而各岗位空位合计只有 ${room} 个——先加岗位或名额再分配。`
       : '，到「报名与调配」一键分配或导入排班。'))
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
const logsLoading = ref(false)

/** 变更记录：切到这一页才拉（见 watch(tab) 与 loadAll 里的直达分支）。 */
async function loadLogs() {
  logsLoading.value = true
  try {
    const d = await api.get(
      `/api/staff/audit-logs?activity_id=${activityId}&size=50`)
    logs.value = d.items || []
  } finally { logsLoading.value = false }
}
// 口径同 models.Enrollment 的 cancel_type；空串是负责人那边取消的
const cancelType = x => ({ normal: '退选', locked: '锁定后退出', expired: '确认超时',
                           abandoned: '放弃候补', rejected: '未录取',
                           not_chosen: '未选中' }[x.cancel_type] || '取消')

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
    // 手动加/扣时长的服务日期默认落在活动服务的那天：时长多是事后统一录进来，
    // 空着的话每录一条都要自己挑一次日历
    if (mhDateFor !== activityId) {
      mhDateFor = activityId
      mh.value.date = serviceDateOf(d)
    }
    // 默认页签：发布后进概览；未发布进岗位设置，补记录活动没有岗位设置，进名单导入
    const fallback = d.recruit_mode === 'record' ? 'records' : 'tree'
    if (!tab.value) tab.value = isPublic.value ? 'overview' : fallback
    if (d.recruit_mode === 'record' && ['tree', 'roster', 'checkin'].includes(tab.value)) {
      tab.value = 'records'
    }
    if (tab.value === 'overview' && !isPublic.value) tab.value = fallback
    const rosters = await Promise.all(d.shifts.map(g => api.get(`/api/staff/shifts/${g.id}/roster`)))
    // 经 act.value 写回：直接改 d 上的对象不触发依赖名单的计算属性（页签叹号）
    act.value.shifts.forEach((g, i) => {
      g._roster = rosters[i]
      g._rosterAt = Date.now()
    })
    // 弹窗里的表要等打开才加载，题数得自己先问一句，按钮上要写
    formCount.value = (await api.get(
      `/api/staff/activities/${activityId}/form`)).questions.length
    await loadManagers()
    loadAppeals()      // 标签上的红点要一进页面就在，不能等切到那一页才知道
    // ?tab=hours 这类直达：watch(tab) 不会触发，切过去才加载的那几页要在这里
    // 补上——时长页的核准按钮要拿申诉截止时间的默认值，签收页要有批次列表
    if (tab.value === 'hours') hoursPreview(true)
    if (tab.value === 'acks') loadBatches()
    if (tab.value === 'logs') loadLogs()
    loadApproval()
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

/** 拉平的场次清单：主场次 + 各自挂着的培训/彩排。
 *
 * 详情接口只在顶层给主场次，培训/彩排嵌在 attached 里。签到码要能给培训场次
 * 生成——hours_mode 是「按签到计」的培训，签不了到就没时长。
 */
const allShifts = computed(() => [
  ...shifts.value.map(g => ({
    id: g.id, name: g.name, kind: g.kind, kind_label: g.kind_label,
    time_text: g.time_text, start_time: g.start_time, end_time: g.end_time,
    positions: g.positions || [] })),
  ...(act.value?.trainings || []).map(a => ({
    id: a.id, name: a.name, kind: a.kind, kind_label: a.kind_label,
    time_text: a.time_text, start_time: a.start_time, end_time: a.end_time,
    // 培训/彩排自己不设岗位（用 applies_to_positions 圈人），只能统一签到
    positions: [] })),
])

// —— 按岗位看 ——
const VIEW_KEY = 'yvruc_tree_view'
function readView() {
  try { return localStorage.getItem(VIEW_KEY) === 'role' ? 'role' : 'shift' } catch { return 'shift' }
}
const treeView = ref(readView())
watch(treeView, v => {
  try { localStorage.setItem(VIEW_KEY, v) } catch { /* 存不了就每次默认按场次看 */ }
})
/** 同名岗位合成一个「岗位」，列它的各个时段。占位岗位不是岗位，不算 */
const roles = computed(() => {
  const by = new Map()
  for (const g of shifts.value) {
    for (const p of g.positions) {
      if (p.is_placeholder) continue
      if (!by.has(p.name)) by.set(p.name, { name: p.name, slots: [], quota: 0, needs_leader: false })
      const r = by.get(p.name)
      r.slots.push({ g, p })
      r.quota += p.quota
      r.needs_leader ||= p.needs_leader
    }
  }
  for (const r of by.values()) r.slots.sort((a, b) => a.g.start_time.localeCompare(b.g.start_time))
  return [...by.values()]
})
const trainingsOf = r => (act.value?.trainings || []).filter(t => !t.applies_to_positions.length
  || r.slots.some(x => t.applies_to_positions.includes(x.p.id)))

const slotDlg = ref({ visible: false, role: null, mode: 'existing', shiftId: '',
                      name: '', start: '', end: '', busy: false })
// 能放进去的场次：还没有这个岗位的服务场次
const slotTargets = computed(() => (slotDlg.value.role
  ? shifts.value.filter(g => !slotDlg.value.role.slots.some(x => x.g.id === g.id)) : []))
function openAddSlot(r) {
  slotDlg.value = { visible: true, role: r, mode: 'existing', shiftId: '',
                    name: r.name, start: '', end: '', busy: false }
  if (!slotTargets.value.length) slotDlg.value.mode = 'new'
}
/** 改岗位信息：打开第一个时段的表单，默认勾上「同时修改其他时段」 */
function editRole(r) {
  openStation(r.slots[0].p, r.slots[0].g)
  positionDlg.value.syncSame = true
}
async function saveSlot() {
  const d = slotDlg.value
  let shiftId = d.shiftId
  if (d.mode === 'existing' && !shiftId) return ElMessage.warning('请选择场次')
  if (d.mode === 'new') {
    if (!d.name.trim()) return ElMessage.warning('请填写场次名')
    if (!d.start || !d.end) return ElMessage.warning('请选择起止时间')
    d.busy = true
    try {
      shiftId = (await api.post(`/api/staff/activities/${activityId}/shifts`, {
        name: d.name.trim(), mode: 'pick', capacity: 0,
        start_time: toISO(d.start), end_time: toISO(d.end) })).id
      await loadAll()
    } finally { d.busy = false }
  }
  const src = d.role.slots[0]
  d.visible = false
  // 走「复制岗位」那条路：表单照原岗位填好，名字不加「副本」
  openStation(src.p, shifts.value.find(g => g.id === shiftId))
  positionDlg.value.editing = null
  positionDlg.value.copyFrom = src.p.id
}

// 同名岗位：改一个时段时可以顺手改其他时段（名额各自保留）
const sameNameOthers = computed(() => {
  const ed = positionDlg.value.editing
  if (!ed) return []
  return shifts.value.flatMap(g => g.positions)
    .filter(p => !p.is_placeholder && p.name === ed.name && p.id !== ed.id)
})
// 有候补转正待本人确认的场次：页签上的叹号看所有场次，不随下拉框变；下拉框里标出是哪几场
const promoShiftIds = computed(() => new Set(shifts.value
  .filter(g => g._roster?.waitlist?.some(w => w.status === 'pending_confirm'))
  .map(g => g.id)))
// 已经确认下来的时长条数（手动补录的也在各自那一笔里）：有账才谈得上核准
const confirmedCount = computed(() => hours.value.filter(h => h.confirmed).length)

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
    lock_hours: g.lock_hours ?? 12,
    campusReq: g.requirement_tags?.校区 || [],
    stageReq: g.requirement_tags?.学段 || [],
  } : { name: '', start_time: '', end_time: '', capacity: 10, note: '',
        mode: act.value.recruit_mode === 'screened' ? 'assign' : 'pick',   // 筛选活动多半先不定岗
        waitlist_quota: 3, lock_hours: 12, campusReq: [], stageReq: [] }
  shiftDlg.value.visible = true
}
const screened = computed(() => act.value?.recruit_mode === 'screened')
/** 切模式只在一个人都没报时允许（后端同样拦）：两种场次的人记在不同的账上，
 *  有人之后再切，要么作废大家选好的岗位，要么让还没安排的人无处可放。 */
const modeSwitchCost = computed(() => {
  const g = shiftDlg.value.editing
  if (!g || g.mode === groupForm.value.mode) return ''
  return g.enrolled || g.waitlist_count
    ? '本场次已有人报名，不能再切换岗位模式；如确需调整，请新建场次。'
    : ''
})

async function saveShift() {
  const f = groupForm.value
  if (!f.name?.trim()) return ElMessage.warning('请填写场次名称')
  if (!f.start_time || !f.end_time) return ElMessage.warning('请选择起止时间')
  savingGroup.value = true
  try {
    const body = {
      name: f.name, capacity: f.capacity, mode: f.mode, note: f.note,
      waitlist_quota: f.waitlist_quota ?? 3, lock_hours: f.lock_hours ?? 12,
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
const attDlg = ref({ visible: false, editing: null, busy: false })
const attForm = ref({})

// 培训对话框里「按岗位勾上所有时段」
const roleChecked = r => r.slots.every(x => attForm.value.applies_to_positions?.includes(x.p.id))
function toggleRole(r) {
  const ids = r.slots.map(x => x.p.id)
  const cur = attForm.value.applies_to_positions
  attForm.value.applies_to_positions = roleChecked(r)
    ? cur.filter(i => !ids.includes(i))
    : [...new Set([...cur, ...ids])]
}

/** 打开培训/彩排对话框。s：编辑哪一场；preset：新建时预先勾上的岗位（岗位卡片上的「加培训」） */
function openAttached(s, preset = []) {
  attDlg.value = { visible: true, editing: s, busy: false }
  attForm.value = s ? {
    kind: s.kind, name: s.name, location: s.location, note: s.note || '',
    start_time: new Date(s.start_time.replace(' ', 'T')),
    end_time: new Date(s.end_time.replace(' ', 'T')),
    applies_to_positions: [...s.applies_to_positions],
    hours_mode: s.hours_mode,
  } : { kind: 'rehearsal', name: '', location: '', note: '', start_time: '', end_time: '',
        applies_to_positions: [...preset], hours_mode: 'fixed' }
}

async function saveAttached() {
  const f = attForm.value
  if (!f.name?.trim()) return ElMessage.warning('请填写名称')
  if (!f.start_time || !f.end_time) return ElMessage.warning('请选择起止时间')
  attDlg.value.busy = true
  try {
    const body = {
      name: f.name, kind: f.kind, location: f.location, note: f.note || '',
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

// 时长表里志愿者本人那一格：确认了没、哪部分记志愿时长、哪部分按劳务
const VolunteerSide = (props) => {
  const r = props.row
  if (!r.volunteer_confirmed_at) return h('span', { class: 'dim' }, '未表态')
  return h(ElTag, { size: 'small', effect: 'plain',
                    type: r.pay_hours > 0 ? 'warning' : 'success' },
           () => `已确认 · ${r.reward_label}`)
}

// —— 急招置顶 ——
const pinBusy = ref(false)
async function togglePin() {
  pinBusy.value = true
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/pin`,
                             { pinned: !act.value.pinned })
    ElMessage.success(d.pinned ? '已置顶，广场上会标「急招」，报名截止时自动取消' : '已取消置顶')
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
  // base 同二维码：文案里的报名链接用管理者此刻打开的站点地址
  const d = await api.get(`/api/staff/activities/${activityId}/poster?base=${qrBase()}`)
  posterDlg.value = { visible: true, text: d.text }
}

/** 二维码存成 PNG：微信群里发不了 SVG。白底、600 像素见方，打印也够清楚。 */
async function downloadQrPng() {
  const svg = await (await fetch(`/api/activities/${activityId}/qr.svg?base=${qrBase()}`)).text()
  const img = new Image()
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
  await img.decode()
  const size = 600
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, size, size)
  ctx.drawImage(img, 0, 0, size, size)
  canvas.toBlob(blob => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${act.value.title}-报名二维码.png`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  }, 'image/png')
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

const materialMgr = ref(null)

/** 暂停 / 恢复一个场次或岗位的报名。暂停期间名单不动：不收新报名，候补也不顶上，
 *  恢复时按候补顺序补齐空位 */
async function togglePause(kind, item) {
  const paused = !item.enroll_paused
  const d = await api.post(`/api/staff/${kind}/${item.id}/pause`, { paused })
  reopenedMessage(d, paused ? `已暂停「${item.name}」的报名，期间候补不会顶上`
                            : `已恢复「${item.name}」的报名`)
  loadAll()
}

/** 锁定 / 解锁一个场次或岗位的名单。锁定前说清楚它意味着什么 */
async function toggleLock(kind, item, locked) {
  if (locked) {
    try {
      await ElMessageBox.confirm(
        '锁定后志愿者名单视为基本敲定：不再接受新报名，停止候补自动转正，此时退出的志愿者会被记录；如需补人请在志愿者管理页面手动转正。',
        `锁定「${item.name}」的名单`, { confirmButtonText: '锁定', type: 'warning' })
    } catch { return }
  }
  const d = await api.post(`/api/staff/${kind}/${item.id}/lock`, { locked })
  reopenedMessage(d, locked ? `已锁定「${item.name}」的名单` : `已解锁「${item.name}」的名单`)
  loadAll()
}

/** 解除暂停或锁定之后：补上了几位候补，或者为什么还没补 */
function reopenedMessage(d, done) {
  if (d.hint) ElMessage.warning(`${done}。${d.hint}`)
  else ElMessage.success(d.promoted ? `${done}，${d.promoted} 位候补已转为待确认` : done)
}

/** 「开放选岗」按钮为什么点不了；空串 = 可以点。候补这些页面上数不全的，交给后端判 */
function openPickBlock(g) {
  if (isOver.value) return '活动已结束'
  if (!realPositions(g).length) return '还没建岗位：先在本场次下建好岗位'
  if (g.unassigned_count) {
    return `待分配里还有 ${g.unassigned_count} 人，先一键分配或手动调岗`
  }
  return ''
}

/** 截止后统一分配的场次排完班，转成报名即选岗位，剩余名额让大家自己选 */
async function openPick(g) {
  await api.post(`/api/staff/shifts/${g.id}/open-pick`)
  ElMessage.success(`「${g.name}」已开放选岗，新报名的人按岗位名额自选`)
  loadAll()
}

function openStation(st, g) {
  positionDlg.value = { visible: true, editing: st, groupId: g.id, copyFrom: '', syncSame: false }
  stationForm.value = st ? {
    name: st.name, location: st.location, quota: st.quota,
    waitlist_quota: st.waitlist_quota, needs_leader: st.needs_leader,
    leader_task: st.leader_task || '',
    attire: st.attire || 'none', attire_note: st.attire_note || '',
    pay_optional: !!st.pay_optional, pay_note: st.pay_note || '',
    group_name: st.group_name || '',
    genderReq: st.requirement_tags?.性别?.[0] || '',   // 存的是 ["女"]，下拉是单选
    collegeReq: st.requirement_tags?.学院 || [],
    service_task: st.service_task, position_requirement: st.position_requirement || '',
    training_note: st.training_note,
  } : {
    name: '', location: '', quota: 2, waitlist_quota: 3, needs_leader: false,
    leader_task: '',
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
  if (f.needs_leader && !f.leader_task?.trim()) return ElMessage.warning('请填写组长任务')
  savingStation.value = true
  try {
    const tags = {}
    if (f.genderReq) tags['性别'] = f.genderReq
    if (f.collegeReq?.length) tags['学院'] = f.collegeReq
    const body = {
      name: f.name, quota: f.quota, location: f.location,
      waitlist_quota: f.waitlist_quota, needs_leader: f.needs_leader,
      leader_task: f.needs_leader ? f.leader_task : '',
      attire: f.attire, attire_note: f.attire === 'none' ? '' : f.attire_note,
      pay_optional: !!f.pay_optional, pay_note: f.pay_optional ? f.pay_note : '',
      group_name: f.group_name || '',
      requirement_tags: tags, service_task: f.service_task,
      position_requirement: f.position_requirement || '',
      training_note: f.training_note,
    }
    if (positionDlg.value.editing) {
      await api.patch(`/api/staff/positions/${positionDlg.value.editing.id}`, body)
      if (positionDlg.value.syncSame) {
        const shared = { ...body }        // 名额、候补名额是各时段自己的
        delete shared.quota
        delete shared.waitlist_quota
        for (const o of sameNameOthers.value) {
          await api.patch(`/api/staff/positions/${o.id}`, shared)
        }
      }
      ElMessage.success(positionDlg.value.syncSame
        ? `已保存，其他 ${sameNameOthers.value.length} 个时段一并修改` : '已保存')
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
  // 学院选项与招募范围同源：从已有用户档案汇总，不用 constants 里的演示清单
  const [c, cl] = await Promise.all([api.get('/api/auth/constants'),
                                     api.get('/api/staff/colleges')])
  modes.value = c.recruit_modes
  colleges.value = cl.colleges || []
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
// 排班表：活动概览、志愿者信息、每个场次一张名单（含签到状态），交付现场
function doExport() { dl(`/api/staff/activities/${activityId}/export.xlsx`) }
// 问卷结果：按场次、岗位挑人；有附件时再问要不要一起下附件包
const answersDlg = ref({ visible: false, shifts: [], positions: [] })
const answersShifts = computed(() => answersDlg.value.shifts.length
  ? shifts.value.filter(g => answersDlg.value.shifts.includes(g.id)) : shifts.value)
// 场次收窄后，不在这些场次里的岗位从已选里去掉，免得导出条件和眼前看到的不一致
watch(answersShifts, list => {
  const ok = new Set(list.flatMap(g => g.positions.map(p => p.id)))
  answersDlg.value.positions = answersDlg.value.positions.filter(id => ok.has(id))
})
function openAnswersExport() {
  answersDlg.value = { visible: true, shifts: [], positions: [] }
}
function doAnswersExport() {
  const { shifts: s, positions: p } = answersDlg.value
  const q = new URLSearchParams()
  if (s.length) q.set('shifts', s.join(','))
  if (p.length) q.set('positions', p.join(','))
  answersDlg.value.visible = false
  downloadExport(`/api/staff/activities/${activityId}/answers`, q.toString())
}
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
  await Promise.all([loadSubmitFiles(), loadDocTemplates()])
  // 青协的人建的活动已经按所在部门预填了归口，这里带出来，可以改
  submitDlg.value = { visible: true, deptId: act.value?.approval_dept_id || '',
                      note: '', autoPublish: true, busy: false }
}

// 对外联络部要立项书：选中它时在对话框里就地上传
const LIAISON_DEPT = '对外联络部'
const submitFiles = ref(null)
const submitToLiaison = computed(() =>
  depts.value.find(x => x.id === submitDlg.value.deptId)?.name === LIAISON_DEPT)
async function loadSubmitFiles() {
  submitFiles.value = await api.get(`/api/staff/activities/${activityId}/project-files`)
}

// 立项书、结项书的空白模板（外联部长上传），在要传文件的地方给下载链接
const docTemplates = ref(null)   // vuecheck: script-only，模板只通过 templates 读它
const templates = computed(() => Object.fromEntries(
  (docTemplates.value?.items || []).map(x => [x.slot, x.file])))
async function loadDocTemplates() {
  docTemplates.value = await api.get('/api/staff/doc-templates', { silent: true })
    .catch(() => null)
}

async function doSubmit() {
  const d = submitDlg.value
  if (!d.deptId) return ElMessage.warning('请选择审批部门')
  if (submitToLiaison.value && !submitFiles.value?.proposal) {
    return ElMessage.warning('提交对外联络部审批须先上传立项书')
  }
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

/** 恢复报名后补位时，有的场次正被抢报、锁一时排不上，那几场的候补没补上 */
function warnBusy(names) {
  if (!names?.length) return
  ElMessage({ type: 'warning', duration: 8000, showClose: true,
              message: `${names.map(n => `「${n}」`).join('、')}此刻报名的人多，`
                + '空出的名额没来得及给候补，请稍后到名单里手动转正' })
}

async function doReopen() {
  const d = reopenDlg.value
  if (!d.regEnd) return ElMessage.warning('请选择新的报名截止时间')
  d.busy = true
  try {
    const r = await api.post(`/api/staff/activities/${activityId}/reopen`,
                             { reg_end: toISO(d.regEnd) })
    ElMessage.success('已恢复报名'
      + (r.promoted ? `；已通知 ${r.promoted} 位候补确认转正` : ''))
    warnBusy(r.busy)
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

/** 名单导入改了人或数：时长页的总账跟着变，已经打开过的话刷一下。 */
function onRecordChanged() {
  if (hoursState.value?.items) hoursPreview(true)
}

/** 刷新这一页：时长本身与申诉列表一起拉，两边都会随志愿者的动作变。 */
async function refreshHours() {
  await Promise.all([hoursPreview(true), loadAppeals()])
}

// 加载时长预览（含手动添加的记录）；silent=true 时不弹「暂无签到数据」提示
async function hoursPreview(silent = false) {
  hoursLoading.value = true
  try {
    const d = await api.get(`/api/staff/activities/${activityId}/hours`)
    // 时长只读（见模板里的说明），勾选确认时按后端算出来的值提交
    hours.value = d.items || []
    hoursState.value = d
    hoursAt.value = Date.now()
    hoursSel.value = []
    if (!hours.value.length && !silent) ElMessage.info('暂无签到数据')
  } finally { hoursLoading.value = false }
}

function onHoursSelect(rows) { hoursSel.value = rows }

/** 改时长的接口回来后的提示：核准过的活动改了数会被撤销核准，要说清楚。 */
function hoursChanged(d, text) {
  if (d.reapproval_required) {
    ElMessage.warning(`${text}；原时长核准已撤销，请重新核准`)
  } else {
    ElMessage.success(text)
  }
}

// 确认选中志愿者的时长
async function confirmSelectedHours() {
  if (!hoursSel.value.length) return ElMessage.warning('请先勾选志愿者')
  // 按后端算出来的数值确认：勾选的意义是
  // 「只确认这几个人」，比如把未签退的几条留着逐条核对
  const items = hoursSel.value.map(r => ({
    student_id: r.user.student_id, hours: Number(r.hours),
  }))
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/hours/confirm`, { items })
    hoursChanged(d, `已确认 ${d.count} 名志愿者的时长`)
    hoursPreview(true)
  } catch { /* toast by api */ }
}

// 确认全部志愿者时长
async function confirmAllHours() {
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/hours/confirm`, {})
    if (d.reapproval_required) ElMessage.warning(d.message)
    else ElMessage.success(d.message || `已确认 ${d.count} 名志愿者的时长`)
    hoursPreview(true)
  } catch { /* toast by api */ }
}

// 手动添加时长（不依赖签到，如签到漏扫、中途离场）。批量：一次给多人写同一条时长
async function addManualHours() {
  const { userIds, sign, hours, note, date } = mh.value
  if (!userIds.length) return ElMessage.warning('请先选择志愿者')
  if (!note.trim()) return ElMessage.warning('备注必填：写清楚为什么加、为什么扣')
  if (!date) return ElMessage.warning('请选择服务日期：这段时长算哪一天的服务')
  // 扣减就是同一个接口传负数——后台按正负分「加」和「扣」，台账上是一段
  const value = sign === 'deduct' ? -Math.abs(hours) : Math.abs(hours)
  try {
    const d = await api.post(`/api/staff/activities/${activityId}/hours/manual`,
      { items: userIds.map(sid => ({ student_id: sid, hours: value, note })), date })
    hoursChanged(d, `已为 ${d.count} 位志愿者${sign === 'deduct' ? '扣减' : '添加'}时长`)
    // 日期留着：一场活动的时长常是连着录几条，每来一条都重挑一次日历是白费事
    mh.value = { userIds: [], sign: 'add', hours: 0.5, note: '', date }
    hoursPreview(true)
  } catch { /* toast by api */ }
}

/** 删掉手动加的那一段（不是整笔账）。按段编号删，不按它在明细里排第几：
 *  这张明细是现拼的，和库里那一笔的顺序不一定一样（见后端 hours_manual_delete）。 */
async function delManualPart(row, part) {
  if (!row.record_id) return ElMessage.warning('这一行还没有时长记录，无法删除')
  const d = await api.del(
    `/api/staff/activities/${activityId}/hours/manual/${row.record_id}/${part.id}`)
  hoursChanged(d, '已删除该段时长')
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

// 时长要由校青协审核过才算数：提交给归口部长，通过之后才锁定。
// 志愿北京活动名称一并填（预先填上之前存过的），锁定后推给对外联络部核对
// 时长审核要附的两份盖章材料；template 是对应空白模板的格子名
const STAMPED_SLOTS = [{ kind: 'stamped', label: '盖章立项书', template: 'proposal' },
                       { kind: 'closing', label: '盖章结项书', template: 'closing' }]
const lockFiles = ref(null)
async function loadLockFiles() {
  lockFiles.value = await api.get(`/api/staff/activities/${activityId}/project-files`)
}
// 对话框里传了文件，页面上「还缺哪几份」的提示也跟着刷新
function onLockFileChanged() {
  loadLockFiles()
  hoursPreview(true)
}

async function submitLockNow() {
  const s = hoursState.value
  await Promise.all([loadLockFiles(), loadDocTemplates()])
  lockDlg.value = {
    visible: true, busy: false, note: '',
    // 申诉期没过就提交，等于把还没表态的人的申诉机会一起交掉了：说清楚再让他点
    early: !!s.appeal_deadline && new Date(s.appeal_deadline) > new Date(),
    names: [...(s.vb_names || [])],
  }
}
async function doSubmitLock() {
  const d = lockDlg.value
  const missing = lockFiles.value?.external
    ? STAMPED_SLOTS.filter(s => !lockFiles.value[s.kind]).map(s => s.label) : []
  if (missing.length) return ElMessage.warning(`请先上传${missing.join('和')}`)
  if (!d.names.length) return ElMessage.warning('请填写志愿北京上的活动名称（至少一个）')
  d.busy = true
  try {
    await api.post(`/api/staff/activities/${activityId}/hours/lock-request`,
                   { vb_names: d.names, note: d.note.trim() })
    ElMessage.success('已提交，等校青协审核')
    d.visible = false
    hoursPreview(true)
    loadAll()
  } finally { d.busy = false }
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
  rosterAt.value = g._rosterAt
  moveSel.value = {}
}

/** 重新拉当前场次的名单。loadRoster 只是把缓存搬到视图上，不发请求。 */
async function refreshRoster() {
  const g = currentShift.value
  if (!g) return
  g._roster = await api.get(`/api/staff/shifts/${g.id}/roster`)
  g._rosterAt = Date.now()
  loadRoster()
}

// 只有「岗位设了组长」的人才能被指定；没岗位的人后端也会拒
function canBeLeader(row) {
  const p = roster.value?.positions?.find(x => x.id === row.position_id)
  return !!p?.needs_leader
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
  mgrCandidates.value = (await api.get(
    `/api/staff/activities/${activityId}/manager-candidates`)).groups
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
// 每次切回概览都重拉：在名单、签到、时长页里办完事，回来看到的是新数。
// 第一次进入时组件自己在挂载时拉
const overviewCard = ref(null)
watch(tab, async t => {
  if (t === 'overview') overviewCard.value?.load()
  if (t === 'hours') await refreshHours()
  if (t === 'acks') await loadBatches()
  if (t === 'logs') {
    // 变更记录是给「翻旧账」用的，多数人从不点开；进活动就拉 50 条，
    // 等于替没打开的页签白干一趟
    loadLogs()
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
/* 手机版时长卡片里的一行：标签 + 控件 */
.mh-row { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
/* 按岗位看：一个时段一行 */
.slot-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
            padding: 8px 0; border-top: 1px dashed #eceef1; }
.slot-time { min-width: 0; flex: 1 1 200px; }
.slot-acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
/* 培训与彩排：一场一行 */
.att-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
           padding: 8px 0; border-bottom: 1px dashed #eceef1; }
.att-quick { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
/* 名单标题行：左边标题，右边一排动作；窄屏各占一行 */
.roster-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.roster-actions { display: inline-flex; gap: 8px; align-items: center; flex-wrap: wrap; }
/* 两个条件选择框并排占满一行；窄屏自动叠成两行 */
.two-col { display: flex; gap: 8px; width: 100%; flex-wrap: wrap; }
.two-col > * { flex: 1 1 180px; min-width: 0; }
/* 招募弹窗：宽屏文案与二维码并排，窄屏竖排 */
.recruit { display: flex; gap: 16px; align-items: flex-start; }
.recruit-text { flex: 1; min-width: 0; }
.recruit-qr { width: 210px; flex-shrink: 0; text-align: center; }
.recruit-qr img { border: 1px solid #eceef1; border-radius: 8px; }
@media (max-width: 860px) {
  .recruit { flex-direction: column; }
  .recruit-qr { width: 100%; }
}

/* 页头两行：第一行活动名与状态，第二行时间与地点。挤在一行里时，长活动名会把
   状态、时间地点一起往右推，整块跟着溢出去 */
.hd-title { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.hd-meta { margin-top: 4px; }

.needs-positions { border-color: var(--el-color-danger) !important; }
.group-card { border:1px solid #eceef1; border-radius:12px; padding:12px 14px; margin-bottom:12px; background:#fff; }
.g-head { display:flex; gap:12px; align-items:center; margin-bottom:8px; font-size:13.5px; flex-wrap:wrap;}
.side-stack { display: flex; flex-direction: column; gap: 12px; }
.side-stack .section-title { margin-top: 0; }
.wl-card { border-radius:12px; }
.wl-row { display:flex; gap:8px; align-items:center; padding:3px 0; font-size:13px; }
button.dashed { border-style:dashed; }
.contacts-box { width: 100%; }
.contact-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
.qr-preview { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.qr-preview img { max-width: 160px; max-height: 160px; border-radius: 8px; border: 1px solid #e4e7ed; }
</style>
