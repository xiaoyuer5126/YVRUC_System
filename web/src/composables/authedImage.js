/** 需要鉴权的图片地址。
 *
 *  <img src> 带不了 Authorization 头，所以带凭据把图取回来、转成本地 blob
 *  地址再显示（和反馈截图同一套，见 components/FeedbackShots.vue）。
 *  只取本站 /api/ 下的地址：取图要带登录令牌，地址要是被人改成了外站，
 *  令牌就跟着发出去了。
 */
import { onUnmounted, ref, watch } from 'vue'
import { authHeaders } from '../api'

/** 能带着令牌去取的地址：本站接口，不含协议和主机名。 */
export function isOwnApi(url) {
  return typeof url === 'string' && url.startsWith('/api/') && !url.startsWith('//')
}

/** 联络群二维码的取图地址。活动里存的是二维码的文件编号，地址在这里拼。 */
export function qrImageUrl(id) {
  return id ? `/api/staff/contact-qr/${encodeURIComponent(id)}` : ''
}

/** 按二维码编号取图，source 传取值函数，比如 () => f.value.contact_group_qr。 */
export function useQrImage(source) {
  return useAuthedImage(() => qrImageUrl(source()))
}

/** source 传一个取值函数，返回本站 /api/ 下的图片地址。 */
export function useAuthedImage(source) {
  const src = ref('')
  let made = ''        // 已经发出去的 blob 地址，换图或卸载时要收回来
  // 第几次取图。换图或卸载都让它加一：慢的那次取回来时序号已经过时，
  // 要是照样显示，旧图会盖掉新图（卸载后还会漏掉一个收不回的 blob 地址）
  let seq = 0

  function release() {
    seq += 1
    if (made.startsWith('blob:')) URL.revokeObjectURL(made)
    made = ''
    src.value = ''
  }

  async function load(url) {
    release()
    const mine = seq
    if (!isOwnApi(url)) return
    try {
      const resp = await fetch(url, { headers: authHeaders() })
      if (mine !== seq || !resp.ok) return   // 已换图，或取不到（没权限、文件丢了）就留空
      const blob = await resp.blob()
      const local = URL.createObjectURL(blob)
      if (mine !== seq) {
        URL.revokeObjectURL(local)
        return
      }
      made = local
      src.value = local
    } catch {
      // 网络断了同样只是显示不出这张图，不打断页面
    }
  }

  watch(source, url => load(url), { immediate: true })
  onUnmounted(release)
  return src
}
