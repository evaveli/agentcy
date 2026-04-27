<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import { usePagination } from '@/composables/usePagination'
import DataTable from '@/components/DataTable.vue'
import { listCfps } from '@/api/cnp'
import type { CfpEntry } from '@/api/types'

const auth = useAuthStore()
const cfps = ref<CfpEntry[]>([])
const { offset, limit, page, hasNext, hasPrev, next, prev } = usePagination()

const columns = [
  { key: 'task_id', label: 'Task ID' },
  { key: 'plan_id', label: 'Plan ID' },
  { key: 'required_capabilities', label: 'Required Capabilities' },
]

async function fetch() {
  cfps.value = await listCfps(auth.username, { offset: offset.value, limit })
}

const { loading } = usePolling(fetch, 15000)
</script>

<template>
  <div>
    <router-link to="/cnp" class="text-sm text-muted-foreground hover:underline">&larr; CNP Monitor</router-link>
    <h1 class="text-2xl font-bold mt-1 mb-6">Calls For Proposals</h1>

    <DataTable
      :columns="columns"
      :rows="(cfps as any[])"
      :loading="loading && cfps.length === 0"
      empty-title="No CFPs"
      empty-message="No calls for proposals found."
    >
      <template #cell-task_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-plan_id="{ value }">
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
  </div>
</template>
