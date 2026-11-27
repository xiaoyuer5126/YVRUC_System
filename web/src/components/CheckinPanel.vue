<template>
  <div v-if="boardShift">
    <div class="toolbar">
      <el-button type="primary" :disabled="over" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;生成签到码
      </el-button>
      <el-button :disabled="over" @click="openReminder">
        <el-icon><Bell /></el-icon>&nbsp;补发签到提醒
      </el-button>
      <span class="dim">
        {{ hoursFrozen ? '时长已封账，签到记录不再改动'
           : over ? '活动已结束，不再发码；漏签的人仍可在下方手动补签'
           : '同一个码：首次扫码签到，再次扫码签退' }}
      </span>
    </div>

    <!-- 标题是这张码管的签到对象；培训/彩排标在旁边 -->
    <DataCards v-if="narrow" :rows="tasks" title-key="target_name"
               style="margin-bottom:16px" :fields="[]" empty="还没有签到码">
      <template #extra="{ row }">
        <el-tag v-for="k in kindTags(row)" :key="k" size="small" effect="plain">{{ k }}</el-tag>
        <el-tag size="small" :type="row.active ? 'success' : 'info'">
          {{ row.active ? '开启' : '已关闭' }}
        </el-tag>
      </template>
      <template #body="{ row }">
        <div v-if="row.period_count > 1" class="dim">{{ row.period_text }}</div>
        <div class="dim">
          <template v-if="coversBoard(row)">当前大屏 · </template>
          {{ fmtDT(row.valid_from) }} ~ {{ fmtDT(row.valid_to) }} · 已签 {{ row.count }} 人
        </div>
        <div v-if="row.window_varies" class="dim">可签时段各自按自己的时段算</div>
        <div class="dim">
          {{ row.geo ? `定位签到：半径 ${row.geo.radius_m} 米`
                     : '扫码签到：到场出示二维码' }}
        </div>
      </template>
      <template #actions="{ row }">
        <el-button size="small" @click="openQR(row)">
          {{ row.geo ? '大屏' : '二维码 / 大屏' }}
        </el-button>
        <el-button size="small" :disabled="over" @click="openGeo(row)">定位</el-button>
        <el-button size="small" :disabled="over" @click="openWindow(row)">改时段</el-button>
        <el-button size="small" :type="row.active ? 'danger' : 'success'" link
                   :disabled="over" @click="toggle(row)">{{ row.active ? '关闭' : '开启' }}</el-button>
      </template>
    </DataCards>
    <el-table v-else :data="tasks" size="small" style="margin-bottom:16px">
      <!-- 不列码：列出来就能被念给群里、抄进聊天框，代签的门槛只剩一张嘴。
           要看码只有一条路——打开二维码弹窗，把那一屏亮在来签到的人面前。
           这张表是整个活动的签到码，所以每张码管哪些签到对象要单独成列 -->
      <el-table-column label="签到对象" min-width="220">
        <template #default="{row}">
          {{ row.target_name }}
          <div v-if="row.period_count > 1" class="dim">{{ row.period_text }}</div>
          <el-tag v-for="k in kindTags(row)" :key="k" size="small" effect="plain">{{ k }}</el-tag>
          <el-tag v-if="coversBoard(row)" size="small" type="success"
                  effect="plain">当前大屏</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="有效时段" min-width="180">
        <template #default="{row}">
          {{ fmtDT(row.valid_from) }} ~ {{ fmtDT(row.valid_to) }}
          <!-- 各岗位按自己那一段算时，这一栏是整张码的范围，不是谁都能
               从头签到尾 -->
          <div v-if="row.window_varies" class="dim">各自按自己的时段算</div>
        </template>
      </el-table-column>
      <el-table-column label="已签/应签" width="100">
        <template #default="{row}">{{ row.count }} 人</template>
      </el-table-column>
      <el-table-column label="方式" width="130">
        <template #default="{row}">
          <!-- 一个任务要么扫码要么定位：真两样都开，定位那道「人在不在场」的
               判定就成了摆设，谁都能挑松的那条走 -->
          <el-tag v-if="row.geo" size="small" effect="plain">定位 · {{ row.geo.radius_m }}m</el-tag>
          <el-tag v-else size="small">扫码</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="360">
        <template #default="{row}">
          <div class="btn-row">
            <el-button size="small" @click="openQR(row)">
              {{ row.geo ? '大屏' : '二维码 / 大屏' }}
            </el-button>
            <el-button size="small" :disabled="over" @click="openGeo(row)">定位</el-button>
            <!-- 现场最常见的意外是场次提前或拖堂：关掉重建会让已经扫过的那张码
                 作废，所以给「改时段」 -->
            <el-button size="small" :disabled="over" @click="openWindow(row)">改时段</el-button>
            <!-- 确认放在 toggle 里：关码的后果要写清楚（没签退的人按场次结束
                 兜底），窄屏卡片那边也走同一个确认 -->
            <el-button size="small" :type="row.active ? 'danger' : 'success'" link
                       :disabled="over" @click="toggle(row)">
              {{ row.active ? '关闭' : '开启' }}
            </el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 这一行只影响下面的大屏（到岗看板与到场名单），跟上面的签到码列表无关
         ——签到码是整个活动的，列的是每一场。所以排在列表之后、大屏之前。
         本页自己管场次，不跟着「志愿者管理」走：那边在排人，这边在盯现场，
         两件事同时在做，共用一个选择等于互相打架。 -->
    <div class="toolbar">
      <span>大屏显示</span>
      <el-select v-model="boardShiftId" style="width:280px" @change="onSwitchShift">
        <el-option v-for="g in shiftOptions" :key="g.id" :value="g.id"
                   :label="`${g.name}（${g.time_text}）`">
          <span>{{ g.name }}</span>
          <el-tag v-if="g.kind !== 'service'" size="small" effect="plain"
                  style="margin-left:8px">{{ g.kind_label }}</el-tag>
          <span class="dim" style="margin-left:8px">{{ g.time_text }}</span>
        </el-option>
      </el-select>
      <el-select v-if="(boardShift?.periods || []).length > 1"
                 v-model="boardPeriodId" style="width:240px"
                 @change="onSwitchBoard">
        <el-option v-for="p in boardShift.periods" :key="p.id" :value="p.id"
                   :label="p.text" />
      </el-select>
      <!-- 刷新条放在这里而不是看板里：换场次时看板会整块卸载重建，
           待在里面的话组件跟着重来一次，「更新于」被清空 -->
      <RefreshBar ref="boardRefresh" :load="loadBoard" :at="boardAt" />
    </div>
    <!-- 固定计时的培训/彩排也要扫码：不扫就没有到场记录，也就没有时长 -->
    <div v-if="boardShift?.kind !== 'service' && boardShift?.hours_mode === 'fixed'"
         class="dim" style="margin-top:8px">
      志愿者扫码签到，不要求签退，时长直接按本场时长计。
    </div>

    <!-- 到岗看板：按场次拉取，不依赖激活任务 -->
    <template v-if="board">
      <div class="section-title">{{ board.shift_name }} · 到岗情况
        <el-tag v-if="board.kind !== 'service'" size="small" effect="plain">
          {{ board.kind_label }}
        </el-tag>
      </div>
      <el-row :gutter="16">
        <el-col :span="6">
          <el-card shadow="never" class="big-card">
            <div class="ratio"><b>{{ board.checked }}</b> / {{ board.expected }}</div>
            <div class="dim">已到岗 / 应到岗</div>
            <div class="dim" style="margin-top:4px">已签退 {{ board.checked_out }} 人</div>
            <div v-if="board.excused" class="dim">已请假 {{ board.excused }} 人（不计入应到岗）</div>
            <!-- 时间灵活的场次一张码一轮：到岗、签退数的是开着的那张码这一轮 -->
            <div v-if="board.flexible_time" class="dim">时间灵活：按当前开着的签到码这一轮统计</div>
            <el-progress type="circle" :percentage="pct" :width="86"
                         :color="pct === 100 ? '#67c23a' : '#ae0b2a'" style="margin-top:8px" />
          </el-card>
        </el-col>
        <el-col :span="18">
          <el-card shadow="never">
            <div class="st-grid">
              <!-- 分母是分到这个岗位的人数，不是名额：名额 5、只排了 2 个人时
                   写「0/5 到岗」会像缺 5 个，其实是名额没招满。名额另说一行 -->
              <div v-for="s in board.positions" :key="s.id" class="st-box">
                <b>{{ s.name }}</b>
                <div class="dim">
                  {{ s.checked }}/{{ s.expected }} 到岗
                  <template v-if="s.quota !== s.expected">· 名额 {{ s.quota }}</template>
                </div>
                <el-progress :percentage="Math.min(100, Math.round(s.checked / Math.max(1, s.expected) * 100))"
                             :stroke-width="7" :show-text="false"
                             :color="s.checked >= s.expected ? '#67c23a' : '#ae0b2a'" />
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <div class="section-title">到场名单（含未签到者，勾选后可批量操作）</div>
      <el-card shadow="never">
        <div class="manual-bar">
          <el-input v-model="manualSid" placeholder="输入学号单个操作" style="width:180px"
                    :disabled="!batchTaskId || hoursFrozen" />
          <el-button :disabled="!batchTaskId || hoursFrozen" @click="quick('checkin')">签到</el-button>
          <el-button :disabled="!batchTaskId || hoursFrozen" @click="quick('checkout')">签退</el-button>
          <el-divider direction="vertical" />
          <template v-if="selected.length">
            <span class="dim">已选 {{ selected.length }} 人：</span>
            <el-button type="primary" :disabled="!batchTaskId || hoursFrozen"
                       :loading="batchBusy === 'checkin'"
                       @click="batch('checkin')">批量签到</el-button>
            <el-button type="warning" :disabled="!batchTaskId || hoursFrozen"
                       :loading="batchBusy === 'checkout'"
                       @click="batch('checkout')">批量签退</el-button>
            <el-button :disabled="!batchTaskId || hoursFrozen"
                       :loading="batchBusy === 'cancel_checkout'"
                       @click="batch('cancel_checkout')">取消签退</el-button>
            <el-button type="danger" plain :disabled="!batchTaskId || hoursFrozen"
                       :loading="batchBusy === 'cancel_checkin'"
                       @click="batch('cancel_checkin')">取消签到</el-button>
            <el-tag v-if="!batchTaskId" size="small" type="warning" effect="plain">请先生成并开启签到码</el-tag>
          </template>
          <template v-else>
            <el-tag v-if="!batchTaskId" size="small" type="warning" effect="plain">请先生成并开启签到码</el-tag>
          </template>
        </div>
        <DataCards v-if="narrow" :rows="board.records" title-key="name"
               row-key="student_id" :selectable="!!batchTaskId && !hoursFrozen"
               @selection-change="onSelect"
               :fields="[{ key: 'phone', label: '手机号' },
                         { key: 'wechat', label: '微信号' },
                         { key: 'position', label: '岗位' },
                         { key: 'time', label: '签到', hideEmpty: true },
                         { key: 'checkout_at', label: '签退', hideEmpty: true }]"
               empty="名单为空">
      <template #extra="{ row }">
        <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">组长</el-tag>
        <el-tag v-if="board.flexible_time && row.checkin_count" size="small" effect="plain">
          共签到 {{ row.checkin_count }} 次
        </el-tag>
        <el-tag size="small"
                :type="row.checked_in ? 'success' : (row.excused ? 'info' : 'danger')"
                :effect="row.checked_in ? 'light' : 'plain'">
          {{ row.checked_in ? (row.checkout_at ? '已签退' : '已到岗')
             : (row.excused ? '已请假' : '未签到') }}
        </el-tag>
      </template>
    </DataCards>
    <el-table v-else :data="board.records" size="small" max-height="320"
                  :row-class-name="rowClass" @selection-change="onSelect">
          <el-table-column type="selection" width="40" />
          <el-table-column label="签到" width="90">
            <template #default="{row}">{{ row.time || '—' }}</template>
          </el-table-column>
          <el-table-column label="签退" width="90">
            <template #default="{row}">
              {{ row.checkout_at || '—' }}
            </template>
          </el-table-column>
          <el-table-column label="姓名" width="120">
            <template #default="{row}">
              {{ row.name }}
              <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark"
                      style="margin-left:4px">组长</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="手机号" width="140">
            <template #default="{row}">{{ row.phone || '—' }}</template>
          </el-table-column>
          <el-table-column label="微信号" width="160">
            <template #default="{row}">{{ row.wechat || '—' }}</template>
          </el-table-column>
          <el-table-column prop="position" label="岗位" min-width="120" />
          <el-table-column v-if="board.flexible_time" label="共签到" width="80">
            <template #default="{row}">{{ row.checkin_count }} 次</template>
          </el-table-column>
          <el-table-column label="方式" width="130">
            <template #default="{row}">
              <el-tag v-if="row.excused && !row.checked_in" size="small"
                      type="info" effect="plain">已请假</el-tag>
              <el-tag v-else-if="!row.checked_in" size="small" type="info" effect="plain">未签到</el-tag>
              <template v-else>
                <el-tag size="small" :effect="row.method === 'manual' ? 'plain' : 'light'">
                  {{ METHOD_LABEL[row.method] || row.method }}
                </el-tag>
                <!-- 只标例外：扫码签退是常态，每条都挂个「扫码退」只是噪音 -->
                <el-tag v-if="row.checkout_method === 'manual' || row.checkout_method === 'geo'"
                        size="small" effect="plain" style="margin-left:4px">
                  {{ row.checkout_method === 'geo' ? '定位退' : '手动退' }}
                </el-tag>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <!-- 生成签到码：一张码只管一个签到对象（2026-11-27 马霄宇定：可签时段每张码
         各调各的）。即选岗位一个一张；统一分配的一批整批一张（还没分到岗位的人
         也在里面）；培训/彩排一项一张 -->
    <el-dialog v-model="creating" title="生成签到码" :width="narrow ? '94%' : '520px'"
               :before-close="guardCreate" @opened="guardCreate.snap">
      <el-form label-width="90px">
        <el-form-item label="签到对象" required>
          <el-select v-model="form.target" filterable style="width:100%"
                     placeholder="选一个岗位、统一分配的一批或培训/彩排"
                     @change="onPickTarget">
            <el-option-group v-for="grp in targetGroups" :key="grp.label" :label="grp.label">
              <el-option v-for="o in grp.options" :key="o.value" :value="o.value"
                         :label="o.name">
                <span>{{ o.name }}</span>
                <el-tag v-if="o.kindLabel" size="small" effect="plain"
                        style="margin-left:8px">{{ o.kindLabel }}</el-tag>
                <span class="dim" style="margin-left:8px">{{ o.time }}</span>
              </el-option>
            </el-option-group>
          </el-select>
          <div class="dim">一张码管一项；统一分配的一批整批签，还没分到岗位的人也能扫。</div>
        </el-form-item>
        <!-- 多时段的：这一天开始的那一段才是这张码管的 -->
        <el-form-item v-if="needDate" label="签到日期" required>
          <el-select v-model="form.date" style="width:100%" @change="fillRange">
            <el-option v-for="d in dateOptions" :key="d" :value="d" :label="d" />
          </el-select>
        </el-form-item>
        <el-form-item label="可签时段">
          <div class="two-col">
            <DateTimeField :model-value="form.range[0]" placeholder="开放签到"
                           @update:model-value="setWindowStart" />
            <DateTimeField :model-value="form.range[1]" placeholder="停止签到"
                           @update:model-value="setWindowEnd" />
          </div>
          <div class="dim">
            默认是所选这一项的时段起止各放宽 {{ WINDOW_PAD }} 分钟，可以改。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="creating = false">取消</el-button>
        <el-button type="primary" :loading="genBusy" @click="createTask">生成</el-button>
      </template>
    </el-dialog>

    <!-- 二维码 -->
    <el-dialog v-model="qrVisible" title="现场签到二维码" width="380">
      <div style="text-align:center" v-if="qrTask">
        <!-- 定位签到的任务没有可扫的码：这一屏显示在墙上也没人能扫。
             说清楚该干什么，别让现场的人对着一块空屏瞎猜 -->
        <el-alert v-if="qrTask.geo" type="info" :closable="false" style="text-align:left">
          <p style="margin:0;line-height:1.6">
            这张码用<b>定位签到</b>，不需要二维码。<br>
            志愿者到场后在「我的报名」里点「签到」，平台比对距离
            （半径 {{ qrTask.geo.radius_m }} 米）。<br>
            定位取不到时，由负责人在下面的名单里手动补签。
          </p>
        </el-alert>
        <!-- 默认动态码：固定码能打印，可也意味着拍一张发进群里就能代签，要打印时手动切 -->
        <el-radio-group v-show="!qrTask.geo" v-model="qrMode" size="small"
                        style="margin-bottom:12px">
          <el-radio-button value="dynamic">动态码</el-radio-button>
          <el-radio-button value="fixed">固定码</el-radio-button>
        </el-radio-group>
        <!-- 只出二维码，不出那串字符，也不出扫码链接：能照着念、能转发的码，
             等于谁都能替别人签。要给人扫，就把这一屏亮在他面前。
             必须带 base：不传的话码里编的是相对路径，相机只会当一行字显示 -->
        <template v-if="!qrTask.geo">
          <img v-if="qrSrc" :src="qrSrc" width="280" height="280" alt="签到二维码" />
          <div v-else class="qr-blank">正在取码…</div>
          <!-- 这张码管谁：打印出来贴到现场，光看码分不出 -->
          <div class="qr-who">
            <b>{{ qrTask.target_name }}</b>
            <el-tag v-for="k in kindTags(qrTask)" :key="k" size="small" effect="plain">{{ k }}</el-tag>
          </div>
          <div class="dim">可签 {{ fmtDT(qrTask.valid_from) }} ~ {{ fmtDT(qrTask.valid_to) }}</div>
          <!-- 一张码管几个岗位、各按自己那一段算时，这一行是整张码的范围：
               现场要知道「早到的那个岗位还签不了」不是码坏了 -->
          <div v-if="qrTask.window_varies" class="dim">
            各自按自己的时段算，上面是整张码的范围
          </div>
          <div class="dim">首次扫码签到，再次扫码签退</div>
          <template v-if="isDynamic">
            <!-- 数字在动，就说明这块屏还活着：现场最怕的是分不清「码没换」和「页面卡了」 -->
            <el-tag size="small" :type="qrStale ? 'danger' : 'success'" effect="plain"
                    style="margin-top:8px">
              {{ qrStale ? '取不到新码，正在重试…' : `${qrSeconds} 秒后换下一张` }}
            </el-tag>
            <div v-if="qrStale" class="dim">屏幕上这张可能已经过期，先别让人扫</div>
            <div v-else class="dim">每 {{ qrRefresh }} 秒换一张，上一张还能再用一会儿；一块屏给所有人扫</div>
          </template>
          <div v-else class="dim" style="margin-top:8px">
            固定码从头用到尾，可以右键存图打印贴在现场——也因此别发进群里
          </div>
        </template>
        <div v-else class="dim" style="margin-top:12px">
          {{ qrTask.target_name }}
          · 可签 {{ fmtDT(qrTask.valid_from) }} ~ {{ fmtDT(qrTask.valid_to) }}
        </div>
      </div>
    </el-dialog>

    <!-- 改可签时段：场次提前或拖堂时用，不必关掉重建（重建会让已扫过的码作废） -->
    <el-dialog v-model="winDlg.visible" title="改可签时段" width="440"
               :before-close="guardWin" @opened="guardWin.snap">
      <p class="dim" style="margin:0 0 12px">
        {{ winDlg.task?.target_name }}。改时段不影响已经扫过的记录，码也还是同一张。
        <template v-if="(winDlg.task?.members || []).length > 1">
          这张码管 {{ winDlg.task.members.length }} 项，保存后整张码统一按这个时段算。
        </template>
      </p>
      <div class="two-col">
        <DateTimeField :model-value="winDlg.range[0]" placeholder="开放签到"
                       @update:model-value="v => winDlg.range[0] = v" />
        <DateTimeField :model-value="winDlg.range[1]" placeholder="停止签到"
                       @update:model-value="v => winDlg.range[1] = v" />
      </div>
      <template #footer>
        <el-button @click="winDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="winDlg.busy" @click="saveWindow">保存</el-button>
      </template>
    </el-dialog>

    <!-- 定位签到：坐标由管理者站在岗位上量 -->
    <el-dialog v-model="geoDlg" title="定位签到" width="440"
               :before-close="guardGeo" @opened="guardGeo.snap">
      <template v-if="geoTask">
        <el-form label-width="90px">
          <el-form-item label="签到地点">
            <GeoPicker v-model="geoForm" :radius="geoForm.radius_m" style="width:100%" />
          </el-form-item>
          <el-form-item label="签到范围">
            <el-input-number v-model="geoForm.radius_m" :min="30" :max="3000"
                             :step="50" />
            <span class="dim" style="margin-left:8px">米</span>
            <div class="dim">
              200 米大约是一栋楼、一个广场；范围越大越好签，也越不说明人到没到
            </div>
          </el-form-item>
        </el-form>
      </template>
      <template #footer>
        <el-button v-if="geoTask?.geo" type="danger" plain :loading="geoBusy"
                   @click="clearGeo">改回扫码</el-button>
        <el-button @click="geoDlg = false">取消</el-button>
        <el-button type="primary" :loading="geoBusy" @click="saveGeo">保存</el-button>
      </template>
    </el-dialog>
  </div>

    <!-- 补发签到提醒：先选范围再发，别在下拉里盲点 -->
    <el-dialog v-model="reminderDlg" title="补发签到提醒" width="460"
               :before-close="guardReminder" @opened="guardReminder.snap">
      <p class="dim">
        只发给上面这一段里<b>未签到</b>的正式志愿者，可以只催某几个岗位。
        这一段开始前 30 分钟系统已自动发送过一次。
      </p>
      <el-form label-width="72px">
        <el-form-item label="范围">
          <el-radio-group v-model="reminderScope">
            <el-radio value="all">不限岗位</el-radio>
            <el-radio v-if="positionGroups.length" value="group">按岗位组</el-radio>
            <el-radio value="position">按岗位</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="reminderScope === 'group'" label="岗位组" required>
          <el-select v-model="reminderGroup" placeholder="选择岗位组" style="width:100%">
            <el-option v-for="gn in positionGroups" :key="gn" :value="gn" :label="gn" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="reminderScope === 'position'" label="岗位" required>
          <el-select v-model="reminderPositions" multiple placeholder="可多选"
                     style="width:100%">
            <el-option v-for="p in boardShift?.positions || []" :key="p.id" :value="p.id"
                       :label="p.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="签收">
          <el-checkbox v-model="reminderAck">要求志愿者点「我已知悉」</el-checkbox>
          <div class="dim">勾选后可在「签收情况」查看并一键提醒。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reminderDlg = false">取消</el-button>
        <el-button type="primary" :loading="reminding" @click="sendReminder">
          发送
        </el-button>
      </template>
    </el-dialog>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtDT, qrBase } from '../api'
