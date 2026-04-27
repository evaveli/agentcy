import apiClient from './client'
import type { HealthResponse, ReadyResponse } from './types'

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>('/health')
  return data
}

export async function getReady(): Promise<ReadyResponse> {
  const { data } = await apiClient.get<ReadyResponse>('/ready')
  return data
}
