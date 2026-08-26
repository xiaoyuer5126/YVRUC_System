<template>
  <div class="page" v-if="data">
    <h2>我的志愿时长</h2>
    <el-row :gutter="16" style="margin-bottom:16px">
      <el-col :span="8">
        <el-card shadow="never" class="stat">
          <div class="big">{{ data.total }}</div>
          <div class="dim">累计时长（小时）</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="stat">
          <div class="big">{{ data.uploaded }}</div>
          <div class="dim">已回传数智人大（小时）</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="stat">
          <div class="big">{{ data.records.length }}</div>
          <div class="dim">参与活动场次</div>
        </el-card>
      </el-col>
    </el-row>
    <el-card shadow="never">
      <template #header><b>时长明细</b><span class="dim" style="font-weight:400;margin-left:8px">
        活动结束后由青协依据签到记录一键确认，并回传至数智人大/志愿北京
      </span></template>
      <el-table :data="data.records">
        <el-table-column prop="activity_title" label="活动" min-width="220" />
        <el-table-column label="时长（小时）" width="110">
          <template #default="{ row }"><b>{{ row.hours }}</b></template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.status === 'uploaded'" type="success" effect="light">已回传</el-tag>
            <el-tag v-else type="warning" effect="light">已确认待回传</el-tag>
            <span v-if="row.upload_ref" class="dim mono" style="margin-left:6px">{{ row.upload_ref }}</span>
          </template>
        </el-table-column>
        <el-table-column label="确认时间" width="130">
          <template #default="{ row }">{{ fmtDT(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!data.records.length" description="参加活动并完成签到后，时长会出现在这里" :image-size="80" />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtDT } from '../api'

const data = ref(null)
onMounted(async () => { data.value = await api.get('/api/me/hours') })
</script>

<style scoped>
.stat { text-align: center; border-radius: 14px; }
.big { font-size: 34px; font-weight: 700; color: var(--ruc-red); }
</style>
