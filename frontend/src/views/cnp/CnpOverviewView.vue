<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import StatusBadge from '@/components/StatusBadge.vue'
import DataTable from '@/components/DataTable.vue'
import { getCnpStats, listCnpCycles, triggerCnpCycle } from '@/api/cnp'
import type { CnpStats, CnpCycle } from '@/api/types'

const auth = useAuthStore()
const stats = ref<CnpStats>({})
const cycles = ref<CnpCycle[]>([])
const triggering = ref(false)

const cycleColumns = [
  { key: 'cycle_id', label: 'Cycle ID' },
  { key: 'status', label: 'Status' },
  { key: 'created_at', label: 'Created' },
]

async function fetch() {
  const [s, c] = await Promise.allSettled([
    getCnpStats(auth.username),
    listCnpCycles(auth.username, { limit: 20 }),
  ])
  if (s.status === 'fulfilled') stats.value = s.value
  if (c.status === 'fulfilled') cycles.value = c.value
}

const { loading } = usePolling(fetch, 10000)

async function trigger() {
  triggering.value = true
  try {
    await triggerCnpCycle(auth.username)
    await fetch()
  } finally {
    triggering.value = false
  }
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold">CNP Monitor</h1>
      <button
        :disabled="triggering"
        class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        @click="trigger"
      >
        {{ triggering ? 'Triggering...' : 'Trigger CNP Cycle' }}
      </button>
    </div>

    <!-- Stats Cards -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
      <div class="rounded-lg border border-border bg-card p-4 text-center">
        <p class="text-2xl font-bold">{{ stats.total_cfps ?? 0 }}</p>
        <p class="text-sm text-muted-foreground">Calls For Proposals</p>
      </div>
      <div class="rounded-lg border border-border bg-card p-4 text-center">
        <p class="text-2xl font-bold">{{ stats.total_bids ?? 0 }}</p>
        <p class="text-sm text-muted-foreground">Total Bids</p>
      </div>
      <div class="rounded-lg border border-border bg-card p-4 text-center">
        <p class="text-2xl font-bold">{{ stats.total_awards ?? 0 }}</p>
        <p class="text-sm text-muted-foreground">Contract Awards</p>
      </div>
    </div>

    <!-- Navigation -->
    <div class="flex gap-3 mb-6">
      <router-link
        to="/cnp/cfps"
        class="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80"
      >
        View All CFPs
      </router-link>
      <router-link
        to="/cnp/awards"
        class="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80"
      >
        View All Awards
      </router-link>
    </div>

    <!-- Recent Cycles -->
    <h2 class="text-lg font-semibold mb-3">Recent Cycles</h2>
    <DataTable
      :columns="cycleColumns"
      :rows="(cycles as any[])"
      :loading="loading && cycles.length === 0"
      empty-title="No cycles"
      empty-message="No CNP cycles have been triggered yet."
    >
      <template #cell-cycle_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="String(value || 'unknown')" />
      </template>
    </DataTable>
  </div>
</template>
