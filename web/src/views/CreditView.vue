<template>
  <div class="page" v-if="data">
    <h2>我的信用分</h2>
    <el-row :gutter="18">
      <el-col :span="9">
        <el-card shadow="never" style="text-align:center;border-radius:14px">
          <div class="score-ring" :style="{ borderColor: scoreColor }">
            <div class="num" :style="{ color: scoreColor }">{{ data.score }}</div>
            <div class="dim">满分 100</div>
          </div>
          <div style="margin-top:10px">
            <el-alert v-if="data.frozen" type="error" :closable="false" center
                      title="报名权限已冻结"
                      :description="`信用分低于60分，冻结至 ${fmtDT(data.frozen_until)}`" />
            <el-alert v-else type="success" :closable="false" center title="状态正常，可正常报名" />
          </div>
          <el-descriptions :column="1" size="small" border style="margin-top:14px;text-align:left">
            <el-descriptions-item label="连续放弃候补">{{ data.abandon_count }} 次（3次暂停资格）</el-descriptions-item>
            <el-descriptions-item v-if="data.restricted_types?.length" label="限制准入">
              不得参与 {{ data.restricted_types.join('/') }} 类活动，
              至 {{ fmtDT(data.restricted_until) }}
            </el-descriptions-item>
          </el-descriptions>
          <p class="dim" style="text-align:left;line-height:1.8;font-size:12px;margin-top:12px">
            规则：开始前24小时内退选 −5 分；放弃候补累计3次暂停候补；
            低于60分冻结报名30天。依据《志愿者服务规范》与平台设计方案。
          </p>
        </el-card>
      </el-col>
      <el-col :span="15">
        <el-card shadow="never" style="border-radius:14px;min-height:420px">
          <template #header><b>信用流水</b></template>
          <DataCards v-if="narrow" :rows="data.logs" title-key="reason"
                     :fields="[{ key: 'after', label: '余额' },
                               { key: 'created_at', label: '时间', format: fmtDT }]"
                     empty="暂无信用分变动">
            <template #extra="{ row }">
              <el-tag :type="row.delta > 0 ? 'success' : 'danger'" effect="light">
                {{ row.delta > 0 ? '+' : '' }}{{ row.delta }}
              </el-tag>
            </template>
          </DataCards>
          <el-table v-else :data="data.logs" size="small">
            <el-table-column label="变动" width="80">
              <template #default="{ row }">
                <b :style="{ color: row.delta >= 0 ? '#67c23a' : '#f56c6c' }">
                  {{ row.delta >= 0 ? '+' : '' }}{{ row.delta }}
                </b>
              </template>
            </el-table-column>
            <el-table-column prop="after" label="余额" width="70" />
            <el-table-column prop="reason" label="事由" min-width="220" />
            <el-table-column label="时间" width="120">
              <template #default="{ row }">{{ fmtDT(row.created_at) }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!data.logs.length" description="暂无记录，保持好习惯！" :image-size="70" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'

const narrow = useNarrow()
const data = ref(null)
const scoreColor = computed(() => {
  const s = data.value?.score ?? 100
  return s >= 60 ? 'var(--el-color-primary)' : '#f56c6c'
})
onMounted(async () => { data.value = await api.get('/api/me/credit') })
</script>

<style scoped>
.score-ring {
  width: 150px; height: 150px; margin: 8px auto 0; border-radius: 50%;
  border: 6px solid var(--ruc-red); display: flex; flex-direction: column;
  align-items: center; justify-content: center;
}
.num { font-size: 44px; font-weight: 700; line-height: 1.1; }
</style>
