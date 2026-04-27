<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import { usePagination } from '@/composables/usePagination'
import DataTable from '@/components/DataTable.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import { listAuditLogs, listHumanApprovals, listExecutionReports } from '@/api/graphStore'
import type { AuditLog, HumanApproval, ExecutionReport } from '@/api/types'

const auth = useAuthStore()
const logs = ref<AuditLog[]>([])
const approvals = ref<HumanApproval[]>([])
const reports = ref<ExecutionReport[]>([])
const selected = ref<unknown>(null)
const { offset, limit, page, hasNext, hasPrev, next, prev } = usePagination()

const logColumns = [
  { key: 'action', label: 'Action' },
  { key: 'actor', label: 'Actor' },
  { key: 'timestamp', label: 'Timestamp' },
]

async function fetch() {
  const [l, a, r] = await Promise.allSettled([
    listAuditLogs(auth.username, { offset: offset.value, limit }),
    listHumanApprovals(auth.username),
    listExecutionReports(auth.username),
  ])
  if (l.status === 'fulfilled') logs.value = l.value
  if (a.status === 'fulfilled') approvals.value = a.value
  if (r.status === 'fulfilled') reports.value = r.value
}

const { loading } = usePolling(fetch, 15000)

function onRowClick(row: Record<string, unknown>) {
  selected.value = row
}
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">Audit Logs</h1>

    <DataTable
      :columns="logColumns"
      :rows="(logs as any[])"
      :loading="loading && logs.length === 0"
      empty-title="No audit logs"
      empty-message="No audit log entries found."
      @row-click="onRowClick"
    />

    <div class="flex items-center justify-between mt-4">
      <button :disabled="!hasPrev" class="rounded-md bg-secondary px-3 py-1.5 text-sm disabled:opacity-50" @click="prev(); fetch()">Previous</button>
      <span class="text-sm text-muted-foreground">Page {{ page }}</span>
      <button :disabled="!hasNext" class="rounded-md bg-secondary px-3 py-1.5 text-sm disabled:opacity-50" @click="next(); fetch()">Next</button>
    </div>

    <div v-if="selected" class="mt-6 rounded-lg border border-border bg-card p-4">
      <h2 class="font-semibold mb-3">Log Details</h2>
      <JsonViewer :data="selected" />
    </div>

    <!-- Human Approvals -->
    <h2 class="text-lg font-semibold mt-8 mb-3">Human Approvals ({{ approvals.length }})</h2>
    <div v-if="approvals.length === 0" class="text-sm text-muted-foreground">No approvals.</div>
    <div v-else class="space-y-2">
      <div v-for="(a, i) in approvals" :key="i" class="rounded-md border border-border p-3">
        <JsonViewer :data="a" />
      </div>
    </div>

    <!-- Execution Reports -->
    <h2 class="text-lg font-semibold mt-8 mb-3">Execution Reports ({{ reports.length }})</h2>
    <div v-if="reports.length === 0" class="text-sm text-muted-foreground">No reports.</div>
    <div v-else class="space-y-2">
      <div v-for="(r, i) in reports" :key="i" class="rounded-md border border-border p-3">
        <JsonViewer :data="r" />
      </div>
    </div>
  </div>
</template>
