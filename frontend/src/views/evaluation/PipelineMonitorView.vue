<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import StatusBadge from '@/components/StatusBadge.vue'
import apiClient from '@/api/client'

interface TaskState {
  status: string
  service_name: string
  error?: string
  attempts?: number
}

interface PipelineRun {
  pipeline_run_id: string
  pipeline_id: string
  status: string
  started_at: string | null
  finished_at: string | null
  tasks: Record<string, TaskState>
}

interface PipelineInfo {
  pipeline_id: string
  name: string
  description: string
  pipeline_name: string
}

interface TaskOutput {
  raw_output: string
}

const pipelines = ref<PipelineInfo[]>([])
const runs = ref<Record<string, PipelineRun[]>>({})
const expandedRun = ref<string | null>(null)
const taskOutputs = ref<Record<string, string>>({})
const loadingOutput = ref<string | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const pollingId = ref<number | null>(null)

const cleaning = ref(false)
const cleanupResult = ref<{ deleted: number; kept: number } | null>(null)
const username = 'default'

async function cleanupStuck() {
  cleaning.value = true
  cleanupResult.value = null
  try {
    const { data } = await apiClient.post('/api/evaluation/pipeline/cleanup')
    cleanupResult.value = data
    await refresh()
  } catch (e: any) {
    error.value = `Cleanup failed: ${e?.message || e}`
  }
  cleaning.value = false
}

async function fetchPipelines() {
  try {
    const { data } = await apiClient.get(`/pipelines/${username}`)
    const allPipes: PipelineInfo[] = Array.isArray(data) ? data : []
    // Filter to ablation pipelines only
    pipelines.value = allPipes.filter(p =>
      (p.name || p.pipeline_name || '').startsWith('ablation-')
    ).reverse()
    error.value = null
  } catch (e: any) {
    error.value = e?.message || 'Failed to fetch pipelines'
  }
}

async function fetchRuns() {
  for (const pipe of pipelines.value) {
    try {
      const { data } = await apiClient.get(`/pipelines/${username}/${pipe.pipeline_id}/runs`)
      const runIds: string[] = data?.runs || []
      const runDetails: PipelineRun[] = []
      for (const rid of runIds.slice(-5)) { // last 5 runs per pipeline
        try {
          const { data: runData } = await apiClient.get(
            `/pipelines/${username}/${pipe.pipeline_id}/${rid}`
          )
          if (runData && !runData.detail) {
            runDetails.push(runData)
          }
        } catch { /* skip */ }
      }
      runs.value[pipe.pipeline_id] = runDetails
    } catch { /* skip */ }
  }
}

async function fetchTaskOutput(pipelineId: string, runId: string, taskId: string) {
  const key = `${runId}:${taskId}`
  if (taskOutputs.value[key]) return // already loaded
  loadingOutput.value = key
  try {
    const { data } = await apiClient.get(
      `/pipelines/${username}/${pipelineId}/${runId}/tasks/${taskId}/output`
    )
    taskOutputs.value[key] = data?.raw_output || JSON.stringify(data, null, 2)
  } catch (e: any) {
    taskOutputs.value[key] = `Error: ${e?.message || 'Failed to load output'}`
  }
  loadingOutput.value = null
}

async function refresh() {
  await fetchPipelines()
  await fetchRuns()
  loading.value = false
}

function toggleRun(runId: string) {
  expandedRun.value = expandedRun.value === runId ? null : runId
}

function statusColor(status: string): string {
  const s = status?.toUpperCase() || ''
  if (s === 'COMPLETED') return 'text-green-600 dark:text-green-400'
  if (s === 'RUNNING') return 'text-blue-600 dark:text-blue-400'
  if (s === 'FAILED') return 'text-red-600 dark:text-red-400'
  if (s === 'PENDING') return 'text-yellow-600 dark:text-yellow-400'
  return 'text-muted-foreground'
}

function statusBadge(status: string): string {
  const s = status?.toUpperCase() || ''
  if (s === 'COMPLETED') return 'active'
  if (s === 'RUNNING') return 'busy'
  if (s === 'FAILED') return 'offline'
  return 'idle'
}

