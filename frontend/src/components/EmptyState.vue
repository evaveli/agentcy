<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'

defineProps<{
  title?: string
  message?: string
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center py-12 text-center">
    <Inbox class="h-12 w-12 text-muted-foreground/50 mb-4" />
    <h3 class="text-lg font-medium text-foreground">
      {{ title || 'No data' }}
    </h3>
    <p class="mt-1 text-sm text-muted-foreground">
      {{ message || 'Nothing to display yet.' }}
    </p>
    <slot />
  </div>
</template>
