<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import JsonViewer from '@/components/JsonViewer.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { getService, deleteService } from '@/api/services'
import type { ServiceRegistration } from '@/api/types'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const serviceId = route.params.serviceId as string

const service = ref<ServiceRegistration | null>(null)
const loading = ref(true)
const showDelete = ref(false)

async function fetch() {
  loading.value = true
  try {
    service.value = await getService(auth.username, serviceId)
  } finally {
    loading.value = false
  }
}

async function confirmDelete() {
  await deleteService(auth.username, serviceId)
  router.push({ name: 'services' })
}

onMounted(fetch)
</script>

<template>
  <div>
    <router-link to="/services" class="text-sm text-muted-foreground hover:underline">&larr; Services</router-link>
    <div class="flex items-center gap-3 mt-1 mb-6">
      <h1 class="text-2xl font-bold">{{ service?.name || serviceId }}</h1>
      <StatusBadge v-if="service?.status" :status="service.status" />
    </div>

    <div v-if="loading" class="flex items-center gap-2 text-muted-foreground">
      <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading...
    </div>

    <template v-else-if="service">
      <div class="rounded-lg border border-border bg-card p-4 mb-6">
        <h2 class="font-semibold mb-3">Service Details</h2>
        <JsonViewer :data="service" />
      </div>

      <button
        class="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90"
        @click="showDelete = true"
      >
        Delete Service
      </button>
    </template>

    <ConfirmDialog
      :open="showDelete"
      title="Delete Service"
      message="This will remove the service registration."
      confirm-label="Delete"
      :destructive="true"
      @confirm="confirmDelete"
      @cancel="showDelete = false"
    />
  </div>
</template>
