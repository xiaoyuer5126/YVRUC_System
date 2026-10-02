/** 常用语：审批意见（见 ApprovalCommentDialog）与意见反馈的回复。
 *  点一下整段填进输入框；文字由校青协定，[活动标题]、[日期] 这类占位在这里填好。
 *  「不通过」里的【填写意见】和联系方式是留给审批人当场改的空。
 *  立项、结项这几条说的是立项书、结项材料和对外联络部，只给归口外联的活动用。 */
const SIGN = '中国人民大学青年志愿者协会'
const DEV_SIGN = '中国人民大学志愿服务管理平台开发者'
const THANKS = '感谢同学对志愿服务的支持。让有爱者有力，让奉献者发光，'
  + '期待与同学一起参与更多丰富多彩的志愿活动！'
const CONTACT_LIAISON = '请通过邮箱rucyv_pr@163.com或微信联系对外联络部工作人员（联系方式：）。'

function today() {
  const d = new Date()
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}

/** 活动立项「通过」 */
export const projectApproved = title => ({
  label: '立项通过',
  text: `同学你好，\n${title}志愿活动的立项书已经收到，同意开展活动。\n`
    + '让有爱者有力，让奉献者发光，中国人民大学青年志愿者协会始终与你同在，祝志愿活动开展顺利！\n'
    + `${SIGN}\n${today()}`,
})

/** 活动立项「驳回」 */
export const projectRejected = title => ({
  label: '立项不通过',
  text: `同学你好，\n${title}志愿活动的立项书已经收到。\n【填写意见】\n`
    + `无法立项，${CONTACT_LIAISON}\n`
    + '让有爱者有力，让奉献者发光，中国人民大学青年志愿者协会始终与你同在。\n'
    + `${SIGN}\n${today()}`,
})

/** 时长审核「通过」（锁定时长） */
export const closingApproved = title => ({
  label: '结项通过',
  text: `同学你好，\n${title}志愿活动的结项材料已经收到，同意结项。\n`
    + '让有爱者有力，让奉献者发光，期待更加丰富多彩的志愿活动！\n'
    + `${SIGN}\n${today()}`,
})

/** 时长审核「驳回」 */
export const closingRejected = title => ({
  label: '结项不通过',
  text: `同学你好，\n${title}志愿活动的结项材料已经收到。\n【填写意见】\n`
    + `无法结项，${CONTACT_LIAISON}\n`
    + '让有爱者有力，让奉献者发光，期待更加丰富多彩的志愿活动！\n'
    + `${SIGN}\n${today()}`,
})

/** 外联「确认志愿北京」：填的是志愿北京上的活动名称，不是本平台的活动标题 */
export const vbConfirmed = names => ({
  label: '志愿北京立项通过',
  text: `志愿北京平台${names.join('、')}立项已通过。`,
})

/** 意见反馈的回复（管理员在「意见反馈」页处理时用） */
export const feedbackReplies = () => [
  { label: '已收到·修复中',
    text: `同学你好！\n已收到同学对平台使用的反馈，正在着手修复。\n${THANKS}\n${DEV_SIGN}\n${today()}` },
  { label: '转校青协处理',
    text: '同学你好！\n已收到同学的反馈，您反馈的是志愿相关问题，已转接校青协处理，'
      + `将以邮件形式给您回复，可查看校内邮箱。\n${THANKS}\n${DEV_SIGN}\n${today()}` },
]
