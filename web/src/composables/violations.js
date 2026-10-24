/** 撤销一条违规登记：先确认，撤了提示分按记录现算，随之恢复。
 * 返回是否真的撤了（用户点了取消返回 false）。 */
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

export async function revokeViolation(v, who) {
  try {
    await ElMessageBox.confirm(
      `撤销${who ? ` ${who} 的` : '这条'}「${v.kind_label}」违规登记？撤销后不再计入提示分，操作会留痕。`,
      '撤销违规登记',
      { type: 'warning', confirmButtonText: '撤销', cancelButtonText: '取消' })
  } catch {
    return false
  }
  await api.del(`/api/staff/violations/${v.id}`)
  ElMessage.success('已撤销')
  return true
}
