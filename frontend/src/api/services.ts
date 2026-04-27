import apiClient from './client'
import type { ServiceRegistration } from './types'

export async function listServices(username: string): Promise<ServiceRegistration[]> {
  const { data } = await apiClient.get(`/services/${username}`)
  return Array.isArray(data) ? data : data.services ?? []
}

export async function getService(username: string, serviceId: string): Promise<ServiceRegistration> {
  const { data } = await apiClient.get(`/services/${username}/${serviceId}`)
  return data
}

export async function upsertService(username: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.post(`/services/${username}`, payload)
  return data
}

export async function deleteService(username: string, serviceId: string): Promise<void> {
  await apiClient.delete(`/services/${username}/${serviceId}`)
}

export async function createWithArtifact(username: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.post(`/services/${username}/create-with-artifact`, payload)
  return data
}
