<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ data: unknown }>()

const formatted = computed(() => {
  try {
    return JSON.stringify(props.data, null, 2)
  } catch {
    return String(props.data)
  }
})
</script>

<template>
  <pre class="overflow-auto rounded-lg bg-muted p-4 text-xs leading-relaxed font-mono max-h-96">{{ formatted }}</pre>
</template>
