/** 窄屏判断。
 *
 * 只有一个断点：860px 以下算手机。表格在这个宽度以下已经没法看了——
 * 与其让它横向滚动，不如整片换成卡片。
 *
 * 用 matchMedia 而不是监听 resize：旋转屏幕、分屏、桌面缩放窗口都会触发，
 * 而且不会每像素都回调一次。
 */
import { onUnmounted, ref } from 'vue'

export const NARROW_PX = 860

export function useNarrow(px = NARROW_PX) {
  const query = window.matchMedia(`(max-width: ${px}px)`)
  const narrow = ref(query.matches)
  const onChange = e => { narrow.value = e.matches }
  query.addEventListener('change', onChange)
  onUnmounted(() => query.removeEventListener('change', onChange))
  return narrow
}

/** 对话框宽度：窄屏几乎满屏，宽屏用给定值。 */
export function dialogWidth(wide = '520px') {
  return window.matchMedia(`(max-width: ${NARROW_PX}px)`).matches ? '94%' : wide
}
