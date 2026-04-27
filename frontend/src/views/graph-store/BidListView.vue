<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import DataTable from '@/components/DataTable.vue'
import { listBids, getBidStats } from '@/api/graphStore'
import JsonViewer from '@/components/JsonViewer.vue'
import type { Bid } from '@/api/types'

const auth = useAuthStore()
const bids = ref<Bid[]>([])
const bidStats = ref<unknown>(null)
const filterPlanId = ref('')
const filterTaskId = ref('')

const columns = [
  { key: 'task_id', label: 'Task ID' },
  { key: 'plan_id', label: 'Plan ID' },
  { key: 'agent_id', label: 'Agent' },
  { key: 'score', label: 'Score' },
]

async function fetch() {
  const params: Record<string, string> = {}
  if (filterPlanId.value) params.plan_id = filterPlanId.value
  if (filterTaskId.value) params.task_id = filterTaskId.value
  const [b, s] = await Promise.allSettled([
    listBids(auth.username, params),
    getBidStats(auth.username),
  ])
  if (b.status === 'fulfilled') bids.value = b.value
  if (s.status === 'fulfilled') bidStats.value = s.value
}

const { loading } = usePolling(fetch, 15000)
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">Bids</h1>

    <div class="flex gap-3 mb-4">
      <input v-model="filterPlanId" placeholder="Filter by Plan ID..." class="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" @change="fetch" />
      <input v-model="filterTaskId" placeholder="Filter by Task ID..." class="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" @change="fetch" />
    </div>

    <DataTable
      :columns="columns"
      :rows="(bids as any[])"
      :loading="loading && bids.length === 0"
      empty-title="No bids"
      empty-message="No bids found."
    >
      <template #cell-task_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-agent_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-score="{ value }">
        <span class="font-medium">{{ typeof value === 'number' ? value.toFixed(2) : value ?? '--' }}</span>
      </template>
    </DataTable>

    <div v-if="bidStats" class="mt-6 rounded-lg border border-border bg-card p-4">
      <h2 class="font-semibold mb-3">Bid Statistics</h2>
      <JsonViewer :data="bidStats" />
    </div>
  </div>
</template>