import { useNarrow } from '../composables/useNarrow'
import { useCloseGuard } from '../composables/closeGuard'
import DataCards from './DataCards.vue'
import DateTimeField from './DateTimeField.vue'
import GeoPicker from './GeoPicker.vue'
import RefreshBar from './RefreshBar.vue'


const props = defineProps({
  activityId: { type: String, required: true },
  allShifts: { type: Array, required: true },  // 主场次 + 培训/彩排
  // 活动已结束：不再发新的签到码，也不催人签到——没人再扫了
  over: { type: Boolean, default: false },
  // 时长已封账（锁定或正在审核）：连手动补签也停，签到记录是算时长的依据
  hoursFrozen: { type: Boolean, default: false },
})

// 与后端 config.CHECKIN_WINDOW_PAD_MINUTES 保持一致
const WINDOW_PAD = 30

// 签到记录的 method：scan 扫码 / geo 定位 / manual 管理员补签（见 CheckinRecord）
const METHOD_LABEL = { scan: '扫码', geo: '定位', manual: '补签' }
const actId = computed(() => props.activityId)   // vuecheck: script-only

const creating = ref(false)
const genBusy = ref(false)
const batchBusy = ref('')
// target 是 "p:岗位 id"（即选岗位）或 "s:组 id"（统一分配的一批、培训/彩排，整组签）
const form = ref({ target: '', date: '', range: [] })
const guardCreate = useCloseGuard(() => form.value)
const guardWin = useCloseGuard(() => winDlg.value.range)
const guardGeo = useCloseGuard(() => geoForm.value)
const guardReminder = useCloseGuard(() => [reminderScope.value, reminderGroup.value, reminderPositions.value, reminderAck.value])

