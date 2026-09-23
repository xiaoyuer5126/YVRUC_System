<!-- 一张只读的小地图：把签到点和它的范围画出来。
 *
 * 用天地图的瓦片 + Leaflet，**不引天地图官方的 JS API**——它的瓦片是标准
 * WMTS，Leaflet 一个 tileLayer 就吃得下，不必再学一套 API。
 *
 * 为什么是天地图：它的瓦片是 Web 墨卡托，`L.marker([lat, lng])` **直接吃
 * 浏览器给的 WGS-84**，不用转换。换成高德/腾讯就得先转 GCJ-02（差几十到
 * 几百米），而我们的锚点和学生坐标都是 WGS-84，一转反而对不上。
 *
 * 只读是有意的：锚点必须**站在现场**量（见后端 set_geo 的说明），能在地图上
 * 拖着改，就等于允许坐办公室把点位挪到任何地方，那道「人到没到现场」的判定
 * 就白设了。要改就回弹窗里点「使用我的当前位置」。
 -->
<template>
  <div>
    <div ref="mapEl" class="geo-map" />
    <div v-if="!tk" class="dim" style="margin-top:6px">
      没有配置天地图的 tk（.env 里的 RUCVA_TIANDITU_TK），先只显示坐标。
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const props = defineProps({
  lat: { type: Number, required: true },
  lng: { type: Number, required: true },
  radius: { type: Number, default: 200 },
  tk: { type: String, default: '' },
})

const mapEl = ref(null)
let map = null
let marker = null
let circle = null

/** 天地图的瓦片地址。_w 是 Web 墨卡托（Leaflet 默认就是这个），
 *  别用 _c —— 那是经纬度，不显式声明 crs 就会整个偏掉。 */
function tile(layer) {
  return L.tileLayer(
    `https://t{s}.tianditu.gov.cn/${layer}_w/wmts?SERVICE=WMTS&REQUEST=GetTile`
    + `&VERSION=1.0.0&LAYER=${layer}&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles`
    + `&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${props.tk}`,
    { subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'], maxZoom: 18 })
}

/** 把视野框到「圈再往外放一圈」：只看圈内的话，周边什么参照都没有 */
function fit() {
  map.fitBounds(
    L.latLng(props.lat, props.lng).toBounds(props.radius * 3), { maxZoom: 17 })
}

onMounted(() => {
  map = L.map(mapEl.value, { zoomControl: true, attributionControl: false })
  if (props.tk) {
    // 底图和注记都要：只加底图的话，图上只有路没有地名，看不出这是哪儿
    tile('vec').addTo(map)
    tile('cva').addTo(map)
  } else {
    // 没配 tk 也把点画在一个灰底上：至少能看出「点在哪、圈多大」，
    // 总比什么都不显示强
    mapEl.value.style.background = '#eef0f2'
  }
  marker = L.marker([props.lat, props.lng]).addTo(map)
  circle = L.circle([props.lat, props.lng], {
    radius: props.radius,
    color: '#9e2235', weight: 1.5, fillColor: '#9e2235', fillOpacity: 0.12,
  }).addTo(map)
  fit()
})

// 重新量了坐标、或者改了半径，图上跟着动（不重建实例：重建会闪一下，
// 而且瓦片得重新拉一遍）
watch(() => [props.lat, props.lng], () => {
  if (!map) return
  marker.setLatLng([props.lat, props.lng])
  circle.setLatLng([props.lat, props.lng])
  fit()
})
watch(() => props.radius, r => {
  if (!map) return
  circle.setRadius(r)
  fit()
})

// 弹窗关掉要销毁：地图实例不销毁的话，Leaflet 的事件还挂在 document 上，
// 同一个容器再打开一次就会报 "Map container is already initialized"
onBeforeUnmount(() => {
  try { map?.remove() } catch { /* 已经销毁 */ }
  map = null
})
</script>

<style scoped>
.geo-map { width: 100%; height: 240px; border-radius: 10px; overflow: hidden; }
</style>
