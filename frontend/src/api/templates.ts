import apiClient from './client'
import type { Template } from './types'

export async function listTemplates(
  username: string,
  params?: { offset?: number; limit?: number; capability?: string },
): Promise<Template[]> {
  const { data } = await apiClient.get(`/templates/${username}`, { params })
  return Array.isArray(data) ? data : data.templates ?? []
}

export async function getTemplate(username: string, templateId: string): Promise<Template> {
  const { data } = await apiClient.get(`/templates/${username}/${templateId}`)
  return data
}

export async function createTemplate(username: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.post(`/templates/${username}`, payload)
  return data
}

export async function updateTemplate(username: string, templateId: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.put(`/templates/${username}/${templateId}`, payload)
  return data
}

export async function deleteTemplate(username: string, templateId: string): Promise<void> {
  await apiClient.delete(`/templates/${username}/${templateId}`)
}

export async function countTemplates(username: string): Promise<number> {
  const { data } = await apiClient.get(`/templates/${username}/stats/count`)
  return typeof data === 'number' ? data : data.count ?? 0
}