const shiftOptions = computed(() => props.allShifts)

/** 即选岗位的真实岗位。占位岗位「待分配」不是岗位，是「这一批还没分岗」。 */
function realPositions(g) {
  return (g.positions || []).filter(p => !p.is_placeholder)
}

/** 签到对象分三组列：即选岗位、报名后统一分配的批次、培训/彩排，各标时段。 */
const targetGroups = computed(() => {
  const pick = []
  const batch = []
  const train = []
  for (const g of shiftOptions.value) {
    if (g.kind !== 'service') {
      train.push({ value: `s:${g.id}`, shift: g, name: g.name,
                   kindLabel: g.kind_label, time: g.time_text })
    } else if (g.mode === 'assign') {
      batch.push({ value: `s:${g.id}`, shift: g, name: g.name, time: g.time_text })
    } else {
      for (const p of realPositions(g)) {
        pick.push({ value: `p:${p.id}`, shift: g, name: p.name,
                    time: p.time_text || g.time_text })
      }
    }
  }
  return [
    { label: '岗位', options: pick },
    { label: '报名后统一分配（还没分到岗位的人也能签）', options: batch },
    { label: '培训 / 彩排', options: train },
  ].filter(grp => grp.options.length)
})

/** 选中的对象所在那一组时段：日期选项、默认可签时段、截止提醒都看它。 */
const pickedShift = computed(() => {   // vuecheck: script-only，供下面几处用
  for (const grp of targetGroups.value) {
    for (const o of grp.options) if (o.value === form.value.target) return o.shift
  }
  return null
})

