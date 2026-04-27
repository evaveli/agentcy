<script setup lang="ts">
defineProps<{
  open: boolean
  title?: string
  message?: string
  confirmLabel?: string
  destructive?: boolean
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="fixed inset-0 bg-black/50" @click="emit('cancel')" />
      <div class="relative z-10 w-full max-w-md rounded-lg bg-card p-6 shadow-lg border border-border">
        <h3 class="text-lg font-semibold">{{ title || 'Confirm' }}</h3>
        <p class="mt-2 text-sm text-muted-foreground">
          {{ message || 'Are you sure?' }}
        </p>
        <div class="mt-4 flex justify-end gap-3">
          <button
            class="rounded-md px-4 py-2 text-sm font-medium bg-secondary text-secondary-foreground hover:bg-secondary/80"
            @click="emit('cancel')"
          >
            Cancel
          </button>
          <button
            class="rounded-md px-4 py-2 text-sm font-medium text-white"
            :class="destructive ? 'bg-destructive hover:bg-destructive/90' : 'bg-primary hover:bg-primary/90'"
            @click="emit('confirm')"
          >
            {{ confirmLabel || 'Confirm' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
