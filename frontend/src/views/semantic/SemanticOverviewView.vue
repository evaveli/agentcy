<script setup lang="ts">
import { ref, onMounted } from 'vue'
import StatusBadge from '@/components/StatusBadge.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import { getSemanticStatus, getGraphSummary, getCapabilityStats, syncSemanticLayer } from '@/api/semantic'
import type { SemanticStatus, GraphSummary } from '@/api/types'

const status = ref<SemanticStatus | null>(null)
const summary = ref<GraphSummary>({})
const capStats = ref<unknown>(null)
const loading = ref(true)
const syncing = ref(false)

async function fetch() {
  loading.value = true
  try {
    const [s, g, c] = await Promise.allSettled([
      getSemanticStatus(),
      getGraphSummary(),
      getCapabilityStats(),
    ])
    if (s.status === 'fulfilled') status.value = s.value
    if (g.status === 'fulfilled') summary.value = g.value
    if (c.status === 'fulfilled') capStats.value = c.value
  } finally {
    loading.value = false
  }
}

async function sync() {
  syncing.value = true
  try {
    await syncSemanticLayer()
    await fetch()
  } finally {
    syncing.value = false
  }
}

onMounted(fetch)
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold">Semantic Layer</h1>
      <button
        :disabled="syncing"
        class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        @click="sync"
      >
        {{ syncing ? 'Syncing...' : 'Sync Ontology & Shapes' }}
      </button>
    </div>

    <div v-if="loading" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading...
    </div>

    <template v-else>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div class="rounded-lg border border-border bg-card p-4">
          <p class="text-sm text-muted-foreground mb-1">Fuseki</p>
          <StatusBadge :status="status?.fuseki_enabled ? 'active' : 'offline'" />
          <p v-if="status?.fuseki_url" class="mt-1 text-xs font-mono text-muted-foreground">{{ status.fuseki_url }}</p>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <p class="text-sm text-muted-foreground mb-1">Total Triples</p>
          <p class="text-2xl font-bold">{{ summary.total_triples ?? '--' }}</p>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <p class="text-sm text-muted-foreground mb-1">Ontology Version</p>
          <p class="text-sm font-mono">{{ status?.ontology_version || '--' }}</p>
        </div>
      </div>

      <!-- Navigation -->
      <div class="flex flex-wrap gap-3 mb-6">
        <router-link to="/semantic/sparql" class="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80">
          SPARQL Explorer
        </router-link>
        <router-link to="/semantic/domain" class="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80">
          Domain Entities
        </router-link>
        <router-link to="/semantic/recommend" class="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80">
          Plan Recommendations
        </router-link>
      </div>

      <div v-if="capStats" class="rounded-lg border border-border bg-card p-4">
        <h2 class="font-semibold mb-3">Capability Statistics</h2>
        <JsonViewer :data="capStats" />
      </div>
    </template>
  </div>
</template>