// 多时段的必须说清是哪一天那一段：后端定不出来会 400
const needDate = computed(() => (pickedShift.value?.periods || []).length > 1)

const dateOptions = computed(() => [...new Set((pickedShift.value?.periods || [])
  .map(p => String(p.start_time).slice(0, 10)))].sort())

/** 这张码管的那一段：单时段就是它；多时段按选的日期，当天只有一段才定得下。 */
const pickedPeriod = computed(() => {   // vuecheck: script-only
  const periods = pickedShift.value?.periods || []
  if (periods.length <= 1) return periods[0] || null
  const day = periods.filter(p => String(p.start_time).slice(0, 10) === form.value.date)
  return day.length === 1 ? day[0] : null
})

// 本页自己持有的场次选择，与「志愿者管理」互不影响
const boardShiftId = ref('')
const boardShift = computed(
  () => shiftOptions.value.find(g => g.id === boardShiftId.value))
// 多时段的场次：大屏看哪一段。单时段时留空，后端按唯一那一段落。
const boardPeriodId = ref('')

const narrow = useNarrow()
const tasks = ref([])
const board = ref(null)
const qrVisible = ref(false)
const qrTask = ref(null)
// 默认动态码。这个默认值就是安全边界本身，所以不记忆上次的选择——
// 谁要打印固定码，谁当场自己切一下。
const qrMode = ref('dynamic')
const qrCode = ref('')   // vuecheck: script-only，模板经 activeCode → qrSrc 读它
const qrSeconds = ref(0)
const qrRefresh = ref(30)
const qrStale = ref(false)          // 取码失败：屏上那张可能已经过期，得说出来
let qrTimer = null
let codeBusy = false
const manualSid = ref('')
const selected = ref([])
const boardRefresh = ref(null)
const boardAt = ref(0)

