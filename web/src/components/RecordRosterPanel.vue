<!-- 补记录活动（不公开招募）的名单导入：粘贴或上传「学号 + 时长」，
     导进来的每一行就是这个人在这场活动里的时长，之后到「时长管理」核准。
     导入分两步：先出预览（会新增谁、覆盖谁、跳过哪几行），点「确认导入」才写入。 -->
<template>
  <div>
    <el-alert v-if="state.block_reason" type="info" show-icon :closable="false"
              :title="state.block_reason" />
    <el-alert v-else type="info" :closable="false">
      每行一个人：<b>学号 时长 [备注]</b>，空格、制表符或逗号分隔；时长以小时计，
      支持 0.5 小时，超过 {{ state.warn_hours }} 小时的会标出来请你核对。可直接从 Excel
      复制三列粘贴，也可上传 .xlsx / .csv（前三列依次为学号、时长、备注，表头行自动跳过）。
      先出预览，确认无误后点「确认导入」才写入。
      平台里找不到的学号只会列出来提醒，不会建档。可以多次导入：新的人追加，
      <b>已在名单里的人按这次的时长覆盖</b>（有待处理申诉的除外）。
      时长核准后再改名单会撤销本轮核准，须到「时长管理」重新核准并通知志愿者。
    </el-alert>

    <el-card v-if="state.editable" shadow="never" style="margin-top:12px">
      <el-input v-model="text" type="textarea" :rows="6"
                placeholder="2023200001 3&#10;2023200002 2.5 场外支援" />
      <div class="toolbar" style="margin-top:8px">
        <el-button type="primary" :loading="busy" :disabled="!text.trim()"
                   @click="previewText">预览</el-button>
        <el-upload :show-file-list="false" :http-request="previewFile"
                   accept=".xlsx,.csv">
          <el-button plain :loading="busy">
            <el-icon><Upload /></el-icon>&nbsp;上传 Excel / CSV
          </el-button>
        </el-upload>
        <span v-if="state.held_text" class="dim">活动时间：{{ state.held_text }}</span>
      </div>
    </el-card>

    <!-- 预览（还没写入）或刚导入的结果：新增与覆盖的人、未改动几人、跳过的行（带原因）、
         找不到的学号。预览时底下是「确认导入」 -->
    <el-card v-if="result" shadow="never" style="margin-top:12px">
      <div class="toolbar">
        <el-tag size="small" :type="result.applied ? 'success' : 'warning'">
          {{ result.applied ? '已导入' : '预览 · 尚未写入' }}
        </el-tag>
        <b>{{ result.message }}</b>
        <el-button v-if="result.applied" link style="margin-left:auto"
                   @click="result = null">收起</el-button>
      </div>
      <el-alert v-if="!result.applied && result.reapproval_required" type="warning"
                show-icon :closable="false" style="margin-top:8px"
                title="时长已经核准过：确认导入会撤销本轮核准，须到「时长管理」重新核准并通知志愿者" />
      <!-- 超过 24 小时的照导，但要人再看一眼 -->
      <div v-if="flagged.length" style="margin-top:8px">
        <span class="dim">请核对（时长偏大）：</span>
        <el-tag v-for="x in flagged" :key="x.student_id" type="warning" effect="plain"
                size="small" style="margin:4px 4px 0 0">
          {{ x.name }} {{ x.student_id }} {{ x.hours }}h · {{ x.warning }}
        </el-tag>
      </div>
      <div v-if="changed.length" class="scroll-x">
        <DataCards v-if="narrow" :rows="changed" title-key="name" style="margin-top:8px"
                   :fields="[{ key: 'student_id', label: '学号' },
                             { key: 'hours', label: '时长' }]">
          <template #extra="{ row }">
            <el-tag size="small" :type="row.old_hours == null ? 'success' : 'primary'">
              {{ row.old_hours == null ? '新增' : '覆盖' }}
            </el-tag>
          </template>
          <template #field-hours="{ row }">
            <template v-if="row.old_hours == null"><b>{{ row.hours }}h</b></template>
            <template v-else>
              {{ row.old_hours }}h → <b>{{ row.hours }}h</b>
              <span v-if="row.note_changed" class="dim">（备注也改了）</span>
            </template>
          </template>
        </DataCards>
        <el-table v-else :data="changed" size="small" max-height="360"
                  style="margin-top:8px;min-width:480px">
          <el-table-column label="" width="70">
            <template #default="{ row }">
              <el-tag size="small" :type="row.old_hours == null ? 'success' : 'primary'">
                {{ row.old_hours == null ? '新增' : '覆盖' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="姓名" width="100" />
          <el-table-column prop="student_id" label="学号" width="130" />
          <el-table-column label="时长" min-width="160">
            <template #default="{ row }">
              <template v-if="row.old_hours == null"><b>{{ row.hours }}h</b></template>
              <template v-else>
                {{ row.old_hours }}h → <b>{{ row.hours }}h</b>
                <span v-if="row.note_changed" class="dim">（备注也改了）</span>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div v-if="result.unchanged" class="dim" style="margin-top:8px">
        {{ result.unchanged }} 人与当前名单一致，不改动
      </div>
      <div v-if="result.not_found.length" style="margin-top:8px">
        <span class="dim">平台里找不到这些学号（未建档，请提醒志愿者登录平台，登录后自动创建账户；或核对名单后重新导入）：</span>
        <el-tag v-for="x in result.not_found" :key="x.line" type="danger" effect="plain"
                size="small" style="margin:4px 4px 0 0">
          第 {{ x.line }} 行 {{ x.student_id }}
        </el-tag>
      </div>
      <DataCards v-if="result.skipped.length && narrow" :rows="result.skipped"
                 title-key="student_id" style="margin-top:8px"
                 empty="没有跳过的行"
                 :fields="[{ key: 'line', label: '行', format: v => `第 ${v} 行` },
                           { key: 'name', label: '姓名' },
                           { key: 'reason', label: '跳过原因' }]" />
      <el-table v-else-if="result.skipped.length" :data="result.skipped" size="small"
                style="margin-top:8px">
        <el-table-column prop="line" label="行" width="60" />
        <el-table-column prop="student_id" label="学号" width="130" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="reason" label="跳过原因" min-width="220" />
      </el-table>
      <div v-if="!result.applied" class="btn-row" style="margin-top:12px">
        <el-button type="primary" :loading="busy" :disabled="!changed.length"
                   @click="confirmImport">确认导入</el-button>
        <el-button :disabled="busy" @click="cancelPreview">取消</el-button>
        <span v-if="!changed.length" class="dim">没有要写入的变动</span>
      </div>
    </el-card>

    <div class="section-title" style="margin-top:16px">
      当前名单（{{ state.items.length }} 人，合计 {{ state.total_hours }} 小时）
    </div>
    <DataCards v-if="narrow" :rows="state.items" title-key="user.name"
               empty="还没有导入名单"
               :fields="[{ key: 'user.student_id', label: '学号' },
                         { key: 'user.college', label: '学院' },
                         { key: 'hours', label: '时长(h)' },
                         { key: 'note', label: '备注' }]">
      <template #field-hours="{ row }"><b>{{ row.hours }}</b></template>
      <template #actions="{ row }">
        <template v-if="state.editable">
          <el-button size="small" link type="primary" @click="openEdit(row)">修改</el-button>
          <el-tooltip :disabled="!row.appealed" content="有过时长申诉的记录不能删除，请改时长">
            <span>
              <el-popconfirm :title="`从名单删除 ${row.user?.name}？`"
                             :disabled="row.appealed" @confirm="remove(row)">
                <template #reference>
                  <el-button size="small" link type="danger"
                             :disabled="row.appealed">删除</el-button>
                </template>
              </el-popconfirm>
            </span>
          </el-tooltip>
        </template>
        <el-tag v-else-if="row.appeal_pending" size="small" type="warning">申诉中</el-tag>
        <span v-else class="dim">—</span>
      </template>
    </DataCards>
    <template v-else>
      <el-empty v-if="!state.items.length" description="还没有导入名单" />
      <div v-else class="scroll-x">
        <el-table :data="state.items" size="small" style="min-width:600px">
          <el-table-column label="姓名" width="110">
            <template #default="{ row }">{{ row.user?.name }}</template>
          </el-table-column>
          <el-table-column label="学号" width="120">
            <template #default="{ row }">{{ row.user?.student_id }}</template>
          </el-table-column>
          <el-table-column label="学院" width="130">
            <template #default="{ row }">{{ row.user?.college }}</template>
          </el-table-column>
          <el-table-column label="时长(h)" width="90">
            <template #default="{ row }"><b>{{ row.hours }}</b></template>
          </el-table-column>
          <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
          <el-table-column label="操作" width="140">
            <template #default="{ row }">
              <template v-if="state.editable">
                <div class="btn-row">
                  <el-button size="small" link type="primary" @click="openEdit(row)">修改</el-button>
                  <el-tooltip :disabled="!row.appealed" content="有过时长申诉的记录不能删除，请改时长">
                    <span>
                      <el-popconfirm :title="`从名单删除 ${row.user?.name}？`"
                                     :disabled="row.appealed" @confirm="remove(row)">
                        <template #reference>
                          <el-button size="small" link type="danger"
                                     :disabled="row.appealed">删除</el-button>
                        </template>
                      </el-popconfirm>
                    </span>
                  </el-tooltip>
                </div>
              </template>
              <el-tag v-else-if="row.appeal_pending" size="small" type="warning">申诉中</el-tag>
              <span v-else class="dim">—</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </template>

    <el-dialog v-model="edit.visible" :title="`修改 ${edit.row?.user?.name || ''} 的时长`"
               width="420"
               :before-close="guardEdit" @opened="guardEdit.snap">
      <el-form label-width="70px">
        <el-form-item label="时长">
          <el-input-number v-model="edit.hours" :min="0.5" :step="0.5" :precision="1" />
          <span class="dim" style="margin-left:8px">小时</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="edit.note" maxlength="120" placeholder="选填" />
        </el-form-item>
      </el-form>
      <div class="dim">核准后改名单会撤销本轮核准；请到「时长管理」重新核准并通知志愿者。</div>
      <template #footer>
        <el-button @click="edit.visible = false">取消</el-button>
        <el-button type="primary" :loading="edit.busy" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import { api } from '../api'
