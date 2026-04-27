<script setup lang="ts">
import { ref, onMounted } from 'vue'
import DataTable from '@/components/DataTable.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import { getE1GroundTruth } from '@/api/evaluation'
import type { E1GroundTruth } from '@/api/evaluation'

const data = ref<E1GroundTruth | null>(null)
const loading = ref(true)
const selectedAgent = ref<string>('email')

const agentLabels: Record<string, string> = {
  email: 'Email Drafting',
  deal_summary: 'Deal Summary',
  necessity_form: 'Client Necessity Form',
  proposal: 'Proposal Template',
  warehouse: 'Warehouse Suggestion',
}

const agentMetrics: Record<string, string[]> = {
  email: ['Entity Accuracy %', 'Hallucination Count', 'Template Adherence %', 'Edit Distance', 'Response Appropriateness'],
  deal_summary: ['Checklist Coverage %', 'Structural Consistency %'],
  necessity_form: ['Field Exact Match %', 'Acceptability %', 'Error Rate %', 'Critical Errors'],
  proposal: ['Critical Error Count', 'Editing Effort', 'Section Completeness %'],
  warehouse: ['Top-1 Match', 'Top-3 Match', 'Hard Constraint Satisfaction %', 'Distance Delta'],
}

const globalMetrics = [
  { name: 'Correction Effort', description: 'Number of issues requiring manual correction (all agents)' },
  { name: 'Critical Error Count', description: 'Errors that would cause real business problems (all agents)' },
  { name: 'Task Success Rate', description: 'Binary: output usable without major rewrite (all agents)' },
]

onMounted(async () => {
  try {
    data.value = await getE1GroundTruth()
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-2">E1 — Agent Output Quality</h1>
    <p class="text-muted-foreground mb-6">Compare AI vs human output across 5 agents with objective metrics</p>

    <div v-if="loading" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading ground truth...
    </div>

    <template v-else-if="data">
      <!-- Agent Selector -->
      <div class="flex gap-2 mb-6 flex-wrap">
        <button
          v-for="(label, key) in agentLabels"
          :key="key"
          @click="selectedAgent = key"
          class="px-3 py-1.5 rounded-md text-sm font-medium transition-colors"
          :class="selectedAgent === key
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted text-muted-foreground hover:bg-muted/80'"
        >
          {{ label }}
        </button>
      </div>

      <!-- Agent Detail -->
      <div v-if="data.agents[selectedAgent]" class="space-y-6">
        <!-- Metrics Description -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">{{ agentLabels[selectedAgent] }} — Evaluation Metrics</h2>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div
              v-for="metric in agentMetrics[selectedAgent]"
              :key="metric"
              class="flex items-center gap-2 text-sm"
            >
              <span class="h-2 w-2 rounded-full bg-primary shrink-0" />
              <span>{{ metric }}</span>
            </div>
          </div>
        </div>

        <!-- Test Cases -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Ground Truth — {{ data.agents[selectedAgent].count }} test cases</h2>
          <div class="space-y-3">
            <div
              v-for="(tc, i) in data.agents[selectedAgent].cases"
              :key="i"
              class="rounded border border-border p-3"
            >
              <JsonViewer :data="tc" />
            </div>
          </div>
        </div>
      </div>

      <!-- Global Metrics -->
      <div class="mt-8 rounded-lg border border-blue-200 bg-blue-50 dark:bg-blue-900/20 dark:border-blue-800 p-4">
        <h2 class="font-semibold mb-3">Global Cross-Agent Metrics (Jordi's Requirement)</h2>
        <p class="text-sm text-muted-foreground mb-3">
          These 3 metrics are computed uniformly across all agents for comparable ablation analysis.
        </p>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div v-for="m in globalMetrics" :key="m.name" class="rounded border border-border bg-card p-3">
            <p class="font-medium text-sm">{{ m.name }}</p>
            <p class="text-xs text-muted-foreground mt-1">{{ m.description }}</p>
          </div>
        </div>
      </div>

      <!-- Seed Data Summary -->
      <div class="mt-8 rounded-lg border border-border bg-card p-4">
        <h2 class="font-semibold mb-3">Evaluation Data</h2>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
          <div>
            <p class="text-2xl font-bold">{{ Object.keys(data.deals).length }}</p>
            <p class="text-xs text-muted-foreground">Deals</p>
          </div>
          <div>
            <p class="text-2xl font-bold">{{ Object.keys(data.clients).length }}</p>
            <p class="text-xs text-muted-foreground">Clients</p>
          </div>
          <div>
            <p class="text-2xl font-bold">{{ Object.keys(data.warehouses).length }}</p>
            <p class="text-xs text-muted-foreground">Warehouses</p>
          </div>
          <div>
            <p class="text-2xl font-bold">{{ Object.keys(data.agents).length }}</p>
            <p class="text-xs text-muted-foreground">Agents</p>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
