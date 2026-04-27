<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import StatusBadge from '@/components/StatusBadge.vue'
import DataTable from '@/components/DataTable.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import {
  getE3Configs, getE3GroundTruth, runE3Ablation, runComplianceTest,
} from '@/api/evaluation'
import type {
  E3Config, E3AblationResponse, E3ConfigSummary, E3GroundTruthResponse,
  ComplianceResponse,
} from '@/api/evaluation'

const configs = ref<Record<string, E3Config>>({})
const groundTruth = ref<E3GroundTruthResponse | null>(null)
const complianceResult = ref<ComplianceResponse | null>(null)
const ablationResult = ref<E3AblationResponse | null>(null)
const loading = ref(true)
const runningCompliance = ref(false)
const runningAblation = ref(false)
const selectedConfigs = ref<string[]>(['C0_full', 'C2_no_cnp', 'C7_minimal'])
const activeTab = ref<'ground-truth' | 'compliance' | 'ablation'>('ground-truth')

const configColors: Record<string, string> = {
  C0_full: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300',
  C1_no_pheromone: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300',
  C2_no_cnp: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300',
  C3_no_shacl: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300',
  C4_no_compliance: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300',
  C34_no_validation: 'bg-pink-100 text-pink-800 dark:bg-pink-900 dark:text-pink-300',
  C5_no_strategist: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300',
  C6_no_ethics: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-300',
  C7_minimal: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300',
}

const severityColors: Record<string, string> = {
  BLOCK: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300',
  WARN: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300',
}

onMounted(async () => {
  try {
    const [c, gt] = await Promise.allSettled([
      getE3Configs(),
      getE3GroundTruth(),
    ])
    if (c.status === 'fulfilled') configs.value = c.value
    if (gt.status === 'fulfilled') groundTruth.value = gt.value
  } finally {
    loading.value = false
  }
})

async function runCompliance() {
  runningCompliance.value = true
  try {
    complianceResult.value = await runComplianceTest()
    activeTab.value = 'compliance'
  } finally {
    runningCompliance.value = false
  }
}

function toggleConfig(name: string) {
  const idx = selectedConfigs.value.indexOf(name)
  if (idx >= 0) selectedConfigs.value.splice(idx, 1)
  else selectedConfigs.value.push(name)
}

async function runAblation() {
  runningAblation.value = true
  try {
    ablationResult.value = await runE3Ablation({
      configs: selectedConfigs.value,
      deal_ids: [1, 2, 3, 4, 5],
      inject_violations: true,
      inject_failures: true,
      failure_rate: 0.2,
    })
    activeTab.value = 'ablation'
  } finally {
    runningAblation.value = false
  }
}

