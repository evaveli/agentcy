<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { listPipelines } from '@/api/pipelines'
import type { PipelineConfig } from '@/api/types'

const router = useRouter()
const auth = useAuthStore()
const pipelines = ref<PipelineConfig[]>([])

const columns = [
  { key: 'pipeline_id', label: 'Pipeline ID' },
  { key: 'name', label: 'Name' },
  { key: 'description', label: 'Description' },
  { key: 'created_at', label: 'Created' },
]

async function fetch() {
  pipelines.value = await listPipelines(auth.username)
}

const { loading } = usePolling(fetch, 15000)

function onRowClick(row: Record<string, unknown>) {
  router.push({ name: 'pipeline-detail', params: { pipelineId: String(row.pipeline_id) } })
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold">Pipelines</h1>
      <router-link
        to="/pipelines/create"
        class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
      >
        Create Pipeline
      </router-link>
    </div>

    <DataTable
      :columns="columns"
      :rows="(pipelines as any[])"
      :loading="loading && pipelines.length === 0"
      empty-title="No pipelines"
      empty-message="Create your first pipeline to get started."
      @row-click="onRowClick"
    >
      <template #cell-pipeline_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
    </DataTable>
  </div>
</template>
