<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import { listPlanSuggestions, getPlanSuggestion, decidePlanSuggestion } from '@/api/graphStore'
import type { PlanSuggestion } from '@/api/types'

const auth = useAuthStore()
const suggestions = ref<PlanSuggestion[]>([])
const selected = ref<PlanSuggestion | null>(null)
const loading = ref(true)

const columns = [
  { key: 'suggestion_id', label: 'Suggestion ID' },
  { key: 'plan_id', label: 'Plan ID' },
  { key: 'type', label: 'Type' },
  { key: 'status', label: 'Status' },
]

async function fetch() {
  loading.value = true
  try {
    suggestions.value = await listPlanSuggestions(auth.username)
  } finally {
    loading.value = false
  }
}

async function selectSuggestion(row: Record<string, unknown>) {
  selected.value = await getPlanSuggestion(auth.username, String(row.suggestion_id))
}

async function decide(decision: string) {
  if (!selected.value) return
  await decidePlanSuggestion(auth.username, selected.value.suggestion_id, { decision })
  selected.value = null
  await fetch()
}

onMounted(fetch)
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">Plan Suggestions</h1>

    <DataTable
      :columns="columns"
      :rows="(suggestions as any[])"
      :loading="loading"
      empty-title="No suggestions"
      empty-message="No plan suggestions pending."
      @row-click="selectSuggestion"
    >
      <template #cell-suggestion_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-status="{ value }">
        <StatusBadge v-if="value" :status="String(value)" />
      </template>
    </DataTable>

    <div v-if="selected" class="mt-6 rounded-lg border border-border bg-card p-4">
      <h2 class="font-semibold mb-3">
        Suggestion: {{ selected.suggestion_id }}
        <StatusBadge v-if="selected.status" :status="selected.status" class="ml-2" />
      </h2>
      <JsonViewer :data="selected" />
      <div class="mt-4 flex gap-3">
        <button
          class="rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700"
          @click="decide('approve')"
        >
          Approve
        </button>
        <button
          class="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90"
          @click="decide('reject')"
        >
          Reject
        </button>
      </div>
    </div>
  </div>
</template>
