import { ElMessageBox } from 'element-plus'

/**
 * 填写类弹窗关闭前先问一句：弹窗打开（@opened）时给表单拍一张快照，关的时候
 * （右上角叉、Esc）和快照不一样就提示还没保存。点弹窗外面不关由各弹窗的
 * close-on-click-modal 管；底部「取消」是明确不要了，不经过这里。
 *
 * data：要比对的表单内容（getter）。用法：
 *   const guardShift = useCloseGuard(() => groupForm.value)
 *   <el-dialog :before-close="guardShift" @opened="guardShift.snap">
 * 在 opened 拍而不在打开那一刻拍：表单里的控件挂载时会补默认值，那不算改动。
 */
export function useCloseGuard(data) {
  let snap = null
  const now = () => JSON.stringify(data() ?? null)
  async function beforeClose(done) {
    if (snap === null || now() === snap) { snap = null; return done() }
    try {
      await ElMessageBox.confirm('有改动还没保存，确定关闭吗？', '还没保存', {
        type: 'warning', confirmButtonText: '不保存，关闭', cancelButtonText: '继续编辑',
      })
      snap = null
      done()
    } catch { /* 继续编辑 */ }
  }
  beforeClose.snap = () => { snap = now() }
  return beforeClose
}
