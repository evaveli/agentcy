<script setup lang="ts">
import { ref } from 'vue'
import JsonViewer from '@/components/JsonViewer.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { recommendPlans } from '@/api/semantic'
import type { PlanRecommendation } from '@/api/types'

const capabilitiesInput = ref('')
const result = ref<PlanRecommendation | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)

async function search() {
  if (!capabilitiesInput.value.trim()) return
  error.value = null
  result.value = null
  loading.value = true
  try {
    result.value = await recommendPlans({
      capabilities: capabilitiesInput.value.trim(),
    })
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div>
    <router-link to="/semantic" class="text-sm text-muted-foreground hover:underline">&larr; Semantic Layer</router-link>
    <h1 class="text-2xl font-bold mt-1 mb-6">Plan Recommendations</h1>

    <div class="flex gap-3 mb-6">
      <input
        v-model="capabilitiesInput"
        placeholder="Enter capabilities (comma-separated, e.g. data_read,validate)"
        class="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        @keyup.enter="search"
      />
      <button
        :disabled="loading"
        class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        @click="search"
      >
        {{ loading ? 'Searching...' : 'Find Similar Plans' }}
      </button>
    </div>

    <div v-if="error" class="mb-4 rounded-lg border border-destructive/50 bg-destructive/10 p-4">
      <p class="text-sm text-destructive">{{ error }}</p>
    </div>

    <template v-if="result">
      <!-- Similar Plans -->
      <div class="rounded-lg border border-border bg-card p-4 mb-6">
        <h2 class="font-semibold mb-3">Similar Plans ({{ result.similar_plans.length }})</h2>
        <div v-if="result.similar_plans.length === 0" class="text-sm text-muted-foreground">No similar plans found.</div>
        <div v-else class="space-y-3">
          <div v-for="plan in result.similar_plans" :key="plan.plan_id" class="rounded-md border border-border p-3">
            <div class="flex items-center justify-between mb-2">
              <span class="font-mono text-sm">{{ plan.plan_id }}</span>
              <span class="text-xs text-muted-foreground">{{ plan.shared_capabilities }} shared capabilities</span>
            </div>
            <div v-if="plan.execution_summary" class="grid grid-cols-3 gap-2 text-center text-xs">
              <div>
                <p class="font-medium">{{ plan.execution_summary.total }}</p>
                <p class="text-muted-foreground">Total</p>
              </div>
              <div>
                <p class="font-medium text-green-600">{{ plan.execution_summary.successes }}</p>
                <p class="text-muted-foreground">Successes</p>
              </div>
              <div>
                <p class="font-medium">{{ plan.execution_summary.avg_duration?.toFixed(1) ?? '--' }}s</p>
                <p class="text-muted-foreground">Avg Duration</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Capability Stats -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Capability Stats</h2>
          <div v-if="Object.keys(result.capability_stats).length === 0" class="text-sm text-muted-foreground">No data</div>
          <div v-else class="space-y-2">
            <div v-for="(stat, cap) in result.capability_stats" :key="cap" class="flex items-center justify-between text-sm">
              <span class="rounded bg-secondary px-2 py-0.5 text-xs">{{ cap }}</span>
              <span class="text-muted-foreground">{{ stat.avg_duration?.toFixed(1) }}s avg / {{ stat.sample_count }} samples</span>
            </div>
          </div>
        </div>

        <!-- Recommended Templates -->
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Recommended Templates</h2>
          <div v-if="result.recommended_templates.length === 0" class="text-sm text-muted-foreground">No recommendations</div>
          <div v-else class="space-y-2">
            <div v-for="tmpl in result.recommended_templates" :key="tmpl.template_id" class="flex items-center justify-between text-sm">
              <router-link :to="{ name: 'template-detail', params: { templateId: tmpl.template_id } }" class="font-mono text-xs text-primary hover:underline">
                {{ tmpl.template_name || tmpl.template_id }}
              </router-link>
              <span class="text-muted-foreground">{{ (tmpl.success_rate * 100).toFixed(0) }}% success</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
