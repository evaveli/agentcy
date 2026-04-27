<script setup lang="ts">
import { ref, computed } from 'vue'
import StatusBadge from '@/components/StatusBadge.vue'
import DataTable from '@/components/DataTable.vue'
import { getE4Dataset, runE4Stub } from '@/api/evaluation'
import type { E4StubResponse, E4TestCase, E4CategoryMetrics } from '@/api/evaluation'

const dataset = ref<E4TestCase[]>([])
const stubResults = ref<E4StubResponse | null>(null)
const loading = ref(false)
const datasetLoading = ref(true)
const activeTab = ref<'overview' | 'dataset' | 'results'>('overview')

// Fetch dataset on mount
;(async () => {
  try {
    const d = await getE4Dataset()
    dataset.value = d.cases
  } finally {
    datasetLoading.value = false
  }
})()

async function runStub() {
  loading.value = true
  try {
    stubResults.value = await runE4Stub()
    activeTab.value = 'results'
  } finally {
    loading.value = false
  }
}

const categoryColors: Record<string, string> = {
  pii: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300',
  price_manipulation: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-300',
  geographic_bias: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300',
  hallucination: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300',
  inappropriate_tone: 'bg-pink-100 text-pink-800 dark:bg-pink-900 dark:text-pink-300',
  clean: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300',
}

const datasetColumns = [
  { key: 'case_id', label: 'Case ID' },
  { key: 'category', label: 'Category' },
  { key: 'expected_detected', label: 'Expected' },
  { key: 'description', label: 'Description' },
]

const resultColumns = [
  { key: 'case_id', label: 'Case ID' },
  { key: 'category', label: 'Category' },
  { key: 'expected', label: 'Expected' },
  { key: 'predicted', label: 'Predicted' },
  { key: 'correct', label: 'Correct' },
]

