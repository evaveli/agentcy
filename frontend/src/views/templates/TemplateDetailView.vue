<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import JsonViewer from '@/components/JsonViewer.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { getTemplate, deleteTemplate } from '@/api/templates'
import { getTemplateExecutionSummary } from '@/api/semantic'
import type { Template } from '@/api/types'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const templateId = route.params.templateId as string

const template = ref<Template | null>(null)
const execSummary = ref<unknown>(null)
const loading = ref(true)
const showDelete = ref(false)

async function fetch() {
  loading.value = true
  try {
    template.value = await getTemplate(auth.username, templateId)
    try {
      execSummary.value = await getTemplateExecutionSummary(templateId)
    } catch { /* semantic may not be enabled */ }
  } finally {
    loading.value = false
  }
}

async function confirmDelete() {
  await deleteTemplate(auth.username, templateId)
  router.push({ name: 'templates' })
}

onMounted(fetch)
</script>

<template>
  <div>
    <router-link to="/templates" class="text-sm text-muted-foreground hover:underline">
      &larr; Templates
    </router-link>
    <h1 class="text-2xl font-bold mt-1 mb-6">{{ template?.name || templateId }}</h1>

    <div v-if="loading" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading...
    </div>

    <template v-else-if="template">
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Template Config</h2>
          <JsonViewer :data="template" />
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="font-semibold mb-3">Execution Summary</h2>
          <JsonViewer v-if="execSummary" :data="execSummary" />
          <p v-else class="text-sm text-muted-foreground">Not available</p>
        </div>
      </div>

      <div class="mt-4">
        <button
          class="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90"
          @click="showDelete = true"
        >
          Delete Template
        </button>
      </div>
    </template>

    <ConfirmDialog
      :open="showDelete"
      title="Delete Template"
      message="This will permanently delete this template."
      confirm-label="Delete"
      :destructive="true"
      @confirm="confirmDelete"
      @cancel="showDelete = false"
    />
  </div>
</template>
