<script setup lang="ts">
import { ref, onMounted } from 'vue'
import DataTable from '@/components/DataTable.vue'
import { listDomainEntities } from '@/api/semantic'
import type { DomainEntity } from '@/api/types'

const entities = ref<DomainEntity[]>([])
const loading = ref(true)
const typeFilter = ref('')

const columns = [
  { key: 'name', label: 'Name' },
  { key: 'type', label: 'Type' },
  { key: 'description', label: 'Description' },
]

const entityTypes = [
  '', 'data_source', 'system', 'service', 'business_unit',
  'product', 'metric', 'workflow', 'policy', 'role',
]

async function fetch() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { limit: 100 }
    if (typeFilter.value) params.type = typeFilter.value
    entities.value = await listDomainEntities(params)
  } finally {
    loading.value = false
  }
}

onMounted(fetch)
</script>

<template>
  <div>
    <router-link to="/semantic" class="text-sm text-muted-foreground hover:underline">&larr; Semantic Layer</router-link>
    <h1 class="text-2xl font-bold mt-1 mb-6">Domain Entities</h1>

    <div class="mb-4">
      <select
        v-model="typeFilter"
        class="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        @change="fetch"
      >
        <option value="">All types</option>
        <option v-for="t in entityTypes.filter(Boolean)" :key="t" :value="t">{{ t }}</option>
      </select>
    </div>

    <DataTable
      :columns="columns"
      :rows="(entities as any[])"
      :loading="loading"
      empty-title="No domain entities"
      empty-message="No domain entities have been extracted yet. Run a pipeline to trigger domain knowledge extraction."
    >
      <template #cell-type="{ value }">
        <span class="rounded bg-secondary px-2 py-0.5 text-xs">{{ value }}</span>
      </template>
    </DataTable>
  </div>
</template>
