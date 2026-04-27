<script setup lang="ts">
import EmptyState from './EmptyState.vue'

export interface Column {
  key: string
  label: string
  sortable?: boolean
}

defineProps<{
  columns: Column[]
  rows: Record<string, unknown>[]
  loading?: boolean
  emptyTitle?: string
  emptyMessage?: string
}>()

defineEmits<{
  'row-click': [row: Record<string, unknown>]
}>()
</script>

<template>
  <div class="overflow-hidden rounded-lg border border-border">
    <div v-if="loading" class="flex items-center justify-center py-12">
      <div class="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
    </div>
    <template v-else-if="rows.length === 0">
      <EmptyState :title="emptyTitle" :message="emptyMessage" />
    </template>
    <table v-else class="w-full text-sm">
      <thead>
        <tr class="border-b border-border bg-muted/50">
          <th
            v-for="col in columns"
            :key="col.key"
            class="px-4 py-3 text-left font-medium text-muted-foreground"
          >
            {{ col.label }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, i) in rows"
          :key="i"
          class="border-b border-border last:border-0 hover:bg-muted/30 cursor-pointer transition-colors"
          @click="$emit('row-click', row)"
        >
          <td v-for="col in columns" :key="col.key" class="px-4 py-3">
            <slot :name="`cell-${col.key}`" :row="row" :value="row[col.key]">
              {{ row[col.key] ?? '-' }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
