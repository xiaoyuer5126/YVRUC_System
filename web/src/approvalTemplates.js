/** 审批意见的常用语：点一下整段填进意见框（见 ApprovalCommentDialog）。
 *  文字由校青协定，[活动标题]、[日期] 这类占位在这里填好。 */
const SIGN = '中国人民大学青年志愿者协会'

function today() {
  const d = new Date()
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}

/** 活动立项「通过」 */
export const projectApproved = title => ({
  label: '立项通过',
  text: `同学你好，${title}志愿活动的立项书已经收到，同意开展活动。\n`
    + '让有爱者有力，让奉献者发光，中国人民大学青年志愿者协会始终与你同在，祝志愿活动开展顺利！\n'
    + `${SIGN}\n${today()}`,
})

/** 时长审核「通过」（锁定时长） */
export const closingApproved = title => ({
  label: '结项通过',
  text: `同学你好，${title}志愿活动的结项材料已经收到，同意结项。\n`
    + '让有爱者有力，让奉献者发光，期待更加丰富多彩的志愿活动！\n'
    + `${SIGN}\n${today()}`,
})

/** 外联「确认志愿北京」：填的是志愿北京上的活动名称，不是本平台的活动标题 */
export const vbConfirmed = names => ({
  label: '志愿北京立项通过',
  text: `志愿北京平台${names.join('、')}立项已通过。`,
})