function fmtPct(val: number): string {
  return (val * 100).toFixed(1) + '%'
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold">E4 — Ethics Detection Evaluation</h1>
        <p class="text-muted-foreground">Rule-based vs LLM detection on synthetic violation dataset</p>
      </div>
      <button
        @click="runStub"
        :disabled="loading"
        class="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
      >
        <span v-if="loading" class="flex items-center gap-2">
          <span class="h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" />
          Running...
        </span>
        <span v-else>Run Stub Mode</span>
      </button>
    </div>

    <!-- Tab Navigation -->
    <div class="flex gap-1 mb-6 border-b border-border">
      <button
        v-for="tab in (['overview', 'dataset', 'results'] as const)"
        :key="tab"
        @click="activeTab = tab"
        class="px-4 py-2 text-sm font-medium border-b-2 transition-colors"
        :class="activeTab === tab
          ? 'border-primary text-foreground'
          : 'border-transparent text-muted-foreground hover:text-foreground'"
      >
        {{ tab === 'overview' ? 'Overview' : tab === 'dataset' ? 'Dataset (50 cases)' : 'Results' }}
      </button>
    </div>

    <!-- Overview Tab -->
    <div v-if="activeTab === 'overview'">
      <div v-if="stubResults" class="space-y-6">
        <!-- Overall Metrics -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div class="rounded-lg border border-border bg-card p-4 text-center">
            <p class="text-sm text-muted-foreground">Accuracy</p>
            <p class="text-3xl font-bold">{{ stubResults.accuracy }}%</p>
          </div>
          <div class="rounded-lg border border-border bg-card p-4 text-center">
            <p class="text-sm text-muted-foreground">Precision</p>
            <p class="text-3xl font-bold">{{ fmtPct(stubResults.overall.precision) }}</p>
          </div>
          <div class="rounded-lg border border-border bg-card p-4 text-center">
            <p class="text-sm text-muted-foreground">Recall</p>
            <p class="text-3xl font-bold">{{ fmtPct(stubResults.overall.recall) }}</p>
          </div>
          <div class="rounded-lg border border-border bg-card p-4 text-center">
            <p class="text-sm text-muted-foreground">F1 Score</p>
            <p class="text-3xl font-bold">{{ fmtPct(stubResults.overall.f1) }}</p>
          </div>
        </div>

        <!-- Confusion Matrix -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Overall Confusion Matrix</h2>
          <div class="grid grid-cols-3 gap-px bg-border rounded overflow-hidden text-center text-sm max-w-xs">
            <div class="bg-card p-2"></div>
            <div class="bg-muted/50 p-2 font-medium">Pred. Violation</div>
            <div class="bg-muted/50 p-2 font-medium">Pred. Clean</div>
            <div class="bg-muted/50 p-2 font-medium">Actual Violation</div>
            <div class="bg-green-50 dark:bg-green-900/30 p-2 font-bold">{{ stubResults.overall.tp }}</div>
            <div class="bg-red-50 dark:bg-red-900/30 p-2 font-bold">{{ stubResults.overall.fn }}</div>
            <div class="bg-muted/50 p-2 font-medium">Actual Clean</div>
            <div class="bg-red-50 dark:bg-red-900/30 p-2 font-bold">{{ stubResults.overall.fp }}</div>
            <div class="bg-green-50 dark:bg-green-900/30 p-2 font-bold">{{ stubResults.overall.tn }}</div>
          </div>
        </div>

        <!-- Per-Category Metrics -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Per-Category Performance</h2>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border">
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Category</th>
                  <th class="px-3 py-2 text-center font-medium text-muted-foreground">TP</th>
                  <th class="px-3 py-2 text-center font-medium text-muted-foreground">FP</th>
                  <th class="px-3 py-2 text-center font-medium text-muted-foreground">FN</th>
                  <th class="px-3 py-2 text-center font-medium text-muted-foreground">TN</th>
                  <th class="px-3 py-2 text-center font-medium text-muted-foreground">Precision</th>
                  <th class="px-3 py-2 text-center font-medium text-muted-foreground">Recall</th>
                  <th class="px-3 py-2 text-center font-medium text-muted-foreground">F1</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(metrics, cat) in stubResults.per_category" :key="cat" class="border-b border-border last:border-0">
                  <td class="px-3 py-2">
                    <span :class="['inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', categoryColors[cat] || 'bg-gray-100 text-gray-800']">
                      {{ cat }}
                    </span>
                  </td>
                  <td class="px-3 py-2 text-center">{{ metrics.tp }}</td>
                  <td class="px-3 py-2 text-center">{{ metrics.fp }}</td>
                  <td class="px-3 py-2 text-center">{{ metrics.fn }}</td>
                  <td class="px-3 py-2 text-center">{{ metrics.tn }}</td>
                  <td class="px-3 py-2 text-center font-medium">{{ fmtPct(metrics.precision) }}</td>
                  <td class="px-3 py-2 text-center font-medium">{{ fmtPct(metrics.recall) }}</td>
                  <td class="px-3 py-2 text-center font-bold">{{ fmtPct(metrics.f1) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Visual Bars -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Recall by Category</h2>
          <div class="space-y-3">
            <div v-for="(metrics, cat) in stubResults.per_category" :key="cat" class="flex items-center gap-3">
              <span class="w-40 text-sm text-muted-foreground truncate">{{ cat }}</span>
              <div class="flex-1 h-5 bg-muted rounded-full overflow-hidden">
                <div
                  class="h-full bg-primary rounded-full transition-all"
                  :style="{ width: (metrics.recall * 100) + '%' }"
                />
              </div>
              <span class="w-14 text-sm font-medium text-right">{{ fmtPct(metrics.recall) }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="rounded-lg border border-border bg-card p-8 text-center">
        <p class="text-muted-foreground mb-4">Click "Run Stub Mode" to execute the ethics detection evaluation</p>
        <p class="text-sm text-muted-foreground">50 synthetic test cases will be evaluated against the rule-based ethics checker</p>
      </div>
    </div>

    <!-- Dataset Tab -->
    <div v-if="activeTab === 'dataset'">
      <DataTable
        :columns="datasetColumns"
        :rows="(dataset as any[])"
        :loading="datasetLoading"
        empty-title="No dataset"
        empty-message="Failed to load synthetic dataset."
      >
        <template #cell-category="{ value }">
          <span :class="['inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', categoryColors[String(value)] || 'bg-gray-100 text-gray-800']">
            {{ value }}
          </span>
        </template>
        <template #cell-expected_detected="{ value }">
          <StatusBadge :status="value ? 'violation' : 'clean'" />
        </template>
      </DataTable>
    </div>

    <!-- Results Tab -->
    <div v-if="activeTab === 'results'">
      <div v-if="stubResults">
        <DataTable
          :columns="resultColumns"
          :rows="(stubResults.detailed_results as any[])"
          :loading="false"
          empty-title="No results"
          empty-message="Run the evaluation first."
        >
          <template #cell-category="{ value }">
            <span :class="['inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', categoryColors[String(value)] || 'bg-gray-100 text-gray-800']">
              {{ value }}
            </span>
          </template>
          <template #cell-expected="{ value }">
            <StatusBadge :status="value ? 'violation' : 'clean'" />
          </template>
          <template #cell-predicted="{ value }">
            <StatusBadge :status="value ? 'violation' : 'clean'" />
          </template>
          <template #cell-correct="{ value }">
            <StatusBadge :status="value ? 'success' : 'failed'" />
          </template>
        </DataTable>
      </div>
      <div v-else class="rounded-lg border border-border bg-card p-8 text-center">
        <p class="text-muted-foreground">Run the evaluation first to see detailed results</p>
      </div>
    </div>
  </div>
</template>