import { useNarrow } from '../composables/useNarrow'
import { useCloseGuard } from '../composables/closeGuard'
import DataCards from './DataCards.vue'

const props = defineProps({
  activityId: { type: String, required: true },
})
const narrow = useNarrow()
const emit = defineEmits(['changed'])

const state = ref({ items: [], total_hours: 0, editable: false, block_reason: '',
                    warn_hours: 24, held_text: '' })
const text = ref('')
const busy = ref(false)
// 预览（applied 为假）或刚写入的结果
const result = ref(null)
// 预览的是哪一份内容：确认时原样再交一次，后端按指纹核对没变过
let source = null
// 这次导入里新增或覆盖后时长偏大、要人核对的
const flagged = computed(() => changed.value.filter(x => x.warning))
// 新增与覆盖合成一张表：新增的没有旧时长
const changed = computed(() => [
  ...(result.value?.added || []).map(x => ({ ...x, old_hours: null })),
  ...(result.value?.overwritten || []),
])
const edit = ref({ visible: false, row: null, hours: 1, note: '', busy: false })
const guardEdit = useCloseGuard(() => [edit.value.hours, edit.value.note])
const base = () => `/api/staff/activities/${props.activityId}/record-roster`

async function load() {
  state.value = await api.get(base())
}

function fileForm(file, fingerprint) {
  const fd = new FormData()
  fd.append('file', file)
  if (fingerprint) fd.append('fingerprint', fingerprint)
  return fd
}

