<template>
  <div class="page">
    <!-- 手机上「标题+注释」和筛选挤一行会把注释压成两三行；
         改成第一行标题与筛选各占一头，注释整条落到第二行 -->
    <div class="hero">
      <div class="hero-top">
        <h2>活动广场</h2>
        <el-radio-group v-model="scope" @change="load">
          <el-radio-button value="open">报名中</el-radio-button>
          <el-radio-button value="all">全部</el-radio-button>
        </el-radio-group>
      </div>
      <p>选择感兴趣的活动，报名即锁定时段；名额满员自动进入候补队列。</p>
    </div>

    <div v-if="list.length" class="card-grid">
      <div v-for="a in list" :key="a.id" class="act-card square-card"
           @click="$router.push('/activity/' + a.id)">
        <div class="card-top">
          <el-tag size="small" effect="dark" :color="modeColor(a.recruit_mode)" style="border:none">
            {{ a.recruit_label }}
          </el-tag>
          <el-tag size="small" :type="statusType(a.display_status)">{{ a.display_label }}</el-tag>
        </div>
        <div class="card-title">{{ a.title }}</div>
        <div v-if="a.organizer" class="meta-row"><el-icon><OfficeBuilding /></el-icon>{{ a.organizer }}</div>
        <div class="meta-row"><el-icon><Clock /></el-icon>{{ a.time_text }}</div>
        <div class="meta-row"><el-icon><Location /></el-icon>{{ a.location || '待定' }}</div>
        <div class="meta-row">
          <!-- 还没开始报名：这张卡上最要紧的信息是「还有多久」，不是剩几个名额 -->
          <span v-if="a.opens_in" class="countdown-row">
            距报名开始
            <el-countdown :value="opensAt(a)" :format="fmt(a)" @finish="load" />
          </span>
          <span v-else-if="a.status === 'published'">
            剩余 <b style="color:var(--el-color-primary)">{{ a.remaining }}</b> / {{ a.total_capacity }}
          </span>
          <span v-else class="dim">已报名 {{ a.total_enrolled }} 人</span>
        </div>
      </div>
    </div>
    <el-empty v-else description="暂无活动" />

    <p class="dim" style="margin-top:26px">
      活动类型：<b>即选即得</b>——报名即生效，名额满了进候补；
      <b>报名后筛选</b>——填一份报名表并勾意向场次，截止后由主办方筛选录取。
    </p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { MODE_COLORS } from '../store'

const scope = ref('open')
const list = ref([])
const modeColor = m => MODE_COLORS[m]?.color

// 展示状态 → el-tag type 颜色
// 展示状态 → el-tag 颜色。以后端 display_status 为准，别在这里另算一套
const STATUS_TYPES = {
  upcoming: 'primary',    // 未开始报名
  published: 'success',   // 报名中
  closed: 'warning',      // 已截止
  ongoing: 'danger',      // 进行中
  finished: 'info',       // 已结束
}
const statusType = code => STATUS_TYPES[code] || 'info'

/** 倒计时目标：后端给的「还剩多少秒」加上此刻。
 *
 * 不直接 parse reg_start——客户端时钟偏一点，倒计时就跟服务端对不上。
 * 归零时重拉列表，卡片自己从「未开始报名」翻成「报名中」。
 */
const opensAt = a => Date.now() + a.opens_in * 1000
const fmt = a => (a.opens_in > 86400 ? 'DD 天 HH:mm:ss' : 'HH:mm:ss')

async function load() {
  list.value = await api.get(`/api/activities?scope=${scope.value}`)
}
onMounted(load)
</script>

<style scoped>
.hero { margin-bottom:18px; }
.hero-top { display:flex; justify-content:space-between; align-items:center; gap:10px; }
.hero h2 { margin:0; }
.hero p { margin:6px 0 0; color:#909399; font-size:13px; line-height:1.7; }
.square-card { padding:16px 18px; }
.card-top { display:flex; justify-content:space-between; align-items:center; gap:8px; }
.card-title {
  font-size:16px; font-weight:600; color:#1f2328; line-height:1.4;
  margin:10px 0 4px;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
}
.countdown-row { display:inline-flex; align-items:center; gap:6px; }
.countdown-row :deep(.el-statistic__content) {
  font-size:14px; font-weight:600; color:var(--el-color-primary);
}
</style>