const pct = computed(() => board.value
  ? Math.min(100, Math.round(board.value.checked / Math.max(1, board.value.expected) * 100))
  : 0)

const isDynamic = computed(() => qrMode.value === 'dynamic')
// 当前真正生效的那串码：动态码来自接口，固定码就是任务自己的 token
const activeCode = computed(   // vuecheck: script-only，模板只读 qrSrc
  () => (isDynamic.value ? qrCode.value : qrTask.value?.token || ''))

const qrSrc = computed(() => {
  if (!activeCode.value) return ''      // 还没取到码：空 src 会让浏览器去请求当前页
  const key = isDynamic.value ? 'code' : 'token'
  return `/api/checkin/qr.svg?${key}=${activeCode.value}&base=${qrBase()}&t=${Date.now()}`
})

async function pullCode() {
  const task = qrTask.value
  if (codeBusy || !task) return
  codeBusy = true
  try {
    const d = await api.get(`/api/staff/checkin-tasks/${task.id}/code`, { silent: true })
    // 这一秒里可能已经关了弹窗或换到另一张码，那这次结果就不要了
    if (!qrVisible.value || !isDynamic.value || qrTask.value?.id !== task.id) return
    qrCode.value = d.code
    qrSeconds.value = d.expires_in
    qrRefresh.value = d.refresh || qrRefresh.value
    qrStale.value = false
  } catch {
    // 取不到就留着上一张，隔几秒再试——现场网络不稳是常事，屏不该因此空掉。
    // 但要把「这张可能已经过期」标出来，否则等于拿一张废码继续招呼人扫。
    qrStale.value = true
    qrSeconds.value = 3
  } finally {
    codeBusy = false
  }
}

