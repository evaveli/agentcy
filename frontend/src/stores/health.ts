import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getHealth, getReady } from '@/api/health'
import type { HealthResponse, ReadyResponse } from '@/api/types'

export const useHealthStore = defineStore('health', () => {
  const health = ref<HealthResponse | null>(null)
  const ready = ref<ReadyResponse | null>(null)
  const error = ref<string | null>(null)

  async function fetchHealth() {
    try {
      health.value = await getHealth()
      error.value = null
    } catch (e: any) {
      error.value = e.message
    }
  }

  async function fetchReady() {
    try {
      ready.value = await getReady()
    } catch {
      // readiness may fail during startup
    }
  }

  async function fetchAll() {
    await Promise.all([fetchHealth(), fetchReady()])
  }

  return { health, ready, error, fetchAll }
})
