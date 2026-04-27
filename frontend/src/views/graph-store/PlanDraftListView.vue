<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { listPlanDrafts } from '@/api/graphStore'
import type { PlanDraft } from '@/api/types'

const router = useRouter()
const auth = useAuthStore()
const drafts = ref<PlanDraft[]>([])

const columns = [
  { key: 'plan_id', label: 'Plan ID' },
  { key: 'status', label: 'Status' },
  { key: 'created_at', label: 'Created' },
]

async function fetch() {
  drafts.value = await listPlanDrafts(auth.username)
}

const { loading } = usePolling(fetch, 15000)

function onRowClick(row: Record<string, unknown>) {
  router.push({ name: 'plan-draft-detail', params: { planId: String(row.plan_id) } })
}
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">Plan Drafts</h1>

    <DataTable
      :columns="columns"
      :rows="(drafts as any[])"
      :loading="loading && drafts.length === 0"
      empty-title="No plan drafts"
      empty-message="No plan drafts have been created yet."
      @row-click="onRowClick"
    >
      <template #cell-plan_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-status="{ value }">
        <StatusBadge v-if="value" :status="String(value)" />
        <span v-else class="text-muted-foreground">--</span>
      </template>
    </DataTable>
  </div>
</template>
