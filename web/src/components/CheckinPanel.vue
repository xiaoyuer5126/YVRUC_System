<template>
  <div v-if="boardShift">
    <!-- 本页自己管场次，不跟着「报名与调配」走：那边在排人，这边在盯现场，
         两件事同时在做，共用一个选择等于互相打架 -->
    <div class="toolbar">
      <span>签到场次</span>
      <el-select v-model="boardShiftId" style="width:280px" @change="onSwitchBoard">
        <el-option v-for="g in shiftOptions" :key="g.id" :value="g.id"
                   :label="`${g.name}（${g.time_text}）`">
          <span>{{ g.name }}</span>
          <el-tag v-if="g.kind !== 'service'" size="small" effect="plain"
                  style="margin-left:8px">{{ g.kind_label }}</el-tag>
          <span class="dim" style="margin-left:8px">{{ g.time_text }}</span>
        </el-option>
      </el-select>
      <!-- 刷新条放在这里而不是看板里：换场次时看板会整块卸载重建，
           待在里面的话组件跟着重来一次，「更新于」被清空 -->
      <RefreshBar ref="boardRefresh" :load="loadBoard" />
    </div>

    <div class="toolbar">
      <el-button type="primary" size="small" :disabled="over" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;生成签到码
      </el-button>
      <el-button size="small" :disabled="over" @click="openReminder">
        <el-icon><Bell /></el-icon>&nbsp;补发签到提醒
      </el-button>
      <span class="dim">
        {{ hoursFrozen ? '时长已封账，签到记录不再改动'
           : over ? '活动已结束，不再发码；漏签的人仍可在下方手动补签'
           : '同一个码：首次扫码签到，再次扫码签退' }}
      </span>
    </div>

    <!-- 场次与岗位放到 #body 里一起讲（要带培训/彩排标记），不再单列字段 -->
    <DataCards v-if="narrow" :rows="tasks" title-key="shift_name"
               style="margin-bottom:16px" :fields="[]" empty="还没有签到码">
      <template #extra="{ row }">
        <el-tag v-if="row.shift_kind !== 'service'" size="small" effect="plain">
          {{ row.shift_kind_label }}
        </el-tag>
        <el-tag size="small" :type="row.active ? 'success' : 'info'">
          {{ row.active ? '开启' : '已关闭' }}
        </el-tag>
      </template>
      <template #body="{ row }">
        <div class="dim">
          <template v-if="row.shift_id === boardShiftId">当前大屏 · </template>
          {{ row.mode === 'position' ? `分岗位·${row.position_name}` : '统一签到' }} ·
          {{ fmtDT(row.valid_from) }} ~ {{ fmtDT(row.valid_to) }} · 已签 {{ row.count }} 人
        </div>
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
        <el-button size="small" :type="row.active ? 'danger' : 'success'" link
                   @click="toggle(row)">{{ row.active ? '关闭' : '开启' }}</el-button>
      </template>
    </DataCards>
    <el-table v-else :data="tasks" size="small" style="margin-bottom:16px">
      <!-- 不列码：列出来就能被念给群里、抄进聊天框，代签的门槛只剩一张嘴。
           要看码只有一条路——打开二维码弹窗，把那一屏亮在来签到的人面前。
           这张表是整个活动的签到任务，所以场次必须单独成列，否则分不出哪行是哪一场 -->
      <el-table-column label="场次" min-width="150">
        <template #default="{row}">
          {{ row.shift_name }}
          <el-tag v-if="row.shift_kind !== 'service'" size="small" effect="plain">
            {{ row.shift_kind_label }}
          </el-tag>
          <el-tag v-if="row.shift_id === boardShiftId" size="small" type="success"
                  effect="plain">当前大屏</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="模式" width="130">
        <template #default="{row}">
          {{ row.mode === 'position' ? `岗位·${row.position_name}` : '统一签到' }}
        </template>
      </el-table-column>
      <el-table-column label="有效时段" min-width="180">
        <template #default="{row}">{{ fmtDT(row.valid_from) }} ~ {{ fmtDT(row.valid_to) }}</template>
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
      <el-table-column label="操作" width="300">
        <template #default="{row}">
          <el-button size="small" @click="openQR(row)">
            {{ row.geo ? '大屏' : '二维码 / 大屏' }}
          </el-button>
          <el-button size="small" :disabled="over" @click="openGeo(row)">定位</el-button>
          <el-popconfirm :title="row.active ? '关闭后无法继续扫码' : '重新开启？'"
                         :disabled="over" @confirm="toggle(row)">
            <template #reference>
              <el-button size="small" :type="row.active ? 'danger' : 'success'" link
                         :disabled="over">
                {{ row.active ? '关闭' : '开启' }}
              </el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

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
            <el-progress type="circle" :percentage="pct" :width="86"
                         :color="pct === 100 ? '#67c23a' : '#9e2235'" style="margin-top:8px" />
          </el-card>
        </el-col>
        <el-col :span="18">
          <el-card shadow="never">
            <div class="st-grid">
              <div v-for="s in board.positions" :key="s.id" class="st-box">
                <b>{{ s.name }}</b>
                <div class="dim">{{ s.checked }}/{{ s.quota }} 到岗</div>
                <el-progress :percentage="Math.min(100, Math.round(s.checked / Math.max(1, s.quota) * 100))"
                             :stroke-width="7" :show-text="false"
                             :color="s.checked >= s.quota ? '#67c23a' : '#9e2235'" />
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <div class="section-title">到场名单（含未签到者，勾选后可批量操作）</div>
      <el-card shadow="never">
        <div class="manual-bar">
          <el-input v-model="manualSid" size="small" placeholder="输入学号单个操作" style="width:180px"
                    :disabled="!batchTaskId || hoursFrozen" />
          <el-button size="small" :disabled="!batchTaskId || hoursFrozen" @click="quick('checkin')">签到</el-button>
          <el-button size="small" :disabled="!batchTaskId || hoursFrozen" @click="quick('checkout')">签退</el-button>
          <el-divider direction="vertical" />
          <template v-if="selected.length">
            <span class="dim">已选 {{ selected.length }} 人：</span>
            <el-button size="small" type="primary" :disabled="!batchTaskId || hoursFrozen"
                       @click="batch('checkin')">批量签到</el-button>
            <el-button size="small" type="warning" :disabled="!batchTaskId || hoursFrozen"
                       @click="batch('checkout')">批量签退</el-button>
            <el-button size="small" :disabled="!batchTaskId || hoursFrozen"
                       @click="batch('cancel_checkout')">取消签退</el-button>
            <el-button size="small" type="danger" plain :disabled="!batchTaskId || hoursFrozen"
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
               :fields="[{ key: 'student_id', label: '学号' },
                         { key: 'position', label: '岗位' },
                         { key: 'time', label: '签到', hideEmpty: true },
                         { key: 'checkout_at', label: '签退', hideEmpty: true }]"
               empty="名单为空">
      <template #extra="{ row }">
        <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">组长</el-tag>
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
          <el-table-column prop="student_id" label="学号" width="120" />
          <el-table-column prop="college" label="学院" min-width="140" />
          <el-table-column prop="position" label="岗位" min-width="120" />
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

    <!-- 生成签到码 -->
    <el-dialog v-model="creating" title="生成签到码" width="460">
      <el-form label-width="110px">
        <!-- 一个码只属于一个场次，所以场次必须在这里选定，不能跟着别的页签走 -->
        <el-form-item label="签到场次" required>
          <el-select v-model="form.shift_id" style="width:100%" @change="onPickShift">
            <el-option v-for="g in shiftOptions" :key="g.id" :value="g.id"
                       :label="`${g.name}（${g.time_text}）`">
              <span>{{ g.name }}</span>
              <el-tag v-if="g.kind !== 'service'" size="small" effect="plain"
                      style="margin-left:8px">{{ g.kind_label }}</el-tag>
              <span class="dim" style="margin-left:8px">{{ g.time_text }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="签到模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="shift">统一签到</el-radio>
            <el-radio value="position" :disabled="!formShift?.positions?.length">
              分岗位签到
            </el-radio>
          </el-radio-group>
          <div v-if="!formShift?.positions?.length" class="dim">
            {{ formShift?.kind !== 'service'
               ? `${formShift?.kind_label}不单设岗位，只能统一签到`
               : '本场次还没有岗位，只能统一签到' }}
          </div>
        </el-form-item>
        <el-form-item v-if="form.mode === 'position'" label="选择岗位" required>
          <el-select v-model="form.position_id" style="width:100%">
            <el-option v-for="s in formShift?.positions || []" :key="s.id" :value="s.id"
                       :label="`${s.name}（${s.time_text}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="可签时段">
          <div class="two-col">
            <DateTimeField :model-value="form.range[0]" placeholder="开放签到"
                           @update:model-value="setWindowStart" />
            <DateTimeField :model-value="form.range[1]" placeholder="停止签到"
                           @update:model-value="setWindowEnd" />
          </div>
          <div class="dim">默认比场次起止各放宽 {{ WINDOW_PAD }} 分钟，可自行调整。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="creating = false">取消</el-button>
        <el-button type="primary" @click="createTask">生成</el-button>
      </template>
    </el-dialog>

    <!-- 二维码 -->
    <el-dialog v-model="qrVisible" title="现场签到二维码" width="380">
      <div style="text-align:center" v-if="qrTask">
        <!-- 定位签到的任务没有可扫的码：这一屏显示在墙上也没人能扫。
             说清楚该干什么，别让现场的人对着一块空屏瞎猜 -->
        <el-alert v-if="qrTask.geo" type="info" :closable="false" style="text-align:left">
          <p style="margin:0;line-height:1.9">
            本场次用<b>定位签到</b>，不需要二维码。<br>
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
          <!-- 这张码是哪一场、哪个岗位的：打印出来贴到现场，光看码分不出 -->
          <div class="qr-who">
            <b>{{ qrTask.shift_name }}</b>
            <el-tag v-if="qrTask.shift_kind !== 'service'" size="small" effect="plain">
              {{ qrTask.shift_kind_label }}
            </el-tag>
            <template v-if="qrTask.mode === 'position'">
              <span class="sep">·</span><b>{{ qrTask.position_name }}</b>
              <el-tag size="small" effect="plain">分岗位</el-tag>
            </template>
            <el-tag v-else size="small" effect="plain">统一签到</el-tag>
          </div>
          <div class="dim">可签 {{ fmtDT(qrTask.valid_from) }} ~ {{ fmtDT(qrTask.valid_to) }}</div>
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
          {{ qrTask.shift_name }}
          <template v-if="qrTask.mode === 'position'">· {{ qrTask.position_name }}</template>
          · 可签 {{ fmtDT(qrTask.valid_from) }} ~ {{ fmtDT(qrTask.valid_to) }}
        </div>
      </div>
    </el-dialog>

    <!-- 定位签到：坐标由管理者站在岗位上量 -->
    <el-dialog v-model="geoDlg" title="定位签到" width="440">
      <template v-if="geoTask">
        <el-form label-width="90px">
          <el-form-item label="签到地点">
            <!-- 三行：快捷来源 / 按地名搜 / 坐标。校区按钮只把地图带到校区，
                 落点还得拖着图钉挪到具体岗位 -->
            <div class="geo-line">
              <el-button size="small" :loading="geoLocating"
                         @click="locateMe">使用我的当前位置</el-button>
              <el-button v-for="c in campuses" :key="c.name" size="small" plain
                         @click="useCampus(c)">{{ c.name }}</el-button>
            </div>
            <div class="geo-line">
              <el-input v-model="geoQuery" size="small" style="width:200px"
                        placeholder="地名，如「明德楼」" clearable
                        @keyup.enter="searchPlace" />
              <el-button size="small" :loading="geoSearching"
                         @click="searchPlace">搜索</el-button>
            </div>
            <!-- 同名异地很常见（「明德楼」全国一堆），所以地址必须列出来 -->
            <div v-if="geoResults.length" class="geo-results">
              <div v-for="(r, i) in geoResults" :key="i" class="geo-result"
                   @click="pickResult(r)">
                <b>{{ r.name }}</b>
                <span class="dim">{{ r.address }}</span>
              </div>
            </div>
            <div v-if="geoForm.lat" class="dim" style="line-height:1.7">
              {{ geoForm.lat.toFixed(5) }}, {{ geoForm.lng.toFixed(5) }}
              <template v-if="geoForm.accuracy">
                （手机报的误差约 ±{{ Math.round(geoForm.accuracy) }} 米）
              </template>
            </div>
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
        <!-- 地图只给人看，不能拖：锚点必须站在现场量，能拖着改就等于允许
             坐办公室把点位挪到任何地方，那道判定就白设了。leaflet 只在打开
             这个弹窗时才去加载（异步组件），大多数管理者不会开这一页 -->
        <GeoMap v-if="geoForm.lat" :lat="geoForm.lat" :lng="geoForm.lng"
                :radius="geoForm.radius_m" :tk="mapTk" pickable
                @pick="onPick" />
        <div class="dim" style="line-height:1.8">
          图上可以直接点一下、或者拖着图钉挪位置。
        </div>
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
    <el-dialog v-model="reminderDlg" title="补发签到提醒" width="460">
      <p class="dim" style="line-height:1.8">
        只发给<b>未签到</b>的正式志愿者。场次开始前 30 分钟系统已自动发送过一次。
      </p>
      <el-form label-width="72px">
        <el-form-item label="范围">
          <el-radio-group v-model="reminderScope">
            <el-radio value="all">全场次</el-radio>
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
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtDT, qrBase } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from './DataCards.vue'
import DateTimeField from './DateTimeField.vue'
import RefreshBar from './RefreshBar.vue'