function tickCode() {
  if (qrSeconds.value > 0) { qrSeconds.value -= 1; return }
  pullCode()
}

function stopCode() {
  clearInterval(qrTimer)
  qrTimer = null
  codeBusy = false
  qrCode.value = ''
  qrSeconds.value = 0
  qrStale.value = false
}

/** 只在弹窗开着、且选的是动态码时才转。开关弹窗、换任务、切模式都走这里。
 *  定位签到的任务没有码可取，转起来只是白打接口。 */
function startCode() {
  stopCode()
  if (!qrVisible.value || !qrTask.value || qrTask.value.geo || !isDynamic.value) return
  tickCode()                                  // 立刻取一张，别让弹窗空着等一秒
  qrTimer = setInterval(tickCode, 1000)
}

watch([qrVisible, qrTask, qrMode], startCode)
onUnmounted(stopCode)

/** 这张码管不管当前大屏这一场（这一段）：一张码可以跨场次，所以按组里
 *  每一行看，不只看它对外那一行。 */
function coversBoard(row) {
  return (row.members || []).some(
    m => m.shift_id === boardShiftId.value
      && (!boardPeriodId.value || m.period_id === boardPeriodId.value))
}

// 批量/单个补签需要一个 task_id，且它必须管着**当前大屏这一场**：码表列的是
// 整个活动的码，取「第一个激活的」可能取到别的场次，后端按人在这张码里的
// 那一行找人，结果整屏人被判成「非本场次确认志愿者」全部跳过。后端列表按
// 创建时间倒序，故本场次第一个激活的即最新。
const batchTaskId = computed(() => tasks.value.find(
  t => t.active && coversBoard(t))?.id || '')

async function loadTasks() {
  tasks.value = await api.get(`/api/staff/activities/${actId.value}/checkin-tasks`)
}

/** 到岗看板 + 码列表一起拉：现场补签会同时改这两处的数字。 */
async function loadBoard() {
  if (!boardShiftId.value) return
  const q = boardPeriodId.value ? `?period_id=${boardPeriodId.value}` : ''
  const [b] = await Promise.all([
    api.get(`/api/staff/shifts/${boardShiftId.value}/board${q}`),
    loadTasks(),
  ])
  board.value = b
  boardAt.value = Date.now()
}

/** 换场次：看板整份换掉，先前勾选的人不再属于这一场，必须清空。 */
function onSwitchBoard() {
  board.value = null
  boardAt.value = 0
  selected.value = []
  manualSid.value = ''
  boardRefresh.value?.fire()
}

/** 换场次：时段从头挑，再换看板。 */
function onSwitchShift() {
  boardPeriodId.value = pickDefaultPeriod(boardShift.value)
  onSwitchBoard()
}

/** 换场次：默认看正在进行的、或下一段；单时段就是它自己。 */
function pickDefaultPeriod(g) {
  const list = g?.periods || []
  if (list.length <= 1) return list[0]?.id || ''
  const t = Date.now()
  return (list.find(p => new Date(p.end_time) > t) || list[list.length - 1]).id
}

function onSelect(rows) { selected.value = rows }
function rowClass({ row }) { return row.checked_in ? '' : 'row-wait' }

async function batch(action) {
  if (!batchTaskId.value) return ElMessage.warning('请先生成并开启签到码')
  if (batchBusy.value) return
  const sids = selected.value.map(r => r.student_id)
  if (!sids.length) return
  if (action === 'cancel_checkin' || action === 'cancel_checkout') {
    const label = action === 'cancel_checkin' ? '签到' : '签退'
    const who = selected.value.length === 1
      ? selected.value[0].name : `所选 ${selected.value.length} 人`
    try {
      await ElMessageBox.confirm(`确认取消${who}的${label}？`, '确认', { type: 'warning' })
    } catch {
      return
    }
  }
  batchBusy.value = action
  try {
    const d = await api.post(`/api/staff/checkin-tasks/${batchTaskId.value}/batch`,
      { action, student_ids: sids })
    ElMessage.success(`${d.done} 人操作成功` +
      (d.skipped.length ? `；跳过：${d.skipped.join('、')}` : ''))
    loadBoard()
  } finally {
    batchBusy.value = ''
  }
}

async function quick(action) {
  if (!batchTaskId.value) return ElMessage.warning('请先生成并开启签到码')
  if (!manualSid.value.trim()) return ElMessage.warning('请输入学号')
  const d = await api.post(`/api/staff/checkin-tasks/${batchTaskId.value}/batch`,
    { action, student_ids: [manualSid.value.trim()] })
  if (d.done) {
    ElMessage.success(`${d.names.join('、')} ${action === 'checkin' ? '签到' : '签退'}成功`)
    manualSid.value = ''
  } else {
    ElMessage.warning(d.skipped.join('；'))
  }
  loadBoard()
}

onMounted(async () => {
  await loadTasks()
  boardShiftId.value = shiftOptions.value[0]?.id || ''
  boardPeriodId.value = pickDefaultPeriod(boardShift.value)
  await loadBoard()
})

