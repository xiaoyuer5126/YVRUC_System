<!-- 小地图：把一个点（和它的范围圈）画出来。签到点和活动地点都用它（经 GeoPicker）。
 *
 * 用天地图的瓦片 + Leaflet，**不引天地图官方的 JS API**——它的瓦片是标准
 * WMTS，Leaflet 一个 tileLayer 就吃得下，不必再学一套 API。
 *
 * 瓦片**走本站转发**（后端 routers/geo.py），浏览器不直连天地图、也拿不到 tk：
 * 直连时 tk 靠 Referer 白名单，AdGuard 隐身模式这类插件一改 Referer 就整块 403。
 * <img> 带不上登录令牌，所以先领一张短期瓦片令牌挂在地址上，快过期时再领。
 *
 * 为什么是天地图：它的瓦片是 Web 墨卡托，`L.marker([lat, lng])` **直接吃
 * 浏览器给的 WGS-84**，不用转换。换成高德/腾讯就得先转 GCJ-02（差几十到
 * 几百米），而我们的锚点和学生坐标都是 WGS-84，一转反而对不上。
 *
 * 默认只读；传 pickable 才能在图上点一下或拖图钉改坐标（抛 pick 事件）。
 * 签到点也允许这样定：到不了现场的时候，总得有个办法把点定下来。但图上点的
 * 坐标说明不了「有人到过那儿」，所以调用方要把来源记成 map，后端把它写进
 * 审计（见 routers/staff_ops 的 set_geo）。
 -->
<template>
  <div>
    <div ref="mapEl" class="geo-map" />
    <div v-if="unconfigured" class="dim" style="margin-top:8px">
      没有配置天地图的 tk（.env 里的 YVRUC_TIANDITU_TK_SERVER），先只显示坐标。
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import { api } from '../api'
import 'leaflet/dist/leaflet.css'
// 图钉的三个图：Leaflet 默认是**运行时**去扫页面里的 leaflet.css、按它的路径
// 拼出 icons 地址的。打包之后 CSS 变成带 hash 的 index-xxxx.css，它扫不到，
// 就退回 'images/marker-icon-2x.png' 这种相对路径——于是 404、图钉不显示。
// 显式把 Vite 处理过的地址塞给它，绕开那段运行时推断。
import markerIcon from 'leaflet/dist/images/marker-icon.png'
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png'
import markerShadow from 'leaflet/dist/images/marker-shadow.png'

L.Icon.Default.mergeOptions({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
})

const props = defineProps({
  lat: { type: Number, required: true },
  lng: { type: Number, required: true },
  radius: { type: Number, default: 0 },          // 范围圈（米）；0 = 不画圈
  // 允许在图上点/拖来定坐标。签到点本该站在现场量，图上挪等于坐办公室定点位，
  // 所以来源要记成 map 写进审计（见 set_geo）
  pickable: { type: Boolean, default: false },
})
const emit = defineEmits(['pick'])

const mapEl = ref(null)
const unconfigured = ref(false)
let map = null
let marker = null
let circle = null
let layers = []
let refreshTimer = null

// 瓦片令牌在几次打开之间共用（后端按整点对齐签发，同一小时里领到的一样，
// 地址不变浏览器缓存才用得上）。deadline 是本机时钟下「该重新领了」的时刻：
// 用后端给的相对秒数算，不拿 expires_at 跟用户电脑的时钟比
let shared = null            // { tile_base, token, deadline }
const REFRESH_AHEAD_MS = 10 * 60 * 1000

async function fetchToken() {
  const d = await api.get('/api/geo/tile-token', { silent: true })
  if (!d.configured) return null
  shared = {
    tile_base: d.tile_base,
    token: d.token,
    deadline: Date.now() + Math.max(60, d.expires_in) * 1000 - REFRESH_AHEAD_MS,
  }
  return shared
}

async function tileToken() {
  if (shared && Date.now() < shared.deadline) return shared
  return fetchToken()
}

/** 本站转发的瓦片地址。后端取的是天地图 _w（Web 墨卡托，Leaflet 默认就是
 *  这个），别换成 _c —— 那是经纬度，不显式声明 crs 就会整个偏掉。 */