// leaflet 六十来 KB，只有打开「定位」弹窗才用得上，就别让它跟着这一页一起
// 进包——大多数管理者整场活动都不会开那个弹窗
const GeoMap = defineAsyncComponent(() => import('./GeoMap.vue'))

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
const form = ref({ shift_id: '', mode: 'shift', position_id: '', range: [] })

const shiftOptions = computed(() => props.allShifts)
const formShift = computed(
  () => shiftOptions.value.find(g => g.id === form.value.shift_id))

// 本页自己持有的场次选择，与「报名与调配」互不影响
const boardShiftId = ref('')
const boardShift = computed(
  () => shiftOptions.value.find(g => g.id === boardShiftId.value))

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

// 批量/单个补签需要一个 task_id，且它必须属于**当前大屏这一场**：码表列的是
// 整个活动的码，取「第一个激活的」可能取到别的场次，后端按 task.shift_id 找人，
// 结果整屏人被判成「非本场次确认志愿者」全部跳过。后端列表按创建时间倒序，
// 故本场次第一个激活的即最新。
const batchTaskId = computed(() => tasks.value.find(
  t => t.active && t.shift_id === boardShiftId.value)?.id || '')

async function loadTasks() {
  tasks.value = await api.get(`/api/staff/activities/${actId.value}/checkin-tasks`)
}

