<script setup lang="ts">
import { ref, onMounted } from 'vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  getE4Dataset, getE1GroundTruth, getE3Configs, getE3GroundTruth,
  getPipelineClients, launchPipeline, launchAllPipelines,
} from '@/api/evaluation'
import type {
  E4DatasetSummary, E1GroundTruth, E3GroundTruthResponse,
  PipelineClientInfo, PipelineLaunchResult,
} from '@/api/evaluation'

const e4Summary = ref<E4DatasetSummary | null>(null)
const e1Data = ref<E1GroundTruth | null>(null)
const e3Configs = ref<Record<string, { description: string }>>({})
const e3GT = ref<E3GroundTruthResponse | null>(null)
const pipelineClients = ref<PipelineClientInfo[]>([])
const launchResults = ref<PipelineLaunchResult[]>([])
const launching = ref<string | null>(null)
const loading = ref(true)
const errors = ref<string[]>([])

async function fetchAll() {
  loading.value = true
  errors.value = []
  const results = await Promise.allSettled([
    getE4Dataset().then(d => { e4Summary.value = d.summary }),
    getE1GroundTruth().then(d => { e1Data.value = d }),
    getE3Configs().then(d => { e3Configs.value = d }),
    getE3GroundTruth().then(d => { e3GT.value = d }),
    getPipelineClients().then(d => { pipelineClients.value = d.clients }),
  ])
  results.forEach(r => {
    if (r.status === 'rejected') errors.value.push(r.reason?.message || 'Unknown error')
  })
  loading.value = false
}

async function handleLaunch(clientKey: string) {
  launching.value = clientKey
  try {
    const result = await launchPipeline(clientKey)
    launchResults.value = [result, ...launchResults.value]
  } catch (e: any) {
    launchResults.value = [
      { client: clientKey, status: 'failed', error: e?.message || String(e) },
      ...launchResults.value,
    ]
  }
  launching.value = null
}

async function handleLaunchAll() {
  launching.value = 'all'
  try {
    const { results } = await launchAllPipelines()
    launchResults.value = [...results, ...launchResults.value]
  } catch (e: any) {
    launchResults.value = [
      { client: 'all', status: 'failed', error: e?.message || String(e) },
      ...launchResults.value,
    ]
  }
  launching.value = null
}

