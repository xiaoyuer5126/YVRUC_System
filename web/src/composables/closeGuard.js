import { ElMessageBox } from 'element-plus'

/**
 * 填写类弹窗关闭前先问一句：弹窗打开（@opened）时给表单拍一张快照，关的时候
 * （点弹窗外面、右上角叉、Esc，el-dialog / el-drawer 这三种关法都走 before-close）
 * 和快照不一样就提示还没保存，一样就直接关。底部「取消」是明确不要了，不经过这里。
 *
 * data：要比对的表单内容（getter）。用法：
 *   const guardShift = useCloseGuard(() => groupForm.value)
 *   <el-dialog :before-close="guardShift" @opened="guardShift.snap">
 * 在 opened 拍而不在打开那一刻拍：表单里的控件挂载时会补默认值，那不算改动。
 *
 * 打开时表单里就已经是没保存的东西（智能起草填进来的草稿等），打开前调一次
 * guard.markDirty()：这一次的快照记成「有改动」，一个字不改关掉也会提示。
 */
export function useCloseGuard(data) {
  const DIRTY = Symbol('dirty')
  let snap = null
  let dirtyNext = false
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
  beforeClose.snap = () => {
    snap = dirtyNext ? DIRTY : now()
    dirtyNext = false
  }
  beforeClose.markDirty = () => { dirtyNext = true }
  return beforeClose
}