/** 到岗看板 + 码列表一起拉：现场补签会同时改这两处的数字。 */
async function loadBoard() {
  if (!boardShiftId.value) return
  const [b] = await Promise.all([
    api.get(`/api/staff/shifts/${boardShiftId.value}/board`),
    loadTasks(),
  ])
  board.value = b
}

/** 换场次：看板整份换掉，先前勾选的人不再属于这一场，必须清空。 */
function onSwitchBoard() {
  board.value = null
  selected.value = []
  manualSid.value = ''
  boardRefresh.value?.fire()
}

function onSelect(rows) { selected.value = rows }
function rowClass({ row }) { return row.checked_in ? '' : 'row-wait' }

async function batch(action) {
  if (!batchTaskId.value) return ElMessage.warning('请先生成并开启签到码')
  const sids = selected.value.map(r => r.student_id)
  if (!sids.length) return
  const d = await api.post(`/api/staff/checkin-tasks/${batchTaskId.value}/batch`,
    { action, student_ids: sids })
  ElMessage.success(`${d.done} 人操作成功` +
    (d.skipped.length ? `；跳过：${d.skipped.join('、')}` : ''))
  loadBoard()
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
  await loadBoard()
  boardRefresh.value?.markLoaded()
})

/** 默认可签时段：场次起止各外扩 WINDOW_PAD 分钟。
 *
 * 卡着场次时间开闸的话，提前到岗的人签不了、拖堂散场的人也签不了。
 */