/** 打开时先选上大屏正在看的那一项：整组的选整组，即选岗位选它的岗位。 */
function openCreate() {
  const g = boardShift.value || shiftOptions.value[0]
  const pos = g && g.kind === 'service' && g.mode !== 'assign' ? realPositions(g)[0] : null
  const target = !g ? '' : pos ? `p:${pos.id}` : g.kind !== 'service' || g.mode === 'assign'
    ? `s:${g.id}` : ''
  const period = boardPeriodId.value && g?.periods?.find(p => p.id === boardPeriodId.value)
  form.value = { target, date: period ? String(period.start_time).slice(0, 10) : '',
                 range: [] }
  onPickTarget()
  creating.value = true
}

/** 改了签到对象：日期选项跟着变，选过的日期不在里面就换成第一天；
 *  可签时段换成这一项的默认值。 */
function onPickTarget() {
  if (!needDate.value) {
    form.value.date = ''
  } else if (!dateOptions.value.includes(form.value.date)) {
    form.value.date = dateOptions.value[0] || ''
  }
  fillRange()
}

/** 默认可签时段：这张码那一段的起止各放宽 WINDOW_PAD 分钟。
 *  卡着时间开闸的话，提前到岗的人签不了、拖堂散场的人也签不了。
 *  当天有几段、定不下是哪段时留空，交给后端报清楚。 */
function fillRange() {
  const p = pickedPeriod.value
  const s = parseLocal(p?.start_time)
  const e = parseLocal(p?.end_time)
  const pad = WINDOW_PAD * 60 * 1000
  form.value.range = s && e ? [new Date(s.getTime() - pad), new Date(e.getTime() + pad)] : []
}

/** 这张码那一段的结束时间：手动设的截止早于它就要先提醒。 */
function pickedEnd() {
  return pickedPeriod.value?.end_time || pickedShift.value?.end_time || ''
}

async function createTask() {
  if (genBusy.value) return
  const [f1, f2] = form.value.range || []
  const target = form.value.target
  if (!target) return ElMessage.warning('请选择签到对象')
  if (needDate.value && !form.value.date) return ElMessage.warning('请选择签到日期')
  // 一端留空的话另一端按默认算，同一张码两种口径，没人说得清
  if (!f1 !== !f2) return ElMessage.warning('可签时段要么都留空，要么起止都填')
  if (f1 && f2 && f2 <= f1) return ElMessage.warning('结束时间要晚于开始时间')
  const id = target.slice(2)
  const body = target.startsWith('p:') ? { position_ids: [id] } : { shift_ids: [id] }
  if (needDate.value) body.date = form.value.date
  if (f1) body.valid_from = toISO(f1)
  if (f2) body.valid_to = toISO(f2)
  genBusy.value = true
  try {
    if (f2 && !await confirmEarlyDeadline(f2, pickedEnd())) return
    await api.post(`/api/staff/activities/${actId.value}/checkin-tasks`, body)
    ElMessage.success('已生成签到码')
    creating.value = false
    await loadTasks()
  } finally {
    genBusy.value = false
  }
}

// ======================= 截止与场次结束 =======================
// 签退没有另外的宽限：码的截止就是签退截止。码过期或被关掉时还没签退的人，
// 时长按场次结束时间兜底、标记待核（见后端 staff_ops.effective_checkout）。
// 所以截止早于场次结束、或场次没完就关码，都得先让管理者知道这个后果。

