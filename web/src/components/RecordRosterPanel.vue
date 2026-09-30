<!-- 补记录活动（不公开招募）的名单导入：粘贴或上传「学号 + 时长」，
     导进来的每一行就是这个人在这场活动里的时长，之后到「时长管理」核准。 -->
<template>
  <div>
    <el-alert v-if="state.block_reason" type="info" show-icon :closable="false"
              :title="state.block_reason" />
    <el-alert v-else type="info" :closable="false">
      每行一个人：<b>学号 时长 [备注]</b>，空格、制表符或逗号分隔；时长以小时计，
      支持 0.5 小时，最多 {{ state.max_hours }} 小时。可直接从 Excel 复制三列粘贴，
      也可上传 .xlsx / .csv（前三列依次为学号、时长、备注，表头行自动跳过）。
      平台里找不到的学号只会列出来提醒，不会建档。可以多次追加导入。
    </el-alert>

    <el-card v-if="state.editable" shadow="never" style="margin-top:12px">
      <el-input v-model="text" type="textarea" :rows="6"
                placeholder="2023200001 3&#10;2023200002 2.5 场外支援" />
      <div class="toolbar" style="margin-top:8px">
        <el-button type="primary" :loading="busy" :disabled="!text.trim()"
                   @click="importText">导入</el-button>
        <el-upload :show-file-list="false" :http-request="importFile"
                   accept=".xlsx,.csv">
          <el-button plain :loading="busy">
            <el-icon><Upload /></el-icon>&nbsp;上传 Excel / CSV
          </el-button>
        </el-upload>
        <span v-if="state.held_text" class="dim">活动时间：{{ state.held_text }}</span>
      </div>
    </el-card>

    <!-- 上一次导入的结果：导进去的、跳过的（带原因）、找不到的学号 -->
    <el-card v-if="result" shadow="never" style="margin-top:12px">
      <div class="toolbar">
        <b>{{ result.message }}</b>
        <el-button link style="margin-left:auto" @click="result = null">收起</el-button>
      </div>
      <div v-if="result.not_found.length" style="margin-top:8px">
        <span class="dim">平台里找不到这些学号（未建档，请核对后重新导入）：</span>
        <el-tag v-for="x in result.not_found" :key="x.line" type="danger" effect="plain"
                size="small" style="margin:4px 4px 0 0">
          第 {{ x.line }} 行 {{ x.student_id }}
        </el-tag>
      </div>
      <el-table v-if="result.skipped.length" :data="result.skipped" size="small"
                style="margin-top:8px">
        <el-table-column prop="line" label="行" width="60" />
        <el-table-column prop="student_id" label="学号" width="130" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="reason" label="跳过原因" min-width="220" />
      </el-table>
    </el-card>

    <div class="section-title" style="margin-top:16px">
      当前名单（{{ state.items.length }} 人，合计 {{ state.total_hours }} 小时）
    </div>
    <el-empty v-if="!state.items.length" :image-size="60" description="还没有导入名单" />
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
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="edit.visible" :title="`修改 ${edit.row?.user?.name || ''} 的时长`"
               width="420">
      <el-form label-width="70px">
        <el-form-item label="时长">
          <el-input-number v-model="edit.hours" :min="0.5" :max="state.max_hours"
                           :step="0.5" :precision="1" />
          <span class="dim" style="margin-left:8px">小时</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="edit.note" maxlength="120" placeholder="选填" />
        </el-form-item>
      </el-form>
      <div class="dim">改了时长，本人之前的确认作废；已核准的话，再核准一次才会通知他。</div>
      <template #footer>
        <el-button @click="edit.visible = false">取消</el-button>
        <el-button type="primary" :loading="edit.busy" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import { api } from '../api'
import { uploadTo } from '../composables/uploadImage'

const props = defineProps({
  activityId: { type: String, required: true },
})
const emit = defineEmits(['changed'])

const state = ref({ items: [], total_hours: 0, editable: false, block_reason: '',
                    max_hours: 24, held_text: '' })
const text = ref('')
const busy = ref(false)
const result = ref(null)
const edit = ref({ visible: false, row: null, hours: 1, note: '', busy: false })
const base = () => `/api/staff/activities/${props.activityId}/record-roster`

async function load() {
  state.value = await api.get(base())
}

function done(d) {
  result.value = d
  const warn = d.skipped.length || d.not_found.length
  ElMessage[warn ? 'warning' : 'success'](d.message)
  load()
  emit('changed')
}

async function importText() {
  busy.value = true
  try {
    done(await api.post(`${base()}/import`, { text: text.value }))
    text.value = ''
  } finally { busy.value = false }
}

async function importFile({ file }) {
  busy.value = true
  try {
    done(await uploadTo(`${base()}/import-file`, file))
  } catch (e) {
    ElMessage.error(e.message || '导入失败')
  } finally { busy.value = false }
}

function openEdit(row) {
  edit.value = { visible: true, row, hours: row.hours, note: row.note || '', busy: false }
}

async function saveEdit() {
  edit.value.busy = true
  try {
    await api.patch(`${base()}/${edit.value.row.id}`,
                    { hours: edit.value.hours, note: edit.value.note })
    ElMessage.success('已修改')
    edit.value.visible = false
    load()
    emit('changed')
  } finally { edit.value.busy = false }
}

async function remove(row) {
  await api.del(`${base()}/${row.id}`)
  ElMessage.success('已从名单删除')
  load()
  emit('changed')
}

onMounted(load)
defineExpose({ load })
</script>
