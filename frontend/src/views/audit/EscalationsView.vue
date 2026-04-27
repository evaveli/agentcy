<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import { listEscalations } from '@/api/graphStore'
import type { Escalation } from '@/api/types'

const auth = useAuthStore()
const escalations = ref<Escalation[]>([])
const loading = ref(true)
const selected = ref<Escalation | null>(null)

const columns = [
  { key: 'escalation_id', label: 'ID' },
  { key: 'reason', label: 'Reason' },
  { key: 'status', label: 'Status' },
]

async function fetch() {
  loading.value = true
  try {
    escalations.value = await listEscalations(auth.username)
  } finally {
    loading.value = false
  }
}

function onRowClick(row: Record<string, unknown>) {
  selected.value = row as unknown as Escalation
}

onMounted(fetch)
</script>

<template>
  <div>
    <router-link to="/audit" class="text-sm text-muted-foreground hover:underline">&larr; Audit Logs</router-link>
    <h1 class="text-2xl font-bold mt-1 mb-6">Escalations</h1>

    <DataTable
      :columns="columns"
      :rows="(escalations as any[])"
      :loading="loading"
      empty-title="No escalations"
      empty-message="No escalation notices found."
      @row-click="onRowClick"
    >
      <template #cell-status="{ value }">
        <StatusBadge v-if="value" :status="String(value)" />
        <span v-else class="text-muted-foreground">--</span>
      </template>
    </DataTable>

    <div v-if="selected" class="mt-6 rounded-lg border border-border bg-card p-4">
      <h2 class="font-semibold mb-3">Escalation Details</h2>
      <JsonViewer :data="selected" />
    </div>
  </div>
</template>