function tileUrl(t, layer) {
  return `${t.tile_base}/${layer}/{z}/{x}/{y}?t=${encodeURIComponent(t.token)}`
}

function tile(t, layer) {
  // 天地图的级别从 1 开始，0 级后端直接回 400，不去请求
  const lyr = L.tileLayer(tileUrl(t, layer), { minZoom: 1, maxZoom: 18 })
  lyr._yvrucLayer = layer
  return lyr
}

/** 令牌快过期时重新领，换掉图层地址（不重画：已经显示的瓦片留着） */
function scheduleRefresh(t) {
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(async () => {
    try {
      const n = await fetchToken()
      if (!map || !n) return
      layers.forEach(l => l.setUrl(tileUrl(n, l._yvrucLayer), true))
      scheduleRefresh(n)
    } catch {
      // 网络抖了一下：一分钟后再试，别让地图在令牌过期后整块灰掉
      scheduleRefresh({ deadline: Date.now() + 60 * 1000 })
    }
  }, Math.max(1000, t.deadline - Date.now()))
}

async function addTiles() {
  let t = null
  try {
    t = await tileToken()
  } catch {
    // 领不到令牌（网络问题）：跟没配一样只画点和圈，不弹错——地图是锦上添花
    return
  }
  if (!map) return                    // 等令牌的时候弹窗已经关了
  if (!t) {
    // 没配 tk：把点画在一个灰底上，至少能看出「点在哪、圈多大」
    unconfigured.value = true
    return
  }
  // 底图和注记都要：只加底图的话，图上只有路没有地名，看不出这是哪儿
  layers = [tile(t, 'vec'), tile(t, 'cva')]
  layers.forEach(l => l.addTo(map))
  scheduleRefresh(t)
}

/** 把视野框到「圈再往外放一圈」：只看圈内的话，周边什么参照都没有。
 *  没有圈的就看周围三百来米，够认出是哪栋楼、哪个路口 */
function fit() {
  map.fitBounds(L.latLng(props.lat, props.lng)
    .toBounds(Math.max(props.radius * 3, 300)), { maxZoom: 17 })
}

onMounted(() => {
  map = L.map(mapEl.value, { zoomControl: true, attributionControl: false })
  // 瓦片出来之前（以及没配 tk 时）先垫一层灰底
  mapEl.value.style.background = '#eef0f2'
  addTiles()
  marker = L.marker([props.lat, props.lng],
                    { draggable: props.pickable }).addTo(map)
  if (props.radius) {
    circle = L.circle([props.lat, props.lng], {
      radius: props.radius,
      color: '#9e2235', weight: 1.5, fillColor: '#9e2235', fillOpacity: 0.12,
    }).addTo(map)
  }
  if (props.pickable) {
    // 拖图钉比点地图准：点击是「大致那一带」，拖动能看着圈挪到位
    marker.on('dragend', () => {
      const p = marker.getLatLng()
      emit('pick', p.lat, p.lng)
    })
    map.on('click', e => emit('pick', e.latlng.lat, e.latlng.lng))
  }
  fit()
})

// 重新量了坐标、或者改了半径，图上跟着动（不重建实例：重建会闪一下，
// 而且瓦片得重新拉一遍）
watch(() => [props.lat, props.lng], () => {
  if (!map) return
  marker.setLatLng([props.lat, props.lng])
  circle?.setLatLng([props.lat, props.lng])
  fit()
})
watch(() => props.radius, r => {
  if (!map) return
  circle?.setRadius(r)
  fit()
})

// 弹窗关掉要销毁：地图实例不销毁的话，Leaflet 的事件还挂在 document 上，
// 同一个容器再打开一次就会报 "Map container is already initialized"
onBeforeUnmount(() => {
  clearTimeout(refreshTimer)
  try { map?.remove() } catch { /* 已经销毁 */ }
  map = null
  layers = []
})
</script>

<style scoped>
.geo-map { width: 100%; height: 240px; border-radius: 10px; overflow: hidden; }
</style>