function duration(start: string | null, end: string | null): string {
  if (!start) return '--'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  const sec = Math.round((e - s) / 1000)
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

function taskOrder(tasks: Record<string, TaskState>): [string, TaskState][] {
  const order = [
    'call-transcription', 'deal-summary', 'warehouse-match',
    'deal-estimation', 'client-necessity', 'compliance-check', 'proposal-generation'
  ]
  const entries = Object.entries(tasks)
  return entries.sort((a, b) => {
    const ai = order.indexOf(a[0])
    const bi = order.indexOf(b[0])
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  })
}

const activePipelines = computed(() =>
  pipelines.value.filter(p => {
    const pRuns = runs.value[p.pipeline_id] || []
    return pRuns.some(r => r.status?.toUpperCase() === 'RUNNING')
  })
)

const completedPipelines = computed(() =>
  pipelines.value.filter(p => {
    const pRuns = runs.value[p.pipeline_id] || []
    return pRuns.length > 0 && pRuns.every(r => r.status?.toUpperCase() !== 'RUNNING')
  })
)

onMounted(() => {
  refresh()
  pollingId.value = window.setInterval(refresh, 5000)
})

onUnmounted(() => {
  if (pollingId.value) clearInterval(pollingId.value)
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold">Pipeline Monitor</h1>
        <p class="text-sm text-muted-foreground">Real-time C0 pipeline execution tracking (auto-refreshes every 5s)</p>
      </div>
      <div class="flex items-center gap-3">
        <span v-if="activePipelines.length" class="flex items-center gap-2 text-sm text-blue-600">
          <span class="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
          {{ activePipelines.length }} running
        </span>
        <button
          @click="cleanupStuck"
          :disabled="cleaning"
          class="px-3 py-1.5 text-sm rounded-md border border-destructive/50 text-destructive hover:bg-destructive/10 disabled:opacity-50"
        >
          {{ cleaning ? 'Cleaning...' : 'Clean Up Stuck' }}
        </button>
        <router-link to="/evaluation" class="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted">
          Back to Evaluation
        </router-link>
      </div>
    </div>

    <!-- Cleanup result -->
    <div v-if="cleanupResult" class="rounded-lg border border-border bg-muted/30 p-3 mb-4 flex items-center justify-between">
      <span class="text-sm">
        Cleaned up <strong>{{ cleanupResult.deleted }}</strong> stuck pipeline(s),
        kept <strong>{{ cleanupResult.kept }}</strong>.
      </span>
      <button @click="cleanupResult = null" class="text-xs text-muted-foreground hover:text-foreground">dismiss</button>
    </div>

    <div v-if="loading" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading pipelines...
    </div>

    <div v-if="!loading && pipelines.length === 0" class="text-center py-12 text-muted-foreground">
      No ablation pipelines found. Launch one from the
      <router-link to="/evaluation" class="text-primary underline">Evaluation Dashboard</router-link>.
    </div>

    <!-- Pipeline Cards -->
    <div class="space-y-4">
      <div
        v-for="pipe in pipelines"
        :key="pipe.pipeline_id"
        class="rounded-lg border border-border bg-card"
      >
        <!-- Pipeline Header -->
        <div class="p-4 border-b border-border">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3">
              <h2 class="font-semibold">{{ pipe.name || pipe.pipeline_name }}</h2>
              <span class="text-xs text-muted-foreground font-mono">{{ pipe.pipeline_id.slice(0, 8) }}</span>
            </div>
            <span class="text-xs text-muted-foreground">
              {{ (runs[pipe.pipeline_id] || []).length }} run(s)
            </span>
          </div>
          <p v-if="pipe.description" class="text-xs text-muted-foreground mt-1">{{ pipe.description }}</p>
        </div>

        <!-- Runs -->
        <div v-for="run in (runs[pipe.pipeline_id] || [])" :key="run.pipeline_run_id" class="border-b border-border last:border-0">
          <!-- Run Header -->
          <div
            class="p-3 flex items-center justify-between cursor-pointer hover:bg-muted/30"
            @click="toggleRun(run.pipeline_run_id)"
          >
            <div class="flex items-center gap-3">
              <StatusBadge :status="statusBadge(run.status)" />
              <span class="text-sm font-mono">{{ run.pipeline_run_id?.slice(0, 12) }}</span>
              <span :class="statusColor(run.status)" class="text-xs font-medium">{{ run.status }}</span>
            </div>
            <div class="flex items-center gap-4 text-xs text-muted-foreground">
              <span>{{ duration(run.started_at, run.finished_at) }}</span>
              <span>{{ Object.keys(run.tasks || {}).length }} tasks</span>
              <span class="text-lg">{{ expandedRun === run.pipeline_run_id ? '▾' : '▸' }}</span>
            </div>
          </div>

          <!-- Task Details (expanded) -->
          <div v-if="expandedRun === run.pipeline_run_id" class="px-4 pb-4">
            <!-- Task Progress Bar -->
            <div class="flex gap-1 mb-4">
              <div
                v-for="[tid, ts] in taskOrder(run.tasks || {})"
                :key="tid"
                class="flex-1 h-2 rounded-full"
                :class="{
                  'bg-green-500': ts.status?.toUpperCase() === 'COMPLETED',
                  'bg-blue-500 animate-pulse': ts.status?.toUpperCase() === 'RUNNING',
                  'bg-red-500': ts.status?.toUpperCase() === 'FAILED',
                  'bg-muted': ts.status?.toUpperCase() === 'PENDING',
                }"
                :title="`${tid}: ${ts.status}`"
              />
            </div>

            <!-- Task Table -->
            <table class="w-full text-sm">
              <thead>
                <tr class="text-xs text-muted-foreground border-b border-border">
                  <th class="text-left py-2 pr-4">Task</th>
                  <th class="text-left py-2 pr-4">Service</th>
                  <th class="text-left py-2 pr-4">Status</th>
                  <th class="text-right py-2">Output</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="[tid, ts] in taskOrder(run.tasks || {})"
                  :key="tid"
                  class="border-b border-border/50 last:border-0"
                >
                  <td class="py-2 pr-4 font-mono text-xs">{{ tid }}</td>
                  <td class="py-2 pr-4 text-xs text-muted-foreground">{{ ts.service_name }}</td>
                  <td class="py-2 pr-4">
                    <span
                      :class="statusColor(ts.status)"
                      class="text-xs font-medium"
                    >{{ ts.status }}</span>
                    <span v-if="ts.error" class="text-xs text-destructive ml-2">{{ ts.error }}</span>
                  </td>
                  <td class="py-2 text-right">
                    <button
                      v-if="ts.status?.toUpperCase() === 'COMPLETED'"
                      @click.stop="fetchTaskOutput(pipe.pipeline_id, run.pipeline_run_id, tid)"
                      class="text-xs text-primary hover:underline"
                    >
                      {{ taskOutputs[`${run.pipeline_run_id}:${tid}`] ? 'Hide' : 'View' }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>

            <!-- Task Output Display -->
            <div
              v-for="[tid, ts] in taskOrder(run.tasks || {})"
              :key="`out-${tid}`"
            >
              <div
                v-if="taskOutputs[`${run.pipeline_run_id}:${tid}`]"
                class="mt-3 rounded-lg bg-muted/30 p-4"
              >
                <div class="flex items-center justify-between mb-2">
                  <span class="text-xs font-semibold">{{ tid }} output</span>
                  <button
                    @click="delete taskOutputs[`${run.pipeline_run_id}:${tid}`]"
                    class="text-xs text-muted-foreground hover:text-foreground"
                  >close</button>
                </div>
                <pre class="text-xs whitespace-pre-wrap max-h-96 overflow-y-auto font-mono">{{ taskOutputs[`${run.pipeline_run_id}:${tid}`] }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Error -->
    <div v-if="error" class="rounded-lg border border-destructive/50 bg-destructive/10 p-4 mt-4">
      <p class="text-sm text-destructive">{{ error }}</p>
    </div>
  </div>
</template>
