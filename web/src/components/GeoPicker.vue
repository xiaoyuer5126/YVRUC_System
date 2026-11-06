<!-- 在地图上定一个点：快捷来源（当前位置、校区）/ 按地名搜 / 图上点或拖图钉。
     签到点（CheckinPanel，带范围圈）和活动地点（ActivityForm）共用。

     v-model 是一个对象，这里只改其中的 lat、lng、accuracy、source，别的键原样带回：
       source 记坐标是怎么来的——gps 站在现场量的 / search 地名搜索 /
       map 在图上点或拖的 / campus 校区预设点。签到点的后端按它写审计。
     选中地名搜索的结果时另外抛 place 事件（地名），活动地点还没填字的可以顺手填上。
-->
<template>
  <div>
    <!-- 三行：快捷来源 / 按地名搜 / 坐标。校区按钮只把地图带到校区，
         落点还得拖着图钉挪到具体位置 -->
    <div class="geo-line">
      <el-button size="small" :loading="locating" @click="locateMe">使用我的当前位置</el-button>
      <el-button v-for="c in campuses" :key="c.name" size="small" plain
                 @click="set(c.lat, c.lng, 'campus')">{{ c.name }}</el-button>
    </div>
    <div class="geo-line">
      <el-input v-model="query" size="small" style="width:200px"
                placeholder="地名，如「明德楼」" clearable @keyup.enter="search" />
      <el-button size="small" :loading="searching" @click="search">搜索</el-button>
    </div>
    <!-- 同名异地很常见（「明德楼」全国一堆），所以地址必须列出来 -->
    <div v-if="results.length" class="geo-results">
      <div v-for="(r, i) in results" :key="i" class="geo-result" @click="pickResult(r)">
        <b>{{ r.name }}</b>
        <span class="dim">{{ r.address }}</span>
      </div>
    </div>
    <div v-else-if="searched === 'unconfigured'" class="dim">
      没有配置天地图的 tk，搜不了地名。可以用当前位置或校区按钮，再在图上挪。
    </div>
    <!-- 只在搜过且为空时说：地名库对校园里的楼不一定收，加上校区名
         （「人民大学明德楼」）常能搜到 -->
    <div v-else-if="searched === 'empty'" class="dim">
      没找到这个地名。加上校区或城市再试（如「人民大学明德楼」），
      或者直接在地图上点、拖着图钉挪。
    </div>
    <div v-if="modelValue.lat" class="dim">
      {{ modelValue.lat.toFixed(5) }}, {{ modelValue.lng.toFixed(5) }}
      <template v-if="modelValue.accuracy">
        （手机报的误差约 ±{{ Math.round(modelValue.accuracy) }} 米）
      </template>
    </div>
    <template v-if="modelValue.lat">
      <GeoMap :lat="modelValue.lat" :lng="modelValue.lng" :radius="radius" pickable
              style="margin-top:8px" @pick="(lat, lng) => set(lat, lng, 'map')" />
      <div class="dim">可以直接在地图上点击，也可以拖动图钉位置。</div>
    </template>
  </div>
</template>

<script>
// 校区快捷选点的坐标来自后端（config.CAMPUS_PRESETS）：改一个校区的位置
// 不该要重新构建前端。放在模块里，几个选点框、几次打开共用一份
let campusCache = null
</script>

<script setup>
import { defineAsyncComponent, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

// leaflet 六十来 KB，只有真要选点时才用得上，别让它跟着页面一起进包
const GeoMap = defineAsyncComponent(() => import('./GeoMap.vue'))

const props = defineProps({
  modelValue: { type: Object, required: true },   // { lat, lng, accuracy, source, … }
  radius: { type: Number, default: 0 },           // 范围圈（米）；0 = 不画圈
})
const emit = defineEmits(['update:modelValue', 'place'])

function set(lat, lng, source, accuracy = 0) {
  emit('update:modelValue', { ...props.modelValue, lat, lng, accuracy, source })
}

const campuses = ref([])
onMounted(async () => {
  try {
    campusCache ||= api.get('/api/staff/map-config', { silent: true })
      .then(d => d.campuses || [])
    campuses.value = await campusCache
  } catch {
    campusCache = null   // 取不到就没有快捷选点，下次打开再试
  }
})

// —— 按地名搜坐标 ——
const query = ref('')
const results = ref([])
const searching = ref(false)
// 搜过之后的状态：'' 没搜过 / 'empty' 搜了没结果 / 'unconfigured' 没配 tk。
// 「没搜到」和「搜不了」要分开说，不然人会一遍遍换关键词
const searched = ref('')

/** 搜地名。已经有点了就带上它：搜出来的范围限定在这附近，
 *  不然「明德楼」会把桂林、深圳的都端上来。 */
async function search() {
  const q = query.value.trim()
  if (q.length < 2) return ElMessage.warning('请至少输入两个字')
  searching.value = true
  try {
    const { lat, lng } = props.modelValue
    const d = await api.get(`/api/staff/geo/search?q=${encodeURIComponent(q)}`
                            + (lat ? `&lat=${lat}&lng=${lng}` : ''), { silent: true })
    results.value = d.items || []
    searched.value = d.configured === false ? 'unconfigured'
      : results.value.length ? '' : 'empty'
  } catch { /* 搜索坏了不影响别的选点办法，api 层已经提示过 */ } finally {
    searching.value = false
  }
}

function pickResult(r) {
  set(r.lat, r.lng, 'search')
  results.value = []
  searched.value = ''
  query.value = r.name
  emit('place', r.name)
}

// —— 当前位置 ——
const locating = ref(false)

/** 量当前位置。这个坐标只是「此刻站在哪」，签到判定用的仍是服务端。 */
function locateMe() {
  if (!navigator.geolocation) {
    return ElMessage.error('这个浏览器不能定位，请换手机浏览器再试')
  }
  locating.value = true
  navigator.geolocation.getCurrentPosition(pos => {
    locating.value = false
    set(pos.coords.latitude, pos.coords.longitude, 'gps', pos.coords.accuracy || 0)
  }, e => {
    locating.value = false
    if (!window.isSecureContext) {
      return ElMessage.error('定位只能在 https 下使用，请用平台正式网址打开')
    }
    ElMessage.error(e?.code === 1 ? '您没有允许定位，请在浏览器设置里允许后重试'
                   : e?.code === 3 ? '定位超时，请到空旷处重试'
                   : '取不到定位，请到空旷处或窗边重试')
  }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 })
}
</script>

<style scoped>
.geo-line { display:flex; gap:8px; align-items:center; flex-wrap:wrap;
            margin-bottom:8px; width:100%; }
/* 搜索结果：一条一行，点一下就用它的坐标。地址必须显示——同名异地太多了 */
.geo-results { margin-bottom:8px; max-height:180px; overflow-y:auto; width:100%;
               border:1px solid #eceef1; border-radius:4px; }
.geo-result { padding:4px 12px; cursor:pointer; line-height:1.6; font-size:13px; }
.geo-result + .geo-result { border-top:1px solid #f2f3f5; }
.geo-result:hover { background:#f7f8fa; }
.geo-result .dim { margin-left:8px; }
</style>
