import apiClient from './client'
import type { CfpEntry, ContractAward, CnpCycle, CnpStats } from './types'

export async function getCnpStats(username: string): Promise<CnpStats> {
  const { data } = await apiClient.get(`/cnp/${username}/stats`)
  return data
}

export async function listCfps(username: string, params?: { offset?: number; limit?: number }): Promise<CfpEntry[]> {
  const { data } = await apiClient.get(`/cnp/${username}/cfps`, { params })
  return Array.isArray(data) ? data : data.cfps ?? []
}

export async function listAwards(username: string, params?: { offset?: number; limit?: number }): Promise<ContractAward[]> {
  const { data } = await apiClient.get(`/cnp/${username}/awards`, { params })
  return Array.isArray(data) ? data : data.awards ?? []
}

export async function listCnpCycles(username: string, params?: Record<string, unknown>): Promise<CnpCycle[]> {
  const { data } = await apiClient.get(`/cnp/${username}/cycles`, { params })
  return Array.isArray(data) ? data : data.cycles ?? []
}

export async function getCnpCycle(username: string, cycleId: string): Promise<CnpCycle> {
  const { data } = await apiClient.get(`/cnp/${username}/cycles/${cycleId}`)
  return data
}

export async function triggerCnpCycle(
  username: string,
  payload?: { pipeline_id: string; task_ids?: string[]; max_rounds?: number; bid_timeout_seconds?: number },
): Promise<unknown> {
  const { data } = await apiClient.post(`/cnp/${username}/cycle`, payload ?? {})
  return data
}

export async function submitBid(username: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.post(`/cnp/${username}/bid`, payload)
  return data
}
