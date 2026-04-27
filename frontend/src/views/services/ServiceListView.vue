<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { usePolling } from '@/composables/usePolling'
import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { listServices } from '@/api/services'
import type { ServiceRegistration } from '@/api/types'

const router = useRouter()
const auth = useAuthStore()
const services = ref<ServiceRegistration[]>([])

const columns = [
  { key: 'service_id', label: 'Service ID' },
  { key: 'name', label: 'Name' },
  { key: 'version', label: 'Version' },
  { key: 'status', label: 'Status' },
]

async function fetch() {
  services.value = await listServices(auth.username)
}

const { loading } = usePolling(fetch, 15000)

function onRowClick(row: Record<string, unknown>) {
  router.push({ name: 'service-detail', params: { serviceId: String(row.service_id) } })
}
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">Services</h1>

    <DataTable
      :columns="columns"
      :rows="(services as any[])"
      :loading="loading && services.length === 0"
      empty-title="No services"
      empty-message="No services registered yet."
      @row-click="onRowClick"
    >
      <template #cell-service_id="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-status="{ value }">
        <StatusBadge v-if="value" :status="String(value)" />
        <span v-else class="text-muted-foreground">--</span>
      </template>
    </DataTable>
  </div>
</template>
