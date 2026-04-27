<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import { listEthicsPolicies, getActiveEthicsPolicy, listEthicsChecks } from '@/api/graphStore'
import type { EthicsPolicy, EthicsCheck } from '@/api/types'

const auth = useAuthStore()
const policies = ref<EthicsPolicy[]>([])
const activePolicy = ref<EthicsPolicy | null>(null)
const checks = ref<EthicsCheck[]>([])
const loading = ref(true)

const policyColumns = [
  { key: 'policy_id', label: 'Policy ID' },
  { key: 'name', label: 'Name' },
  { key: 'active', label: 'Active' },
]

const checkColumns = [
  { key: 'check_id', label: 'Check ID' },
  { key: 'plan_id', label: 'Plan ID' },
  { key: 'result', label: 'Result' },
]

async function fetch() {
  loading.value = true
  try {
    const [p, a, c] = await Promise.allSettled([
      listEthicsPolicies(auth.username),
      getActiveEthicsPolicy(auth.username),
      listEthicsChecks(auth.username),
    ])
    if (p.status === 'fulfilled') policies.value = p.value
    if (a.status === 'fulfilled') activePolicy.value = a.value
    if (c.status === 'fulfilled') checks.value = c.value
  } finally {
    loading.value = false
  }
}

onMounted(fetch)
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">Ethics</h1>

    <!-- Active Policy -->
    <div v-if="activePolicy" class="rounded-lg border border-green-300 bg-green-50 dark:bg-green-900/20 p-4 mb-6">
      <h2 class="font-semibold mb-2">Active Policy: {{ activePolicy.name }}</h2>
      <JsonViewer :data="activePolicy" />
    </div>

    <!-- Policies -->
    <h2 class="text-lg font-semibold mb-3">Policies</h2>
    <DataTable
      :columns="policyColumns"
      :rows="(policies as any[])"
      :loading="loading"
      empty-title="No policies"
      empty-message="No ethics policies configured."
    >
      <template #cell-active="{ value }">
        <StatusBadge :status="value ? 'active' : 'inactive'" />
      </template>
    </DataTable>

    <!-- Checks -->
    <h2 class="text-lg font-semibold mt-8 mb-3">Ethics Checks</h2>
    <DataTable
      :columns="checkColumns"
      :rows="(checks as any[])"
      :loading="loading"
      empty-title="No checks"
      empty-message="No ethics checks have been run."
    >
      <template #cell-result="{ value }">
        <StatusBadge :status="String(value || 'unknown')" />
      </template>
    </DataTable>
  </div>
</template>
