<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import JsonViewer from '@/components/JsonViewer.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { getPlanDraft, buildPlanDraft } from '@/api/graphStore'
import type { PlanDraft } from '@/api/types'

const route = useRoute()
const auth = useAuthStore()
const planId = route.params.planId as string

const draft = ref<PlanDraft | null>(null)
const loading = ref(true)
const building = ref(false)
const buildResult = ref<unknown>(null)

async function fetch() {
  loading.value = true
  try {
    draft.value = await getPlanDraft(auth.username, planId)
  } finally {
    loading.value = false
  }
}

async function build() {
  building.value = true
  try {
    buildResult.value = await buildPlanDraft(auth.username, { plan_id: planId })
  } catch (e: any) {
    buildResult.value = { error: e.response?.data?.detail || e.message }
  } finally {
    building.value = false
  }
}

onMounted(fetch)
</script>

<template>
  <div>
    <router-link to="/graph-store/plans" class="text-sm text-muted-foreground hover:underline">
      &larr; Plan Drafts
    </router-link>
    <div class="flex items-center gap-3 mt-1 mb-6">
      <h1 class="text-2xl font-bold">Plan: {{ planId }}</h1>
      <StatusBadge v-if="draft?.status" :status="draft.status" />
    </div>

    <div v-if="loading" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading...
    </div>

    <template v-else-if="draft">
      <div class="mb-4">
        <button
          :disabled="building"
          class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          @click="build"
        >
          {{ building ? 'Building...' : 'Build Plan' }}
        </button>
      </div>

      <div class="rounded-lg border border-border bg-card p-4 mb-6">
        <h2 class="font-semibold mb-3">Draft Data</h2>
        <JsonViewer :data="draft" />
      </div>

      <div v-if="buildResult" class="rounded-lg border border-border bg-card p-4">
        <h2 class="font-semibold mb-3">Build Result</h2>
        <JsonViewer :data="buildResult" />
      </div>
    </template>
  </div>
</template>
