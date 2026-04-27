<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import DataTable from '@/components/DataTable.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import { listPlanRevisions, diffPlanRevisions } from '@/api/graphStore'

const auth = useAuthStore()
const revisions = ref<unknown[]>([])
const loading = ref(true)
const diffResult = ref<unknown>(null)
const diffPlanId = ref('')
const diffFrom = ref('')
const diffTo = ref('')

const columns = [
  { key: 'plan_id', label: 'Plan ID' },
  { key: 'revision', label: 'Revision' },
  { key: 'created_at', label: 'Created' },
]

async function fetch() {
  loading.value = true
  try {
    revisions.value = await listPlanRevisions(auth.username)
  } finally {
    loading.value = false
  }
}

async function runDiff() {
  if (!diffPlanId.value || !diffFrom.value || !diffTo.value) return
  diffResult.value = await diffPlanRevisions(auth.username, diffPlanId.value, {
    from_rev: diffFrom.value,
    to_rev: diffTo.value,
  })
}

onMounted(fetch)
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">Plan Revisions</h1>

    <DataTable
      :columns="columns"
      :rows="(revisions as any[])"
      :loading="loading"
      empty-title="No revisions"
      empty-message="No plan revisions found."
    >
      <template #cell-plan_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
    </DataTable>

    <!-- Diff Tool -->
    <div class="mt-8 rounded-lg border border-border bg-card p-4">
      <h2 class="font-semibold mb-3">Compare Revisions</h2>
      <div class="flex gap-3 mb-4">
        <input v-model="diffPlanId" placeholder="Plan ID" class="rounded-md border border-input bg-background px-3 py-2 text-sm w-48 focus:outline-none focus:ring-2 focus:ring-ring" />
        <input v-model="diffFrom" placeholder="From revision" class="rounded-md border border-input bg-background px-3 py-2 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-ring" />
        <input v-model="diffTo" placeholder="To revision" class="rounded-md border border-input bg-background px-3 py-2 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-ring" />
        <button
          class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          @click="runDiff"
        >
          Diff
        </button>
      </div>
      <JsonViewer v-if="diffResult" :data="diffResult" />
    </div>
  </div>
</template>