function defaultRange(g) {
  const pad = WINDOW_PAD * 60 * 1000
  const s = new Date((g?.start_time || '').replace(' ', 'T'))
  const e = new Date((g?.end_time || '').replace(' ', 'T'))
  if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime())) return []
  return [new Date(s.getTime() - pad), new Date(e.getTime() + pad)]
}

function openCreate() {
  const g = boardShift.value || shiftOptions.value[0]
  form.value = { shift_id: g?.id || '', mode: 'shift', position_id: '',
                 range: defaultRange(g) }
  creating.value = true
}

/** 换了场次：时段跟着换，岗位清空——旧岗位不属于新场次，后端会 400。 */
function onPickShift() {
  const g = formShift.value
  form.value.range = defaultRange(g)
  form.value.position_id = ''
  if (!g?.positions?.length) form.value.mode = 'shift'
}

async function createTask() {
  if (!form.value.shift_id) return ElMessage.warning('请选择签到场次')
  if (form.value.mode === 'position' && !form.value.position_id)
    return ElMessage.warning('请选择岗位')
  const body = {
    mode: form.value.mode,
    position_id: form.value.mode === 'position' ? form.value.position_id : null,
  }
  const [f1, f2] = form.value.range || []
  if (f1) body.valid_from = toISO(f1)
  if (f2) body.valid_to = toISO(f2)
  await api.post(`/api/staff/shifts/${form.value.shift_id}/checkin-tasks`, body)
  ElMessage.success('签到码已生成')
  creating.value = false
  await loadTasks()
}