function parseLocal(v) {
  if (!v) return null
  const d = v instanceof Date ? v : new Date(String(v).replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? null : d
}

function hm(d) {
  const p = n => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 码表一行对应场次的结束时间：列表接口带了 shift_end，没有就去场次列表里找。 */
function shiftEndOf(row) {
  return row?.shift_end
    || shiftOptions.value.find(g => g.id === row?.shift_id)?.end_time || ''
}

/** 截止早于场次结束时要确认；不早于（或取不到场次时间）直接放行。 */
async function confirmEarlyDeadline(deadline, shiftEnd) {
  const d = parseLocal(deadline)
  const e = parseLocal(shiftEnd)
  if (!d || !e || d >= e) return true
  return ElMessageBox.confirm(
    `截止时间 ${hm(d)} 早于服务时段结束（${hm(e)}）。码过期后志愿者无法再签到、签退，`
    + '届时尚未签退的人将按服务时段结束时间记时长，并标记待核。确定吗？',
    '截止早于服务时段结束', { type: 'warning' }).then(() => true).catch(() => false)
}

function toISO(v) {
  if (!v) return null
  const d = v instanceof Date ? v : new Date(v)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

// ======================= 改可签时段 =======================

const winDlg = ref({ visible: false, task: null, range: [], busy: false })

function openWindow(row) {
  // 列表给的是 "YYYY-MM-DDTHH:MM"，直接 new Date 能认
  winDlg.value = {
    visible: true, task: row, busy: false,
    range: [row.valid_from ? new Date(row.valid_from) : null,
            row.valid_to ? new Date(row.valid_to) : null],
  }
}

async function saveWindow() {
  const [f1, f2] = winDlg.value.range || []
  if (!f1 || !f2) return ElMessage.warning('起止时间都要填')
  if (f2 <= f1) return ElMessage.warning('结束时间要晚于开始时间')
  if (!await confirmEarlyDeadline(f2, shiftEndOf(winDlg.value.task))) return
  winDlg.value.busy = true
  try {
    await api.patch(`/api/staff/checkin-tasks/${winDlg.value.task.id}`,
                    { valid_from: toISO(f1), valid_to: toISO(f2) })
    ElMessage.success('可签时段已改')
    winDlg.value.visible = false
    await loadTasks()
  } finally { winDlg.value.busy = false }
}

function openQR(row) {
  qrTask.value = row
  qrVisible.value = true
}

// ======================= 定位签到 =======================

const geoDlg = ref(false)
const geoTask = ref(null)
const geoBusy = ref(false)
// source：这个坐标是怎么来的（gps / search / map / campus，见 GeoPicker）。
// 后端按它写审计，别让几种来源在记录里分不出来
const geoForm = ref({ lat: 0, lng: 0, accuracy: 0, radius_m: 200, source: '' })

function openGeo(row) {
  geoTask.value = row
  // 已经有锚点的，先把旧的填进去：多数时候只是改个半径，
  // 不该逼着人再站到太阳底下量一次
  geoForm.value = {
    lat: row.geo?.lat || 0,
    lng: row.geo?.lng || 0,
    accuracy: row.geo?.accuracy_m || 0,
    radius_m: row.geo?.radius_m || 200,
    source: '',
  }
  geoDlg.value = true
}

async function saveGeo() {
  if (!geoForm.value.lat && !geoForm.value.lng) {
    return ElMessage.warning('请先点「使用我的当前位置」量一下坐标')
  }
  geoBusy.value = true
  try {
    await api.put(`/api/staff/checkin-tasks/${geoTask.value.id}/geo`, geoForm.value)
    ElMessage.success('已改为定位签到')
    geoDlg.value = false
    await loadTasks()
  } finally {
    geoBusy.value = false
  }
}

async function clearGeo() {
  const ok = await ElMessageBox.confirm(
    '改回扫码后，志愿者要在现场扫描二维码才能签到。确定吗？',
    '改回扫码', { type: 'warning' })
    .catch(() => false)
  if (!ok) return
  geoBusy.value = true
  try {
    await api.del(`/api/staff/checkin-tasks/${geoTask.value.id}/geo`)
    ElMessage.success('已改回扫码签到')
    geoDlg.value = false
    await loadTasks()
  } finally {
    geoBusy.value = false
  }
}

const reminding = ref(false)

// 现场催场往往是「东门那三个人没到」，所以可以只催某个岗位组或岗位
const positionGroups = computed(() => [...new Set(
  (boardShift.value?.positions || []).map(p => p.group_name).filter(Boolean))])

const reminderDlg = ref(false)
const reminderScope = ref('all')
const reminderGroup = ref('')
const reminderPositions = ref([])
const reminderAck = ref(false)

function openReminder() {
  reminderScope.value = 'all'
  reminderGroup.value = ''
  reminderPositions.value = []
  reminderAck.value = false
  reminderDlg.value = true
}

async function sendReminder() {
  const body = { need_ack: reminderAck.value }
  if (boardPeriodId.value) body.period_id = boardPeriodId.value
  if (reminderScope.value === 'group') {
    if (!reminderGroup.value) return ElMessage.warning('请选择岗位组')
    body.group_name = reminderGroup.value
  } else if (reminderScope.value === 'position') {
    if (!reminderPositions.value.length) return ElMessage.warning('请选择岗位')
    body.position_ids = reminderPositions.value
  }
  reminding.value = true
  try {
    const d = await api.post(
      `/api/staff/shifts/${boardShiftId.value}/checkin-reminder`, body)
    ElMessage.success(`已向${d.scope}的 ${d.count} 位未签到志愿者发送提醒`
      + (d.batch_id ? '（要求签收）' : ''))
    reminderDlg.value = false
  } finally { reminding.value = false }
}

async function toggle(row) {
  const e = parseLocal(shiftEndOf(row))
  const early = row.active && e && new Date() < e
  const msg = !row.active ? '重新开启这张签到码？'
    : early
      ? `服务时段还没结束（${hm(e)} 结束）。关闭后志愿者无法再用这张码签到、签退，`
        + '尚未签退的人将按服务时段结束时间记时长，并标记待核。确定关闭吗？'
      : '关闭后无法继续扫码签到、签退；尚未签退的人按服务时段结束时间记时长并标记待核。确定吗？'
  const ok = await ElMessageBox.confirm(
    msg, row.active ? '关闭签到码' : '开启签到码',
    { type: row.active ? 'warning' : 'info' }).then(() => true).catch(() => false)
  if (!ok) return
  await api.patch(`/api/staff/checkin-tasks/${row.id}`, { active: !row.active })
  loadTasks(); loadBoard()
}

/** 码里的培训/彩排标记：一张码可能混着服务岗位和培训，只标出非服务的那几种。 */
function kindTags(row) {
  return [...new Set((row?.members || [])
    .filter(m => m.kind && m.kind !== 'service').map(m => m.kind_label))]
}

function setWindowStart(v) { form.value.range = [v || '', form.value.range[1] || ''] }
function setWindowEnd(v) { form.value.range = [form.value.range[0] || '', v || ''] }
</script>

<style scoped>
/* 起止两个选择器并排；窄屏自动叠成两行 */
.two-col { display: flex; gap: 8px; width: 100%; flex-wrap: wrap; }
.two-col > * { flex: 1 1 180px; min-width: 0; }
.big-card { text-align:center; border-radius:4px; }
.ratio b { font-size:30px; color: var(--ruc-red); }
.st-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr)); gap:12px; }
.manual-bar { display:flex; gap:8px; margin-bottom:8px; align-items:center; flex-wrap:wrap; }
/* 二维码下面那行「这是哪一场、哪个岗位」 */
.qr-who { display:flex; justify-content:center; align-items:center; gap:8px;
          flex-wrap:wrap; margin:8px 0 4px; font-size:14px; }
.qr-who .sep { color:#c0c4cc; }
/* 动态码还没取到的这一两秒：占住 280×280，否则弹窗会跳一下 */
.qr-blank { width:280px; height:280px; margin:0 auto; display:flex;
            align-items:center; justify-content:center; color:#c0c4cc;
            background:#fafafa; border:1px dashed #e4e7ed; border-radius:4px; }
:deep(tr.row-wait td.el-table__cell) { color:#9c9c9c; }
</style>
