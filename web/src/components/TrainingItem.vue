<!-- 志愿者活动页上的一条培训 / 彩排。挂在哪里就说明谁要去：全体参加的挂在
     场次下面，只叫某几个岗位的挂在那几个岗位下面，所以这里不再写「谁参加」。

     full：电脑上的场次表格，多写名称与是否计时长；手机卡片只写时间地点。
     excuse：本人点「我无法参加 / 销假」，由页面去请假或销假。 -->
<template>
  <div class="att-item" :class="{ mine: s.mine }">
    <el-tag size="small" effect="dark" :type="s.kind === 'training' ? 'success' : 'warning'">
      {{ s.kind_label }}
    </el-tag>
    <b v-if="full">{{ s.name }}</b>
    <span>{{ s.time_text }}</span>
    <span class="dim">{{ s.location || location || '地点待定' }}</span>
    <span v-if="full" class="dim">{{ s.hours_mode === 'none' ? '不计时长' : '计入志愿时长' }}</span>
    <span v-if="s.note" class="dim">备注：{{ s.note }}</span>
    <template v-if="s.mine">
      <el-tag size="small" :type="s.excused ? 'info' : 'primary'" effect="plain">
        {{ s.excused ? '已请假' : (full ? '您需要参加' : '要参加') }}
      </el-tag>
      <el-button size="small" link :type="s.excused ? 'primary' : 'warning'"
                 @click="emit('excuse', s)">
        {{ s.excused ? '销假' : '我无法参加' }}
      </el-button>
    </template>
  </div>
</template>

<script setup>
defineProps({
  s: { type: Object, required: true },
  location: { type: String, default: '' },   // 培训没填地点时用活动地点
  full: { type: Boolean, default: false },
})
const emit = defineEmits(['excuse'])
</script>
