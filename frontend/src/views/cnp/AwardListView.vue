<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import { usePagination } from '@/composables/usePagination'
import DataTable from '@/components/DataTable.vue'
import { listAwards } from '@/api/cnp'
import type { ContractAward } from '@/api/types'

const auth = useAuthStore()
const awards = ref<ContractAward[]>([])
const { offset, limit, page, hasNext, hasPrev, next, prev } = usePagination()

const columns = [
  { key: 'task_id', label: 'Task ID' },
  { key: 'plan_id', label: 'Plan ID' },
  { key: 'agent_id', label: 'Awarded Agent' },
]

async function fetch() {
  awards.value = await listAwards(auth.username, { offset: offset.value, limit })
}

const { loading } = usePolling(fetch, 15000)
</script>

<template>
  <div>
    <router-link to="/cnp" class="text-sm text-muted-foreground hover:underline">&larr; CNP Monitor</router-link>
    <h1 class="text-2xl font-bold mt-1 mb-6">Contract Awards</h1>

    <DataTable
      :columns="columns"
      :rows="(awards as any[])"
      :loading="loading && awards.length === 0"
      empty-title="No awards"
      empty-message="No contract awards found."
    >
      <template #cell-task_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-plan_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-agent_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
    </DataTable>

    <div class="flex items-center justify-between mt-4">
      <button :disabled="!hasPrev" class="rounded-md bg-secondary px-3 py-1.5 text-sm disabled:opacity-50" @click="prev(); fetch()">Previous</button>
      <span class="text-sm text-muted-foreground">Page {{ page }}</span>
      <button :disabled="!hasNext" class="rounded-md bg-secondary px-3 py-1.5 text-sm disabled:opacity-50" @click="next(); fetch()">Next</button>
    </div>
  </div>
</template>