onMounted(fetchAll)
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-2">Evaluation Dashboard</h1>
    <p class="text-muted-foreground mb-6">Thesis experiments: ablation study (E3), ethics detection (E4), agent quality (E1)</p>

    <div v-if="loading" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading...
    </div>

    <!-- Runnable Now Banner -->
    <div class="rounded-lg border border-green-200 bg-green-50 dark:bg-green-900/20 dark:border-green-800 p-4 mb-6">
      <h2 class="font-semibold text-green-800 dark:text-green-300 mb-2">Runnable Without Docker</h2>
      <div class="flex gap-4 text-sm">
        <div class="flex items-center gap-2">
          <span class="h-2 w-2 rounded-full bg-green-500" />
          <span>E4 Ethics Detection (stub mode) — 50 test cases, confusion matrix</span>
        </div>
        <div class="flex items-center gap-2">
          <span class="h-2 w-2 rounded-full bg-green-500" />
          <span>Compliance Agent Test — 40 client-warehouse pairs, 8 seeded scenarios</span>
        </div>
      </div>
    </div>

    <!-- Experiment Cards -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
      <!-- E3 Ablation -->
      <router-link to="/evaluation/e3" class="block">
        <div class="rounded-lg border border-border bg-card p-5 hover:border-primary/50 transition-colors h-full">
          <div class="flex items-center justify-between mb-3">
            <h2 class="font-semibold">E3 — Ablation Study</h2>
            <StatusBadge status="active" />
          </div>
          <p class="text-sm text-muted-foreground mb-3">
            9 configurations (C0-C7 + C3+C4) testing 5 hypotheses about coordination mechanisms.
          </p>
          <div v-if="e3GT" class="space-y-2 text-sm">
            <div class="flex justify-between">
              <span class="text-muted-foreground">Clients:</span>
              <span class="font-medium">{{ e3GT.assignments.length }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted-foreground">Competition points:</span>
              <span class="font-medium">2 (warehouse + estimator)</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted-foreground">Seeded violations:</span>
              <span class="font-medium">{{ e3GT.seeded_scenarios.length }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted-foreground">Total pipeline runs:</span>
              <span class="font-medium">790</span>
            </div>
          </div>
          <div class="mt-3 pt-3 border-t border-border flex gap-1 flex-wrap">
            <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-green-100 text-green-800">
              compliance: local
            </span>
            <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-yellow-100 text-yellow-800">
              full ablation: docker
            </span>
          </div>
        </div>
      </router-link>

      <!-- E4 Ethics -->
      <router-link to="/evaluation/e4" class="block">
        <div class="rounded-lg border border-border bg-card p-5 hover:border-primary/50 transition-colors h-full">
          <div class="flex items-center justify-between mb-3">
            <h2 class="font-semibold">E4 — Ethics Detection</h2>
            <StatusBadge status="active" />
          </div>
          <p class="text-sm text-muted-foreground mb-3">
            Rule-based vs LLM detection on synthetic violation dataset.
          </p>
          <div v-if="e4Summary" class="space-y-2 text-sm">
            <div class="flex justify-between">
              <span class="text-muted-foreground">Total cases:</span>
              <span class="font-medium">{{ e4Summary.total }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted-foreground">Violations:</span>
              <span class="font-medium">{{ e4Summary.violation_cases }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted-foreground">Clean:</span>
              <span class="font-medium">{{ e4Summary.clean_cases }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted-foreground">Categories:</span>
              <span class="font-medium">{{ Object.keys(e4Summary.by_category).length }}</span>
            </div>
          </div>
          <div class="mt-3 pt-3 border-t border-border">
            <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-green-100 text-green-800">
              stub mode: local
            </span>
            <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-yellow-100 text-yellow-800 ml-1">
              LLM mode: API key
            </span>
          </div>
        </div>
      </router-link>

      <!-- E1 Quality -->
      <router-link to="/evaluation/e1" class="block">
        <div class="rounded-lg border border-border bg-card p-5 hover:border-primary/50 transition-colors h-full">
          <div class="flex items-center justify-between mb-3">
            <h2 class="font-semibold">E1 — Agent Quality</h2>
            <StatusBadge status="active" />
          </div>
          <p class="text-sm text-muted-foreground mb-3">
            Output quality scoring for 5 agents with ground truth comparison.
          </p>
          <div v-if="e1Data" class="space-y-2 text-sm">
            <div v-for="(agent, name) in e1Data.agents" :key="name" class="flex justify-between">
              <span class="text-muted-foreground">{{ name }}:</span>
              <span class="font-medium">{{ agent.count }} cases</span>
            </div>
          </div>
          <div class="mt-3 pt-3 border-t border-border">
            <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-yellow-100 text-yellow-800">
              requires docker + API key
            </span>
          </div>
        </div>
      </router-link>
    </div>

    <!-- Agent Pipeline DAG -->
    <div class="rounded-lg border border-border bg-card p-4 mb-6">
      <h2 class="font-semibold mb-3">Redesigned Pipeline DAG (10 agents, 2 competition points)</h2>
      <pre class="text-xs font-mono text-muted-foreground bg-muted/30 p-4 rounded overflow-x-auto">
                                             ┌── Warehouse North  ──┐
Call Transcription → Deal Summary ──────────→├── Warehouse Central──┤──→ Compliance ──┐
                         │                   └── Warehouse South  ──┘                 │
                         │                                                            │
                         ├──→ Client Necessity ──────────────────────────────────────→├──→ Proposal
                         │                                                            │
                         │                   ┌── Cost Estimator ───┐                  │
                         └──────────────────→│                     │─────────────────→┘
                                             └── Speed Estimator ──┘
      </pre>
      <div class="flex gap-4 mt-3 text-xs text-muted-foreground">
        <span>CNP competition: <strong>warehouse_matching</strong> (3 agents) + <strong>deal_estimation</strong> (2 agents)</span>
      </div>
    </div>

    <!-- Pipeline Launch (C0) -->
    <div class="rounded-lg border border-border bg-card p-4 mb-6">
      <div class="flex items-center justify-between mb-3">
        <div>
          <h2 class="font-semibold">Launch C0 Pipeline Runs</h2>
          <p class="text-xs text-muted-foreground mt-1">Register and launch pipelines through the full framework (CNP bidding, pheromones, compliance, ethics)</p>
        </div>
        <button
          @click="handleLaunchAll"
          :disabled="launching !== null"
          class="px-4 py-2 rounded-md text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {{ launching === 'all' ? 'Launching...' : 'Launch All 5' }}
        </button>
      </div>

      <div v-if="pipelineClients.length" class="grid grid-cols-1 md:grid-cols-5 gap-3 mb-4">
        <div
          v-for="client in pipelineClients"
          :key="client.key"
          class="rounded-lg border border-border bg-muted/30 p-3"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-sm font-medium">{{ client.key }}</span>
            <button
              @click="handleLaunch(client.key)"
              :disabled="launching !== null"
              class="px-2 py-1 rounded text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {{ launching === client.key ? '...' : 'Launch' }}
            </button>
          </div>
          <p class="text-xs text-muted-foreground">{{ client.description }}</p>
          <div class="mt-2 flex flex-wrap gap-1">
            <span
              v-for="task in client.tasks"
              :key="task.id"
              class="inline-block px-1.5 py-0.5 rounded text-[10px] bg-muted text-muted-foreground"
            >{{ task.name }}</span>
          </div>
        </div>
      </div>

      <!-- Launch Results -->
      <div v-if="launchResults.length" class="border-t border-border pt-3">
        <h3 class="text-sm font-medium mb-2">Launch Results</h3>
        <div class="space-y-2">
          <div
            v-for="(r, i) in launchResults"
            :key="i"
            class="flex items-center gap-3 text-sm"
          >
            <span class="font-mono text-xs w-20">{{ r.client }}</span>
            <StatusBadge :status="r.status === 'launched' ? 'active' : r.status === 'failed' ? 'offline' : 'idle'" />
            <span v-if="r.pipeline_id" class="text-xs text-muted-foreground font-mono">
              pipeline={{ r.pipeline_id?.slice(0, 8) }}
            </span>
            <span v-if="r.run_id" class="text-xs text-muted-foreground font-mono">
              run={{ r.run_id?.slice(0, 8) }}
            </span>
            <span v-if="r.error" class="text-xs text-destructive">{{ r.error }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Errors -->
    <div v-if="errors.length" class="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
      <p class="text-sm font-medium text-destructive mb-1">Connection Issues</p>
      <ul class="text-xs text-destructive/80 space-y-1">
        <li v-for="(err, i) in errors" :key="i">{{ err }}</li>
      </ul>
    </div>
  </div>
</template>
