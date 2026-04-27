<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps<{ status: string }>()

const colorClass = computed(() => {
  const s = props.status.toLowerCase()
  if (['ok', 'healthy', 'active', 'success', 'completed', 'approved', 'online'].includes(s))
    return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300'
  if (['warning', 'pending', 'idle', 'waiting', 'in_progress'].includes(s))
    return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300'
  if (['error', 'failed', 'rejected', 'offline', 'unhealthy'].includes(s))
    return 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300'
  return 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300'
})
</script>

<template>
  <span :class="cn('inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium', colorClass)">
    {{ status }}
  </span>
</template>
