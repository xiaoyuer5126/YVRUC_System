import { nextTick, watch } from 'vue'
import { ElMessageBox } from 'element-plus'

/**
 * 填写类弹窗关闭前先问一句：打开时给表单拍一张快照，关的时候（右上角叉、Esc）
 * 和快照不一样就提示还没保存。点弹窗外面不关由各弹窗的 close-on-click-modal
 * 管；底部「取消」是明确不要了，不经过这里。
 *
 * visible：弹窗开没开（getter 或 ref）；data：要比对的表单内容（getter）。
 * 返回值给 el-dialog / el-drawer 的 :before-close。
 * 表单在打开之后才异步拉回来的，拉完调一次返回值上的 snap() 重拍。
 */
export function useCloseGuard(visible, data) {
  let snap = null
  const take = () => { snap = JSON.stringify(data() ?? null) }
  watch(visible, v => {
    if (v) nextTick(take)
    else snap = null
  })
  async function beforeClose(done) {
    if (snap === null || JSON.stringify(data() ?? null) === snap) return done()
    try {
      await ElMessageBox.confirm('有改动还没保存，确定关闭吗？', '还没保存', {
        type: 'warning', confirmButtonText: '不保存，关闭', cancelButtonText: '继续编辑',
      })
      done()
    } catch { /* 继续编辑 */ }
  }
  beforeClose.snap = take
  return beforeClose
}
