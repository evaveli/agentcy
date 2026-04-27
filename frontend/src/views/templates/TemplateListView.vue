<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import DataTable from '@/components/DataTable.vue'
import { listTemplates } from '@/api/templates'
import type { Template } from '@/api/types'

const router = useRouter()
const auth = useAuthStore()
const templates = ref<Template[]>([])

const columns = [
  { key: 'template_id', label: 'Template ID' },
  { key: 'name', label: 'Name' },
  { key: 'description', label: 'Description' },
  { key: 'version', label: 'Version' },
]

async function fetch() {
  templates.value = await listTemplates(auth.username)
}

const { loading } = usePolling(fetch, 15000)

function onRowClick(row: Record<string, unknown>) {
  router.push({ name: 'template-detail', params: { templateId: String(row.template_id) } })
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold">Templates</h1>
      <router-link
        to="/templates/create"
        class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
      >
        Create Template
      </router-link>
    </div>

    <DataTable
      :columns="columns"
      :rows="(templates as any[])"
      :loading="loading && templates.length === 0"
      empty-title="No templates"
      empty-message="Create your first template to get started."
      @row-click="onRowClick"
    >
      <template #cell-template_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
    </DataTable>
  </div>
</template>
