<template>
  <div class="page page-wide">
    <h2>志愿者档案</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="q" placeholder="姓名 / 学号" clearable style="width:220px"
                  @keyup.enter="reload" @clear="reload">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select v-model="college" clearable placeholder="学院筛选" style="width:180px" @change="reload">
          <el-option v-for="c in colleges" :key="c" :value="c" :label="c" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
        <span class="dim">档案记录参与过的活动、违规记录与锁定后退出次数，不作为报名限制</span>
      </div>

      <DataCards v-if="narrow" :rows="users" title-key="name" v-loading="loading"
                 :fields="[{ key: 'student_id', label: '学号' },
                           { key: 'college', label: '学院' },
                           { key: 'campus', label: '校区', hideEmpty: true },
                           { key: 'phone', label: '手机号' },
                           { key: 'wechat', label: '微信号', hideEmpty: true }]"
                 empty="没有匹配的志愿者">
        <template #actions="{ row }">
          <el-button size="small" type="primary" plain @click="openArchive(row)">查看档案</el-button>
        </template>
      </DataCards>

      <el-table v-else :data="users" size="small" v-loading="loading" max-height="620">
        <el-table-column label="姓名" width="110">
          <template #default="{row}">
            <el-link type="primary" underline="never" @click="openArchive(row)">{{ row.name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="student_id" label="学号" width="110" />
        <el-table-column prop="gender" label="性别" width="60" />
        <el-table-column prop="college" label="学院" min-width="120" />
        <el-table-column label="校区 / 学段" min-width="150">
          <template #default="{row}">{{ row.campus || '—' }} / {{ row.stage || '—' }}</template>
        </el-table-column>
        <el-table-column prop="phone" label="手机号" width="120" />
        <el-table-column label="微信号" width="120">
          <template #default="{row}"><span class="mono">{{ row.wechat || '—' }}</span></template>
        </el-table-column>
        <el-table-column prop="id_card_masked" label="身份证（脱敏）" width="146">
          <template #default="{row}"><span class="mono dim">{{ row.id_card_masked || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{row}">
            <el-button size="small" link type="primary" @click="openArchive(row)">查看档案</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination layout="total, prev, pager, next" :total="total" :page-size="SIZE"
                     v-model:current-page="page" @current-change="load" size="small"
                     style="margin-top:12px;justify-content:flex-end" />
    </el-card>

    <ArchiveDrawer ref="drawer" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { api } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'
import ArchiveDrawer from '../components/ArchiveDrawer.vue'

const SIZE = 20
const narrow = useNarrow()
const users = ref([])
const total = ref(0)
const page = ref(1)
const colleges = ref([])
const q = ref('')
const college = ref('')
const loading = ref(false)
const drawer = ref(null)

async function load() {
  loading.value = true
  try {
    const d = await api.get(`/api/staff/users?page=${page.value}&size=${SIZE}`
      + `&q=${encodeURIComponent(q.value)}`
      + (college.value ? `&college=${encodeURIComponent(college.value)}` : ''))
    users.value = d.items
    total.value = d.total
  } finally { loading.value = false }
}

// 换了搜索词或学院，结果集变了，停在第 5 页可能是空的：回到第 1 页
function reload() {
  page.value = 1
  load()
}
onMounted(async () => {
  // 学院选项从档案里汇总，不用写死的清单（与活动页的招募范围同源）
  colleges.value = (await api.get('/api/staff/colleges')).colleges || []
  load()
})

function openArchive(row) {
  drawer.value.open(`/api/staff/users/${row.id}/archive`)
}
</script>

<style scoped>
.toolbar .dim { flex: 1 1 100%; }   /* 说明文字独占一行，别把工具条撑宽 */
</style>