function toISO(v) {
  if (!v) return null
  const d = v instanceof Date ? v : new Date(v)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

function openQR(row) {
  qrTask.value = row
  qrVisible.value = true
}

// ======================= 定位签到 =======================

const geoDlg = ref(false)
const geoTask = ref(null)
const geoBusy = ref(false)
const geoLocating = ref(false)
// source：这个坐标是怎么来的——'gps' 站在现场量的 / 'map' 在图上点或拖的 /
// 'campus' 校区预设点。后端按它写审计，别让几种来源在记录里分不出来
const geoForm = ref({ lat: 0, lng: 0, accuracy: 0, radius_m: 200, source: '' })

// 天地图的 tk：从后端取，不是构建时写死——换 key 不用重新构建前端。
// 取不到（没配）就只是不画底图，点位和范围照样有
const mapTk = ref('')
let mapTkLoaded = false

// 校区快捷选点的坐标也来自后端（config.CAMPUS_PRESETS）：改一个校区的位置
// 不该要重新构建前端
const campuses = ref([])

async function loadMapTk() {
  if (mapTkLoaded) return
  mapTkLoaded = true
  try {
    const d = await api.get('/api/staff/map-config', { silent: true })
    mapTk.value = d.tk || ''
    campuses.value = d.campuses || []
  } catch { /* 没配就没有地图，不影响定位签到本身 */ }
}

/** 按校区把地图挪过去。**这是起点不是终点**：校区中心跟具体岗位（东门、
 *  某栋楼前）能差好几百米，落点要拖着图钉挪到实际位置上。 */
function useCampus(c) {
  geoForm.value.lat = c.lat
  geoForm.value.lng = c.lng
  geoForm.value.accuracy = 0
  geoForm.value.source = 'campus'
}

// —— 按地名搜坐标 ——
const geoQuery = ref('')
const geoResults = ref([])
const geoSearching = ref(false)

/** 搜地名。**带上当前坐标**：搜出来的范围限定在这附近，
 *  不然「明德楼」会把桂林、深圳的都端上来。 */
async function searchPlace() {
  const q = geoQuery.value.trim()
  if (q.length < 2) return ElMessage.warning('请至少输入两个字')
  geoSearching.value = true
  try {
    const d = await api.get(
      `/api/staff/geo/search?q=${encodeURIComponent(q)}`
      + (geoForm.value.lat ? `&lat=${geoForm.value.lat}&lng=${geoForm.value.lng}` : ''),
      { silent: true })
    geoResults.value = d.items || []
  } catch { /* 搜索坏了不影响手填坐标，api 层已经提示过 */ } finally {
    geoSearching.value = false
  }
}

/** 选中一个候选点：坐标以它为准，并且记下来源不是现场量的。 */
function pickResult(r) {
  geoForm.value.lat = r.lat
  geoForm.value.lng = r.lng
  geoForm.value.accuracy = 0
  geoForm.value.source = 'search'
  geoResults.value = []
  geoQuery.value = r.name
}

function openGeo(row) {
  geoTask.value = row
  loadMapTk()
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

/** 在图上点了一下或拖了图钉：坐标以这个为准，并且记下它不是现场量的。 */
function onPick(lat, lng) {
  geoForm.value.lat = lat
  geoForm.value.lng = lng
  geoForm.value.accuracy = 0
  geoForm.value.source = 'map'
}

/** 量当前位置。这个坐标只是「管理者此刻站在哪」，判定用的仍是服务端。 */
function locateMe() {
  if (!navigator.geolocation) {
    return ElMessage.error('这个浏览器不能定位，请换手机浏览器再试')
  }
  geoLocating.value = true
  navigator.geolocation.getCurrentPosition(pos => {
    geoLocating.value = false
    geoForm.value.lat = pos.coords.latitude
    geoForm.value.lng = pos.coords.longitude
    geoForm.value.accuracy = pos.coords.accuracy || 0
    geoForm.value.source = 'gps'
  }, e => {
    geoLocating.value = false
    if (!window.isSecureContext) {
      return ElMessage.error('定位只能在 https 下使用，请用平台正式网址打开')
    }
    ElMessage.error(e?.code === 1 ? '您没有允许定位，请在浏览器设置里允许后重试'
                   : e?.code === 3 ? '定位超时，请到空旷处重试'
                   : '取不到定位，请到空旷处或窗边重试')
  }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 })
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
  await api.patch(`/api/staff/checkin-tasks/${row.id}`, { active: !row.active })
  loadTasks(); loadBoard()
}

function setWindowStart(v) { form.value.range = [v || '', form.value.range[1] || ''] }
function setWindowEnd(v) { form.value.range = [form.value.range[0] || '', v || ''] }
</script>

<style scoped>
/* 起止两个选择器并排；窄屏自动叠成两行 */
.two-col { display: flex; gap: 8px; width: 100%; flex-wrap: wrap; }
.two-col > * { flex: 1 1 180px; min-width: 0; }
.big-card { text-align:center; border-radius:12px; }
.ratio b { font-size:30px; color: var(--ruc-red); }
.st-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr)); gap:12px; }
.manual-bar { display:flex; gap:8px; margin-bottom:8px; align-items:center; flex-wrap:wrap; }
/* 签到地点那三行：快捷来源 / 搜索 / 坐标 */
.geo-line { display:flex; gap:8px; align-items:center; flex-wrap:wrap;
            margin-bottom:8px; width:100%; }
/* 搜索结果：一条一行，点一下就用它的坐标。地址必须显示——同名异地太多了 */
.geo-results { margin-bottom:8px; max-height:180px; overflow-y:auto; width:100%;
               border:1px solid #eceef1; border-radius:8px; }
.geo-result { padding:6px 10px; cursor:pointer; line-height:1.6; font-size:13px; }
.geo-result + .geo-result { border-top:1px solid #f2f3f5; }
.geo-result:hover { background:#f7f8fa; }
.geo-result .dim { margin-left:8px; }
/* 二维码下面那行「这是哪一场、哪个岗位」 */
.qr-who { display:flex; justify-content:center; align-items:center; gap:8px;
          flex-wrap:wrap; margin:8px 0 4px; font-size:14px; }
.qr-who .sep { color:#c0c4cc; }
/* 动态码还没取到的这一两秒：占住 280×280，否则弹窗会跳一下 */
.qr-blank { width:280px; height:280px; margin:0 auto; display:flex;
            align-items:center; justify-content:center; color:#c0c4cc;
            background:#fafafa; border:1px dashed #e4e7ed; border-radius:8px; }
:deep(tr.row-wait td.el-table__cell) { color:#9c9c9c; }
</style>
