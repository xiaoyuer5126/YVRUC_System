<!-- 角色管理。管理员与会长团管全部；部长只管本部门的骨干与成员；骨干只认领本部门成员。 -->
<template>
  <div class="page">
    <h2>角色管理</h2>
    <el-alert type="warning" :closable="false">
      <template v-if="coreOnly">
        你是<b>{{ myDeptName }}</b>骨干：此处列出本部门成员，点「归我管理」将成员归到自己名下，
        其可见范围与你一致。其他身份与层级请联系部长。
      </template>
      <template v-else-if="headOnly">
        你是<b>{{ myDeptName }}</b>部长：可设置或撤销本部门的骨干与成员。
        仅能调整青协身份，管理员、活动发起方、学生身份请联系会长团。
      </template>
      <template v-else>
        本页调整的是<b>权限</b>：谁能进入工作台、谁能审批、谁是部长。
        用户类型（老师/学生）由统一身份认证下发，平台不作修改；<b>不能移除自己的管理员身份</b>，
        如需交接请由另一位管理员操作。
      </template>
    </el-alert>

    <div class="toolbar">
      <el-input v-model="q" placeholder="按姓名或学号搜索"
                style="width:280px" clearable @keyup.enter="reload" @clear="reload" />
      <el-button type="primary" @click="reload">查询</el-button>
      <!-- 批量：学号一行一个，添加/移除身份；骨干用不上（他们只认领成员） -->
      <el-button v-if="!coreOnly" @click="openBatch">批量调整</el-button>
      <RefreshBar :load="load" :at="loadedAt" />
      <span class="dim">{{ coreOnly ? '只列本部门的成员' : headOnly ? '不搜索时只列本部门的人，添加新成员请搜索学号' : '不搜索时只列已有管理类身份的人' }}</span>
    </div>

    <DataCards v-if="narrow" :rows="rows" title-key="name" v-loading="loading"
               row-key="id"
               :fields="[{ key: 'student_id', label: '学号' },
                         { key: 'college', label: '单位' },
                         { key: 'role_label', label: '身份' },
                         { key: 'post_label', label: '青协层级', hideEmpty: true },
                         { key: 'department', label: '部门', hideEmpty: true },
                         { key: 'supervisor', label: '归属骨干', hideEmpty: true }]"
               empty="没有匹配的人">
      <template #extra="{ row }">
        <el-tag v-if="row.is_me" size="small" type="warning" effect="plain">这是你</el-tag>
        <el-tag v-if="coreOnly && row.supervisor_id === auth.user.id" size="small"
                type="success" effect="plain">归我管理</el-tag>
      </template>
      <template #actions="{ row }">
        <el-button v-if="coreOnly" size="small" type="primary" plain
                   :disabled="row.supervisor_id === auth.user.id"
                   @click="claim(row)">归我管理</el-button>
        <el-button v-else size="small" type="primary" plain @click="open(row)">调整身份</el-button>
      </template>
    </DataCards>

    <el-table v-else :data="rows" size="small" v-loading="loading" max-height="620">
      <el-table-column prop="name" label="姓名" width="100">
        <template #default="{ row }">
          {{ row.name }}
          <el-tag v-if="row.is_me" size="small" type="warning" effect="plain">你</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="student_id" label="学号" width="120" />
      <el-table-column prop="college" label="单位" min-width="140" />
      <el-table-column label="类型" width="70">
        <template #default="{ row }">{{ row.user_type === 'teacher' ? '老师' : '学生' }}</template>
      </el-table-column>
      <el-table-column label="身份" min-width="180">
        <template #default="{ row }">
          <el-tag v-for="r in row.roles" :key="r" size="small" effect="plain"
                  :type="ROLE_TAG[r]" style="margin-right:4px">
            {{ ROLE_LABELS[r] || r }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="青协层级" min-width="150">
        <template #default="{ row }">
          <template v-if="row.roles.includes('qingxie')">
            {{ row.post_label }}
            <span v-if="row.department" class="dim">· {{ row.department }}</span>
            <span v-if="row.supervisor" class="dim">· 归属 {{ row.supervisor }}</span>
          </template>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button v-if="coreOnly" size="small" link type="primary"
                     :disabled="row.supervisor_id === auth.user.id" @click="claim(row)">
            {{ row.supervisor_id === auth.user.id ? '已归我管理' : '归我管理' }}
          </el-button>
          <el-button v-else size="small" link type="primary" @click="open(row)">调整身份</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination layout="total, prev, pager, next" :total="total" :page-size="SIZE"
                   v-model:current-page="page" @current-change="load" size="small"
                   style="margin-top:12px;justify-content:flex-end" />

    <el-dialog v-model="dlg.visible" title="调整身份" width="480">
      <p>
        <b>{{ dlg.row?.name }}</b>
        <span class="dim">（{{ dlg.row?.student_id }}·{{ dlg.row?.college }}）</span>
      </p>
      <el-form label-width="88px">
        <el-form-item label="身份" required>
          <el-checkbox-group v-model="dlg.roles">
            <!-- 部长只能动青协身份：别的身份（含学生）显示但锁住 -->
            <el-checkbox v-for="r in ROLE_ORDER" :key="r" :value="r"
                         :disabled="(r === 'admin' && dlg.row?.is_me)
                                    || (headOnly && r !== 'qingxie')">
              {{ ROLE_LABELS[r] }}
            </el-checkbox>
          </el-checkbox-group>
          <div v-if="dlg.row?.is_me && !headOnly" class="dim">不能移除自己的管理员身份</div>
        </el-form-item>
        <template v-if="dlg.roles.includes('qingxie')">
          <el-form-item label="青协层级">
            <el-radio-group v-model="dlg.post">
              <el-radio value="member">部门成员</el-radio>
              <el-radio value="core">部门骨干</el-radio>
              <el-radio value="head" :disabled="headOnly">部长</el-radio>
              <el-radio value="council" :disabled="headOnly">会长团</el-radio>
            </el-radio-group>
            <div class="dim">
              会长团：除平台公告、操作日志、意见反馈外与管理员同权 · 部长：审批本部门活动，管理本部门骨干与成员 ·
              骨干：只管理自己参与的活动 · 成员：可见范围与归属骨干一致，可查看但不能保存
            </div>
          </el-form-item>
          <el-form-item v-if="dlg.post !== 'council'" label="所属部门" required>
            <el-select v-model="dlg.deptId" placeholder="选择部门" style="width:100%"
                       :disabled="headOnly">
              <el-option v-for="d in depts" :key="d.id" :value="d.id" :label="d.name" />
            </el-select>
            <div class="dim">骨干与成员须归属部门，部长才能管理</div>
          </el-form-item>
          <!-- 成员是跟着某个人学做事的，可见范围就是那位骨干的可见范围 -->
          <el-form-item v-if="dlg.post === 'member'" label="归属骨干" required>
            <el-select v-model="dlg.supervisorId" placeholder="选择本部门的骨干"
                       style="width:100%" :disabled="!dlg.deptId">
              <el-option v-for="c in cores" :key="c.id" :value="c.id"
                         :label="`${c.name}（${c.student_id}）`" />
            </el-select>
            <div class="dim">
              成员的可见范围与归属骨干一致。
              <template v-if="dlg.deptId && !cores.length">该部门还没有骨干，请先设置一位。</template>
            </div>
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="dlg.busy" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="bd.visible" title="批量调整身份" width="560">
      <el-form label-width="88px">
        <el-form-item label="操作">
          <el-radio-group v-model="bd.action" @change="bd.preview = null">
            <el-radio value="add">添加身份</el-radio>
            <el-radio value="remove">移除身份</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="身份">
          <el-radio-group v-model="bd.role" @change="bd.preview = null">
            <el-radio value="qingxie">青协</el-radio>
            <el-radio v-if="!headOnly" value="initiator">活动发起方</el-radio>
          </el-radio-group>
          <div class="dim">管理员与「学生」不批量：管理员逐个交接，学生由统一身份认证下发</div>
        </el-form-item>
        <template v-if="bd.action === 'add' && bd.role === 'qingxie'">
          <el-form-item label="青协层级">
            <el-radio-group v-model="bd.post" @change="bd.preview = null">
              <el-radio value="member">部门成员</el-radio>
              <el-radio value="core">部门骨干</el-radio>
              <el-radio value="council" :disabled="headOnly">会长团</el-radio>
            </el-radio-group>
            <div class="dim">部长每个部门只有一位，请逐个设置</div>
          </el-form-item>
          <el-form-item v-if="bd.post !== 'council'" label="所属部门" required>
            <el-select v-model="bd.deptId" placeholder="选择部门" style="width:100%"
                       :disabled="headOnly" @change="bd.preview = null">
              <el-option v-for="d in depts" :key="d.id" :value="d.id" :label="d.name" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="bd.post === 'member'" label="归属骨干" required>
            <el-select v-model="bd.supervisorId" placeholder="选择本部门的骨干"
                       style="width:100%" :disabled="!bd.deptId"
                       @change="bd.preview = null">
              <el-option v-for="c in bdCores" :key="c.id" :value="c.id"
                         :label="`${c.name}（${c.student_id}）`" />
            </el-select>
            <div v-if="bd.deptId && !bdCores.length" class="dim">
              该部门还没有骨干，请先设置一位。
            </div>
          </el-form-item>
        </template>
        <el-form-item label="学号" required>
          <el-input v-model="bd.text" type="textarea" :rows="6"
                    placeholder="一行一个学号" />
          <div class="dim">已识别 {{ batchIds.length }} 个学号（重复的按一个算）</div>
        </el-form-item>
      </el-form>
      <!-- 预览结果：执行前先看一遍谁会变、谁会被跳过及原因 -->
      <template v-if="bd.preview">
        <div class="batch-out">
          <div><b>将调整 {{ bd.preview.updated.length }} 人</b></div>
          <div v-for="x in bd.preview.updated.slice(0, 8)" :key="x.student_id" class="dim">
            · {{ x.name }}（{{ x.student_id }}）：{{ x.change }}
          </div>
          <div v-if="bd.preview.updated.length > 8" class="dim">
            …其余 {{ bd.preview.updated.length - 8 }} 人
          </div>
          <template v-if="bd.preview.skipped.length">
            <div><b>跳过 {{ bd.preview.skipped.length }} 人</b></div>
            <div v-for="x in bd.preview.skipped.slice(0, 8)" :key="x.student_id" class="dim">
              · {{ x.name }}（{{ x.student_id }}）：{{ x.reason }}
            </div>
            <div v-if="bd.preview.skipped.length > 8" class="dim">
              …其余 {{ bd.preview.skipped.length - 8 }} 人
            </div>
          </template>
          <template v-if="bd.preview.not_found.length">
            <div><b>没找到 {{ bd.preview.not_found.length }} 个学号</b></div>
            <div class="dim">· {{ bd.preview.not_found.slice(0, 12).join('、') }}</div>
          </template>
        </div>
      </template>
      <template #footer>
        <el-button @click="bd.visible = false">取消</el-button>
        <el-button :loading="bd.busy" @click="runBatch(true)">预览</el-button>
        <el-button type="primary" :loading="bd.busy"
                   :disabled="!bd.preview || !bd.preview.updated.length"
                   @click="runBatch(false)">确认执行</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../store'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'
import RefreshBar from '../components/RefreshBar.vue'

const ROLE_ORDER = ['admin', 'qingxie', 'initiator', 'student']
const ROLE_LABELS = { admin: '管理员', qingxie: '青协', initiator: '活动发起方', student: '学生' }
const ROLE_TAG = { admin: 'danger', qingxie: 'success', initiator: 'primary', student: 'info' }

const SIZE = 20
const narrow = useNarrow()
const rows = ref([])
const total = ref(0)
const page = ref(1)
const depts = ref([])
const q = ref('')
const loading = ref(false)
const loadedAt = ref(0)
const dlg = ref({ visible: false, row: null, roles: [], post: 'member', deptId: '', busy: false })
// 部长视角：只能在本部门里设骨干/成员；骨干视角：只认领本部门成员
const headOnly = computed(() =>
  auth.activeIdentity === 'qingxie' && auth.user?.qingxie_post === 'head')
const coreOnly = computed(() =>
  auth.activeIdentity === 'qingxie' && auth.user?.qingxie_post === 'core')

// 骨干认领成员：身份、层级、部门原样送回，只改归属
async function claim(row) {
  await api.put(`/api/staff/roles/${row.id}`,
                { roles: row.roles, qingxie_post: row.qingxie_post,
                  department_id: row.department_id, supervisor_id: auth.user.id })
  ElMessage.success(`${row.name} 已归你管理`)
  load()
}
const myDeptName = computed(() =>
  depts.value.find(d => d.id === auth.user?.department_id)?.name || '')

async function load() {
  loading.value = true
  try {
    const d = await api.get(`/api/staff/roles?page=${page.value}&size=${SIZE}`
      + `&q=${encodeURIComponent(q.value.trim())}`)
    rows.value = d.items
    total.value = d.total
    loadedAt.value = Date.now()
  } finally { loading.value = false }
}

// 换了搜索词，结果集变了，停在后面的页可能是空的：回到第 1 页
function reload() {
  page.value = 1
  load()
}

onMounted(async () => {
  depts.value = await api.get('/api/staff/departments')
  await load()
})

function open(row) {
  dlg.value = {
    visible: true, row,
    roles: [...row.roles],
    post: row.qingxie_post || 'member',
    // 部长拉人进部门：部门就是他自己的，不用选
    deptId: row.department_id || (headOnly.value ? auth.user.department_id : ''),
    supervisorId: row.supervisor_id || '',
    busy: false,
  }
  loadCores()
}

// 归属骨干的候选：跟着所选部门走
const cores = ref([])
async function loadCores() {
  const dept = dlg.value.deptId
  cores.value = dept
    ? await api.get(`/api/staff/roles/cores?department_id=${dept}`)
    : []
}
watch(() => dlg.value.deptId, loadCores)

async function save() {
  const d = dlg.value
  if (!d.roles.length) return ElMessage.warning('至少要保留一个身份')
  d.busy = true
  try {
    await api.put(`/api/staff/roles/${d.row.id}`,
                  { roles: d.roles, qingxie_post: d.post, department_id: d.deptId,
                    supervisor_id: d.supervisorId })
    ElMessage.success('已保存')
    d.visible = false
    load()
  } finally { d.busy = false }
}

// —— 批量调整：学号一行一个，先预览（不落库）再执行 ——
// 逐人校验在服务端：没有权限、会变成孤儿活动的记进「跳过」并写明原因，
// 不连累同批其他人；执行时逐人写审计
const bd = ref({ visible: false, action: 'add', role: 'qingxie', post: 'member',
                 deptId: '', supervisorId: '', text: '', busy: false, preview: null })
const batchIds = computed(() =>
  bd.value.text.split('\n').map(s => s.trim()).filter(Boolean))

const bdCores = ref([])
async function loadBdCores() {
  bdCores.value = bd.value.deptId
    ? await api.get(`/api/staff/roles/cores?department_id=${bd.value.deptId}`)
    : []
}
watch(() => bd.value.deptId, loadBdCores)
watch(() => bd.value.text, () => { bd.value.preview = null })

function openBatch() {
  bd.value = { visible: true, action: 'add', role: 'qingxie', post: 'member',
               // 部长只能设本部门：部门就是他自己的，不用选
               deptId: headOnly.value ? (auth.user?.department_id || '') : '',
               supervisorId: '', text: '', busy: false, preview: null }
  loadBdCores()
}

async function runBatch(dry) {
  const b = bd.value
  if (!batchIds.value.length) return ElMessage.warning('请粘贴学号，一行一个')
  b.busy = true
  try {
    const d = await api.post('/api/staff/roles/batch', {
      action: b.action, role: b.role, qingxie_post: b.post,
      department_id: b.deptId || '', supervisor_id: b.supervisorId || '',
      student_ids: batchIds.value, dry_run: dry })
    if (dry) {
      b.preview = d
    } else {
      // 执行完就关掉：留着那份预览的话「确认执行」还能再点一次，而这批人
      // 身份已经改好了，再点全部落进「跳过·已经是这个身份」，看着像出错
      b.visible = false
      ElMessage.success(`已调整 ${d.updated.length} 人`
        + (d.skipped.length ? `，跳过 ${d.skipped.length} 人` : '')
        + (d.not_found.length ? `，${d.not_found.length} 个学号没找到` : ''))
      load()
    }
  } finally { b.busy = false }
}
</script>

<style scoped>
.batch-out {
  margin-top: 8px; padding-top: 8px; line-height: 1.7;
  border-top: 1px dashed var(--el-border-color-lighter);
}
</style>
