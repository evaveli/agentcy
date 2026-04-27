<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import StatusBadge from '@/components/StatusBadge.vue'
import { getHealth, getReady } from '@/api/health'
import { getGraphStoreStats } from '@/api/graphStore'
import { getCnpStats } from '@/api/cnp'
import { countTemplates } from '@/api/templates'
import { getGraphSummary, getSemanticStatus } from '@/api/semantic'
import type { HealthResponse, ReadyResponse, CnpStats, GraphSummary, SemanticStatus } from '@/api/types'

const auth = useAuthStore()

const health = ref<HealthResponse | null>(null)
const ready = ref<ReadyResponse | null>(null)
const stats = ref<Record<string, unknown>>({})
const cnp = ref<CnpStats>({})
const templateCount = ref(0)
const graphSummary = ref<GraphSummary>({})
const semanticStatus = ref<SemanticStatus | null>(null)
const errors = ref<string[]>([])

async function fetchAll() {
  errors.value = []
  const results = await Promise.allSettled([
    getHealth().then((d) => { health.value = d }),
    getReady().then((d) => { ready.value = d }),
    getGraphStoreStats(auth.username).then((d) => { stats.value = d }),
    getCnpStats(auth.username).then((d) => { cnp.value = d }),
    countTemplates(auth.username).then((d) => { templateCount.value = d }),
    getGraphSummary().then((d) => { graphSummary.value = d }),
    getSemanticStatus().then((d) => { semanticStatus.value = d }),
  ])
  results.forEach((r) => {
    if (r.status === 'rejected') errors.value.push(r.reason?.message || 'Unknown error')
  })
}

const { loading } = usePolling(fetchAll, 15000)
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">Dashboard</h1>

    <div v-if="loading && !health" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading...
    </div>

    <!-- Health Cards -->
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      <div class="rounded-lg border border-border bg-card p-4">
        <p class="text-sm text-muted-foreground mb-1">API Health</p>
        <StatusBadge v-if="health" :status="health.status" />
        <span v-else class="text-sm text-muted-foreground">--</span>
      </div>

      <div class="rounded-lg border border-border bg-card p-4">
        <p class="text-sm text-muted-foreground mb-1">Readiness</p>
        <StatusBadge v-if="ready" :status="ready.status" />
        <span v-else class="text-sm text-muted-foreground">--</span>
        <div v-if="ready?.checks" class="mt-2 space-y-1">
          <div v-for="(val, key) in ready.checks" :key="key" class="flex items-center justify-between text-xs">
            <span class="text-muted-foreground">{{ key }}</span>
            <StatusBadge :status="String(val)" />
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-border bg-card p-4">
        <p class="text-sm text-muted-foreground mb-1">Knowledge Graph</p>
        <p class="text-2xl font-bold">{{ graphSummary.total_triples ?? '--' }}</p>
        <p class="text-xs text-muted-foreground">triples</p>
        <div v-if="semanticStatus" class="mt-2">
          <StatusBadge :status="(semanticStatus.fuseki_enabled || (semanticStatus as any).enabled) ? 'active' : 'offline'" />
        </div>
      </div>

      <div class="rounded-lg border border-border bg-card p-4">
        <p class="text-sm text-muted-foreground mb-1">Templates</p>
        <p class="text-2xl font-bold">{{ templateCount }}</p>
        <p class="text-xs text-muted-foreground">registered</p>
      </div>
    </div>

    <!-- CNP & Graph Store Stats -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
      <div class="rounded-lg border border-border bg-card p-4">
        <h2 class="font-semibold mb-3">Contract Net Protocol</h2>
        <div class="grid grid-cols-3 gap-4 text-center">
          <div>
            <p class="text-2xl font-bold">{{ cnp.total_cfps ?? 0 }}</p>
            <p class="text-xs text-muted-foreground">CFPs</p>
          </div>
          <div>
            <p class="text-2xl font-bold">{{ cnp.total_bids ?? 0 }}</p>
            <p class="text-xs text-muted-foreground">Bids</p>
          </div>
          <div>
            <p class="text-2xl font-bold">{{ cnp.total_awards ?? 0 }}</p>
            <p class="text-xs text-muted-foreground">Awards</p>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-border bg-card p-4">
        <h2 class="font-semibold mb-3">Graph Store</h2>
        <div class="space-y-2 text-sm">
          <div v-for="(value, key) in stats" :key="String(key)" class="flex justify-between">
            <span class="text-muted-foreground capitalize">{{ String(key).replace(/_/g, ' ') }}</span>
            <span class="font-medium">{{ value }}</span>
          </div>
          <p v-if="Object.keys(stats).length === 0" class="text-muted-foreground">No data yet</p>
        </div>
      </div>
    </div>

    <!-- Errors -->
    <div v-if="errors.length > 0" class="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
      <p class="text-sm font-medium text-destructive mb-1">Connection Issues</p>
      <ul class="text-xs text-destructive/80 space-y-1">
        <li v-for="(err, i) in errors" :key="i">{{ err }}</li>
      </ul>
    </div>
  </div>
</template>
