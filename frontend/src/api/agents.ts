import apiClient from './client'
import type { Agent } from './types'

export async function listAgents(
  username: string,
  params?: { capability?: string; status?: string; tags?: string },
): Promise<Agent[]> {
  const { data } = await apiClient.get(`/agent-registry/${username}`, { params })
  return Array.isArray(data) ? data : data.agents ?? []
}

export async function getAgent(username: string, agentId: string): Promise<Agent> {
  const { data } = await apiClient.get(`/agent-registry/${username}/${agentId}`)
  return data
}

export async function registerAgent(username: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.post(`/agent-registry/${username}`, payload)
  return data
}

export async function deleteAgent(username: string, agentId: string): Promise<void> {
  await apiClient.delete(`/agent-registry/${username}/${agentId}`)
}
