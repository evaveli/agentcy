<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import { usePagination } from '@/composables/usePagination'
import DataTable from '@/components/DataTable.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import { listTaskSpecs, getTaskSpec } from '@/api/graphStore'
import type { TaskSpec } from '@/api/types'

const auth = useAuthStore()
const specs = ref<TaskSpec[]>([])
const selected = ref<TaskSpec | null>(null)
const { offset, limit, page, hasNext, hasPrev, next, prev } = usePagination()

const columns = [
  { key: 'task_id', label: 'Task ID' },
  { key: 'description', label: 'Description' },
  { key: 'required_capabilities', label: 'Capabilities' },
]

async function fetch() {
  specs.value = await listTaskSpecs(auth.username, { offset: offset.value, limit })
}

const { loading } = usePolling(fetch, 15000)

async function onRowClick(row: Record<string, unknown>) {
  selected.value = await getTaskSpec(auth.username, String(row.task_id))
}
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">Task Specs</h1>

    <DataTable
      :columns="columns"
      :rows="(specs as any[])"
      :loading="loading && specs.length === 0"
      empty-title="No task specs"
      empty-message="No task specifications found."
      @row-click="onRowClick"
    >
      <template #cell-task_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-required_capabilities="{ value }">
        <div class="flex flex-wrap gap-1">
          <span v-for="cap in (Array.isArray(value) ? value : [])" :key="cap" class="rounded bg-secondary px-2 py-0.5 text-xs">{{ cap }}</span>
        </div>
      </template>
    </DataTable>

    <div class="flex items-center justify-between mt-4">
      <button :disabled="!hasPrev" class="rounded-md bg-secondary px-3 py-1.5 text-sm disabled:opacity-50" @click="prev(); fetch()">Previous</button>
      <span class="text-sm text-muted-foreground">Page {{ page }}</span>
      <button :disabled="!hasNext" class="rounded-md bg-secondary px-3 py-1.5 text-sm disabled:opacity-50" @click="next(); fetch()">Next</button>
    </div>

    <div v-if="selected" class="mt-6 rounded-lg border border-border bg-card p-4">
      <h2 class="font-semibold mb-3">Task Spec: {{ selected.task_id }}</h2>
      <JsonViewer :data="selected" />
    </div>
  </div>
</template>
