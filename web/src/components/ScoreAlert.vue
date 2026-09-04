<!-- 提示分低的人，名单上姓名前挂个感叹号。只是提醒排班的人点开档案看一眼，
     不代表这个人报不了名——平台不拦任何人。 -->
<template>
  <el-tooltip v-if="user?.score_alert" placement="top"
              :content="`内部提示分 ${user.score}，点姓名看档案里的明细`">
    <el-icon color="var(--el-color-warning)" style="vertical-align:-2px">
      <WarningFilled />
    </el-icon>
  </el-tooltip>
</template>

<script setup>
import { WarningFilled } from '@element-plus/icons-vue'

defineProps({ user: { type: Object, default: null } })
</script>
