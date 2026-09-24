<!-- 一个人的服装登记按钮：按他当前的状态给该有的那几个。

     现场没网、码贴反了、临时换人，都得有人工兜底。但只给说得通的动作——
     没领过的人不该有「撤销领取」，还了的人不该再出现「登记归还」。
     桌面表格和手机卡片共用这一份，省得两边的规则慢慢长歪。 -->
<template>
  <div class="ops">
    <el-button v-if="!row.taken && !row.returned" size="small" type="primary" plain
               @click="$emit('do', 'take', row)">登记领取</el-button>
    <template v-if="row.taken">
      <el-button size="small" type="warning" plain
                 @click="$emit('do', 'return', row)">登记归还</el-button>
      <el-button size="small" type="danger" plain
                 @click="$emit('do', 'cancel_take', row)">撤销领取</el-button>
    </template>
    <el-button v-if="row.returned" size="small" plain
               @click="$emit('do', 'cancel_return', row)">撤销归还</el-button>
  </div>
</template>

<script setup>
defineProps({ row: { type: Object, required: true } })
defineEmits(['do'])
</script>

<style scoped>
.ops { display:flex; gap:8px; flex-wrap:wrap; }
</style>
