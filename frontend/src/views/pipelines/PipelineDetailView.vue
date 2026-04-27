<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import StatusBadge from '@/components/StatusBadge.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { getPipeline, listRuns, getRun, deletePipeline } from '@/api/pipelines'
import type { PipelineConfig, PipelineRun } from '@/api/types'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const pipelineId = route.params.pipelineId as string

const pipeline = ref<PipelineConfig | null>(null)
const runIds = ref<string[]>([])
const selectedRun = ref<PipelineRun | null>(null)
const loading = ref(true)
const showDelete = ref(false)

async function fetch() {
  loading.value = true
  try {
    const [p, r] = await Promise.all([
      getPipeline(auth.username, pipelineId),
      listRuns(auth.username, pipelineId),
    ])
    pipeline.value = p
    runIds.value = r
  } finally {
    loading.value = false
  }
}

async function loadRun(runId: string) {
  selectedRun.value = await getRun(auth.username, pipelineId, runId)
}

async function confirmDelete() {
  await deletePipeline(auth.username, pipelineId)
  router.push({ name: 'pipelines' })
}

onMounted(fetch)
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <router-link to="/pipelines" class="text-sm text-muted-foreground hover:underline">
          &larr; Pipelines
        </router-link>
        <h1 class="text-2xl font-bold mt-1">{{ pipeline?.name || pipelineId }}</h1>
      </div>
      <button
        class="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90"
        @click="showDelete = true"
      >
        Delete
      </button>
    </div>

    <div v-if="loading" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading...
    </div>

    <template v-else-if="pipeline">
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Config -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Configuration</h2>
          <JsonViewer :data="pipeline" />
        </div>

        <!-- Runs -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Runs ({{ runIds.length }})</h2>
          <div v-if="runIds.length === 0" class="text-sm text-muted-foreground">No runs yet.</div>
          <div v-else class="space-y-2 max-h-96 overflow-y-auto">
            <button
              v-for="rid in runIds"
              :key="rid"
              class="w-full rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-accent transition-colors"
              :class="selectedRun?.run_id === rid ? 'bg-accent' : ''"
              @click="loadRun(rid)"
            >
              <span class="font-mono text-xs">{{ rid }}</span>
            </button>
          </div>
        </div>
      </div>

      <!-- Run Detail -->
      <div v-if="selectedRun" class="mt-6 rounded-lg border border-border bg-card p-4">
        <h2 class="font-semibold mb-3">
          Run: <span class="font-mono text-sm">{{ selectedRun.run_id }}</span>
          <StatusBadge v-if="selectedRun.status" :status="selectedRun.status" class="ml-2" />
        </h2>
        <JsonViewer :data="selectedRun" />
      </div>
    </template>

    <ConfirmDialog
      :open="showDelete"
      title="Delete Pipeline"
      message="This will permanently delete this pipeline and all its data. This cannot be undone."
      confirm-label="Delete"
      :destructive="true"
      @confirm="confirmDelete"
      @cancel="showDelete = false"
    />
  </div>
</template>
