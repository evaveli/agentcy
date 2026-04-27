<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { createTemplate } from '@/api/templates'

const router = useRouter()
const auth = useAuthStore()

const jsonInput = ref('{\n  "name": "",\n  "description": "",\n  "capabilities": [],\n  "config": {}\n}')
const error = ref<string | null>(null)
const submitting = ref(false)

async function submit() {
  error.value = null
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(jsonInput.value)
  } catch {
    error.value = 'Invalid JSON'
    return
  }
  submitting.value = true
  try {
    await createTemplate(auth.username, parsed)
    router.push({ name: 'templates' })
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div>
    <router-link to="/templates" class="text-sm text-muted-foreground hover:underline">
      &larr; Templates
    </router-link>
    <h1 class="text-2xl font-bold mt-1 mb-6">Create Template</h1>

    <div class="max-w-2xl">
      <label class="block text-sm font-medium mb-2">Template Configuration (JSON)</label>
      <textarea
        v-model="jsonInput"
        rows="12"
        class="w-full rounded-lg border border-input bg-background px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-ring"
      />
      <div v-if="error" class="mt-2 text-sm text-destructive">{{ error }}</div>
      <div class="mt-4 flex gap-3">
        <button
          :disabled="submitting"
          class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          @click="submit"
        >
          {{ submitting ? 'Creating...' : 'Create Template' }}
        </button>
        <router-link
          to="/templates"
          class="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80"
        >
          Cancel
        </router-link>
      </div>
    </div>
  </div>
</template>