const complianceScenarioCorrect = computed(() => {
  if (!complianceResult.value) return 0
  return complianceResult.value.seeded_scenario_results.filter(s => s.correct).length
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-2">
      <div>
        <h1 class="text-2xl font-bold">E3 — Ablation Study</h1>
        <p class="text-muted-foreground">Component contribution analysis with 9 configurations (C0-C7 + C3+C4)</p>
      </div>
      <div class="flex gap-2">
        <button
          @click="runCompliance"
          :disabled="runningCompliance"
          class="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
        >
          <span v-if="runningCompliance" class="flex items-center gap-2">
            <span class="h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" />
            Running...
          </span>
          <span v-else>Run Compliance Test</span>
        </button>
      </div>
    </div>

    <!-- Status badges -->
    <div class="flex gap-2 mb-6 flex-wrap">
      <span class="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium bg-green-100 text-green-800">
        Compliance Test — runs locally, no deps
      </span>
      <span class="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium bg-yellow-100 text-yellow-800">
        Full Ablation — requires docker-compose stack
      </span>
    </div>

    <div v-if="loading" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading...
    </div>

    <template v-else>
      <!-- Tabs -->
      <div class="flex gap-1 mb-6 border-b border-border">
        <button
          v-for="tab in (['ground-truth', 'compliance', 'ablation'] as const)"
          :key="tab"
          @click="activeTab = tab"
          class="px-4 py-2 text-sm font-medium border-b-2 transition-colors"
          :class="activeTab === tab
            ? 'border-primary text-foreground'
            : 'border-transparent text-muted-foreground hover:text-foreground'"
        >
          {{ tab === 'ground-truth' ? 'Ground Truth & Hypotheses' : tab === 'compliance' ? 'Compliance Test' : 'Full Ablation' }}
        </button>
      </div>

      <!-- Ground Truth Tab -->
      <div v-if="activeTab === 'ground-truth' && groundTruth" class="space-y-6">
        <!-- Hypotheses -->
        <div class="rounded-lg border border-blue-200 bg-blue-50 dark:bg-blue-900/20 dark:border-blue-800 p-4">
          <h2 class="font-semibold mb-3">Research Hypotheses</h2>
          <div class="space-y-2">
            <div v-for="(desc, id) in groundTruth.hypotheses" :key="id" class="flex gap-3 text-sm">
              <span class="font-mono font-bold text-primary shrink-0">{{ id }}</span>
              <span class="text-muted-foreground">{{ desc }}</span>
            </div>
          </div>
        </div>

        <!-- Assignment Ground Truth -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Assignment Accuracy Ground Truth</h2>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border">
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Client</th>
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Correct Warehouse Agent</th>
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Correct Estimator</th>
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Priority</th>
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Rationale</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="a in groundTruth.assignments" :key="a.client_id" class="border-b border-border last:border-0">
                  <td class="px-3 py-2 font-medium">{{ a.client_name }}</td>
                  <td class="px-3 py-2">
                    <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300">
                      {{ a.correct_warehouse_agent }}
                    </span>
                  </td>
                  <td class="px-3 py-2">
                    <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300">
                      {{ a.correct_estimator }}
                    </span>
                  </td>
                  <td class="px-3 py-2">
                    <StatusBadge :status="a.priority" />
                  </td>
                  <td class="px-3 py-2 text-xs text-muted-foreground max-w-xs">{{ a.warehouse_rationale }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Seeded Violation Scenarios -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Seeded Violation Scenarios (for validation layer testing)</h2>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border">
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">ID</th>
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Client → Warehouse</th>
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Should Block?</th>
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Expected Rules</th>
                  <th class="px-3 py-2 text-left font-medium text-muted-foreground">Description</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in groundTruth.seeded_scenarios" :key="s.scenario_id" class="border-b border-border last:border-0">
                  <td class="px-3 py-2 font-mono text-xs">{{ s.scenario_id }}</td>
                  <td class="px-3 py-2">{{ s.client_name }} → {{ s.warehouse_name }}</td>
                  <td class="px-3 py-2">
                    <StatusBadge :status="s.should_block ? 'error' : 'success'" />
                  </td>
                  <td class="px-3 py-2">
                    <span v-for="rule in s.expected_rules" :key="rule"
                      class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-red-100 text-red-800 mr-1">
                      {{ rule }}
                    </span>
                    <span v-if="!s.expected_rules.length" class="text-muted-foreground text-xs">none</span>
                  </td>
                  <td class="px-3 py-2 text-xs text-muted-foreground">{{ s.description }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Configurations -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Ablation Configurations (9 total)</h2>
          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            <div v-for="(config, name) in configs" :key="name" class="rounded border border-border p-3">
              <span :class="['inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium mb-2', configColors[name as string] || 'bg-gray-100 text-gray-800']">
                {{ name }}
              </span>
              <p class="text-xs text-muted-foreground">{{ config.description }}</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Compliance Test Tab -->
      <div v-if="activeTab === 'compliance'">
        <div v-if="complianceResult" class="space-y-6">
          <!-- Summary Cards -->
          <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div class="rounded-lg border border-border bg-card p-4 text-center">
              <p class="text-sm text-muted-foreground">Pairs Checked</p>
              <p class="text-2xl font-bold">{{ complianceResult.checked_pairs }}</p>
            </div>
            <div class="rounded-lg border border-border bg-card p-4 text-center">
              <p class="text-sm text-muted-foreground">Blocks</p>
              <p class="text-2xl font-bold text-red-600">{{ complianceResult.blocks }}</p>
            </div>
            <div class="rounded-lg border border-border bg-card p-4 text-center">
              <p class="text-sm text-muted-foreground">Warnings</p>
              <p class="text-2xl font-bold text-yellow-600">{{ complianceResult.warnings }}</p>
            </div>
            <div class="rounded-lg border border-border bg-card p-4 text-center">
              <p class="text-sm text-muted-foreground">GT Detection</p>
              <p class="text-2xl font-bold text-green-600">{{ complianceResult.ground_truth_detection_rate }}%</p>
            </div>
            <div class="rounded-lg border border-border bg-card p-4 text-center">
              <p class="text-sm text-muted-foreground">Scenarios Correct</p>
              <p class="text-2xl font-bold">{{ complianceScenarioCorrect }}/{{ complianceResult.seeded_scenario_results.length }}</p>
            </div>
          </div>

          <!-- Seeded Scenario Results -->
          <div class="rounded-lg border border-border bg-card p-4">
            <h2 class="font-semibold mb-3">Seeded Scenario Validation</h2>
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="border-b border-border">
                    <th class="px-3 py-2 text-left font-medium text-muted-foreground">ID</th>
                    <th class="px-3 py-2 text-left font-medium text-muted-foreground">Pairing</th>
                    <th class="px-3 py-2 text-center font-medium text-muted-foreground">Should Block</th>
                    <th class="px-3 py-2 text-center font-medium text-muted-foreground">Actually Blocked</th>
                    <th class="px-3 py-2 text-center font-medium text-muted-foreground">Result</th>
                    <th class="px-3 py-2 text-left font-medium text-muted-foreground">Violations Found</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="s in complianceResult.seeded_scenario_results" :key="s.scenario_id" class="border-b border-border last:border-0">
                    <td class="px-3 py-2 font-mono text-xs">{{ s.scenario_id }}</td>
                    <td class="px-3 py-2 text-xs">{{ s.client }} → {{ s.warehouse }}</td>
                    <td class="px-3 py-2 text-center">
                      <StatusBadge :status="s.should_block ? 'error' : 'success'" />
                    </td>
                    <td class="px-3 py-2 text-center">
                      <StatusBadge :status="s.actually_blocked ? 'error' : 'success'" />
                    </td>
                    <td class="px-3 py-2 text-center">
                      <StatusBadge :status="s.correct ? 'success' : 'failed'" />
                    </td>
                    <td class="px-3 py-2">
                      <div v-for="v in s.violations_found" :key="v.rule" class="text-xs">
                        <span :class="['inline-flex items-center rounded-full px-1.5 py-0.5 font-medium mr-1', severityColors[v.severity] || 'bg-gray-100']">
                          {{ v.severity }}
                        </span>
                        {{ v.rule }}
                      </div>
                      <span v-if="!s.violations_found.length" class="text-xs text-muted-foreground">none</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- All Violations -->
          <div class="rounded-lg border border-border bg-card p-4">
            <h2 class="font-semibold mb-3">All Detected Violations ({{ complianceResult.total_violations }})</h2>
            <div class="max-h-96 overflow-y-auto">
              <table class="w-full text-sm">
                <thead class="sticky top-0 bg-card">
                  <tr class="border-b border-border">
                    <th class="px-3 py-2 text-left font-medium text-muted-foreground">Client</th>
                    <th class="px-3 py-2 text-left font-medium text-muted-foreground">Warehouse</th>
                    <th class="px-3 py-2 text-left font-medium text-muted-foreground">Rule</th>
                    <th class="px-3 py-2 text-left font-medium text-muted-foreground">Severity</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(v, i) in complianceResult.violations" :key="i" class="border-b border-border last:border-0">
                    <td class="px-3 py-2 text-xs">{{ v.client }}</td>
                    <td class="px-3 py-2 text-xs">{{ v.warehouse }}</td>
                    <td class="px-3 py-2 text-xs font-mono">{{ v.rule }}</td>
                    <td class="px-3 py-2">
                      <span :class="['inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', severityColors[v.severity] || 'bg-gray-100']">
                        {{ v.severity }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div v-else class="rounded-lg border border-border bg-card p-8 text-center">
          <p class="text-muted-foreground mb-4">Click "Run Compliance Test" to validate all client-warehouse pairings</p>
          <p class="text-sm text-muted-foreground">Runs locally against seed data — no Docker or API keys needed</p>
        </div>
      </div>

      <!-- Ablation Tab -->
      <div v-if="activeTab === 'ablation'">
        <!-- Config selector + run button -->
        <div class="rounded-lg border border-border bg-card p-4 mb-6">
          <div class="flex items-center justify-between mb-3">
            <h2 class="font-semibold">Select Configurations</h2>
            <button
              @click="runAblation"
              :disabled="runningAblation || selectedConfigs.length === 0"
              class="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
            >
              <span v-if="runningAblation" class="flex items-center gap-2">
                <span class="h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" />
                Running...
              </span>
              <span v-else>Run Ablation ({{ selectedConfigs.length }} configs)</span>
            </button>
          </div>
          <div class="flex gap-2 flex-wrap">
            <button
              v-for="name in Object.keys(configs)"
              :key="name"
              @click="toggleConfig(name)"
              class="px-3 py-1.5 rounded-md text-xs font-medium transition-colors border"
              :class="selectedConfigs.includes(name)
                ? 'border-primary bg-primary/10 text-foreground'
                : 'border-border text-muted-foreground hover:border-muted-foreground/30'"
            >
              {{ name }}
            </button>
          </div>
          <p class="text-xs text-muted-foreground mt-2">Requires the full docker-compose stack running with OPENAI_API_KEY set</p>
        </div>

        <div v-if="ablationResult">
          <JsonViewer :data="ablationResult" />
        </div>
        <div v-else class="rounded-lg border border-border bg-card p-8 text-center">
          <p class="text-muted-foreground">Select configs and run the ablation study</p>
        </div>
      </div>
    </template>
  </div>
</template>
