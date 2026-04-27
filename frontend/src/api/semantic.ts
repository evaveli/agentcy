import apiClient from './client'
import type {
  SemanticStatus, GraphSummary, DomainEntity, PlanRecommendation,
} from './types'

export async function getSemanticStatus(): Promise<SemanticStatus> {
  const { data } = await apiClient.get('/semantic/status')
  return data
}

export async function getGraphSummary(): Promise<GraphSummary> {
  const { data } = await apiClient.get('/semantic/graph/summary')
  return data
}

export async function executeSparql(query: string): Promise<unknown> {
  const { data } = await apiClient.post('/semantic/sparql', { query })
  return data
}

export async function findPlansByCapability(capability: string): Promise<unknown[]> {
  const { data } = await apiClient.get(`/semantic/plans/by-capability/${capability}`)
  return Array.isArray(data) ? data : data.results ?? []
}

export async function findPlansByAgent(agentId: string): Promise<unknown[]> {
  const { data } = await apiClient.get(`/semantic/plans/by-agent/${agentId}`)
  return Array.isArray(data) ? data : data.results ?? []
}

export async function findSimilarPlans(planId: string): Promise<unknown[]> {
  const { data } = await apiClient.get(`/semantic/plans/${planId}/similar`)
  return Array.isArray(data) ? data : data.results ?? []
}

export async function getPlanTaskGraph(planId: string): Promise<unknown> {
  const { data } = await apiClient.get(`/semantic/plans/${planId}/task-graph`)
  return data
}

export async function getPlanDetails(planId: string): Promise<unknown> {
  const { data } = await apiClient.get(`/semantic/plans/${planId}/details`)
  return data
}

export async function getCapabilityStats(): Promise<unknown> {
  const { data } = await apiClient.get('/semantic/capabilities/stats')
  return data
}

export async function getAgentSuccessRate(agentId: string): Promise<unknown> {
  const { data } = await apiClient.get(`/semantic/agents/${agentId}/success-rate`)
  return data
}

export async function getTaskAvgDuration(capability: string): Promise<unknown> {
  const { data } = await apiClient.get(`/semantic/capabilities/${capability}/avg-duration`)
  return data
}

export async function getFailurePatterns(capability: string): Promise<unknown> {
  const { data } = await apiClient.get(`/semantic/capabilities/${capability}/failure-patterns`)
  return data
}

export async function getDataLineage(runId: string, taskId: string): Promise<unknown> {
  const { data } = await apiClient.get(`/semantic/runs/${runId}/tasks/${taskId}/lineage`)
  return data
}

export async function getDownstreamImpact(runId: string, taskId: string): Promise<unknown> {
  const { data } = await apiClient.get(`/semantic/runs/${runId}/tasks/${taskId}/impact`)
  return data
}

export async function getTemplateExecutionSummary(templateId: string): Promise<unknown> {
  const { data } = await apiClient.get(`/semantic/templates/${templateId}/execution-summary`)
  return data
}

export async function getBestTemplateForCapability(capability: string): Promise<unknown> {
  const { data } = await apiClient.get(`/semantic/capabilities/${capability}/best-template`)
  return data
}

export async function recommendPlans(params: { capabilities: string; limit?: number }): Promise<PlanRecommendation> {
  const { data } = await apiClient.get('/semantic/plans/recommend', { params })
  return data
}

export async function listDomainEntities(params?: { type?: string; limit?: number }): Promise<DomainEntity[]> {
  const { data } = await apiClient.get('/semantic/domain/entities', { params })
  return Array.isArray(data) ? data : data.entities ?? []
}

export async function getDomainContext(params: { capabilities: string }): Promise<unknown> {
  const { data } = await apiClient.get('/semantic/domain/context', { params })
  return data
}

export async function syncSemanticLayer(): Promise<unknown> {
  const { data } = await apiClient.post('/semantic/sync')
  return data
}