// 按 source 预览一次：粘贴的文本或上传的文件
async function preview() {
  const src = source
  result.value = src.text != null
    ? await api.post(`${base()}/import/preview`, { text: src.text })
    : await api.post(`${base()}/import-file/preview`, fileForm(src.file))
}

async function previewText() {
  busy.value = true
  try {
    source = { text: text.value }
    await preview()
  } finally { busy.value = false }
}

async function previewFile({ file }) {
  busy.value = true
  try {
    source = { file }
    await preview()
  } finally { busy.value = false }
}

async function confirmImport() {
  const src = source
  const fingerprint = result.value.fingerprint
  busy.value = true
  try {
    const d = src.text != null
      ? await api.post(`${base()}/import`, { text: src.text, fingerprint },
                       { skipErrorToast: true })
      : await api.post(`${base()}/import-file`, fileForm(src.file, fingerprint),
                       { skipErrorToast: true })
    result.value = d
    source = null
    if (src.text != null) text.value = ''
    const warn = d.reapproval_required || d.skipped.length || d.not_found.length
      || changed.value.some(x => x.warning)
    ElMessage[warn ? 'warning' : 'success'](d.message)
    load()
    emit('changed')
  } catch (e) {
    if (e.data?.code !== 'ROSTER_CHANGED') { ElMessage.error(e.message); return }
    // 预览之后别人改过名单：按现在的名单重新预览，请管理者再看一遍
    ElMessage.warning('预览之后名单有变动，已按最新名单重新预览，请核对后再确认')
    load()
    await preview()
  } finally { busy.value = false }
}

function cancelPreview() {
  result.value = null
  source = null
}

function openEdit(row) {
  edit.value = { visible: true, row, hours: row.hours, note: row.note || '', busy: false }
}

async function saveEdit() {
  edit.value.busy = true
  try {
    const d = await api.patch(`${base()}/${edit.value.row.id}`,
                              { hours: edit.value.hours, note: edit.value.note })
    ElMessage[d.reapproval_required ? 'warning' : 'success'](
      d.reapproval_required ? '已修改，原时长核准已撤销，请重新核准' : '已修改')
    edit.value.visible = false
    load()
    emit('changed')
  } finally { edit.value.busy = false }
}

async function remove(row) {
  const d = await api.del(`${base()}/${row.id}`)
  ElMessage[d.reapproval_required ? 'warning' : 'success'](
    d.reapproval_required ? '已删除，原时长核准已撤销，请重新核准' : '已从名单删除')
  load()
  emit('changed')
}

onMounted(load)
defineExpose({ load })
</script>
