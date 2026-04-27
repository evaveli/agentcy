import apiClient from './client'
import type {
  PlanDraft, TaskSpec, Bid, EthicsPolicy, EthicsCheck,
  AuditLog, Escalation, PlanSuggestion, HumanApproval, ExecutionReport,
  GraphStoreStat,
} from './types'

// ── Plan Drafts ─────────────────────────────────────────────────────
export async function listPlanDrafts(username: string, params?: { offset?: number; limit?: number }): Promise<PlanDraft[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/plan-drafts`, { params })
  return Array.isArray(data) ? data : data.drafts ?? []
}

export async function getPlanDraft(username: string, planId: string): Promise<PlanDraft> {
  const { data } = await apiClient.get(`/graph-store/${username}/plan-drafts/${planId}`)
  return data
}

export async function savePlanDraft(username: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.post(`/graph-store/${username}/plan-drafts`, payload)
  return data
}

export async function buildPlanDraft(username: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.post(`/graph-store/${username}/plan-drafts/build`, payload)
  return data
}

// ── Plan Revisions ──────────────────────────────────────────────────
export async function listPlanRevisions(username: string, params?: { plan_id?: string }): Promise<unknown[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/plan-revisions`, { params })
  return Array.isArray(data) ? data : data.revisions ?? []
}

export async function getPlanRevision(username: string, planId: string, revision: string): Promise<unknown> {
  const { data } = await apiClient.get(`/graph-store/${username}/plan-revisions/${planId}/${revision}`)
  return data
}

export async function diffPlanRevisions(username: string, planId: string, params: { from_rev: string; to_rev: string }): Promise<unknown> {
  const { data } = await apiClient.get(`/graph-store/${username}/plan-revisions/${planId}/diff`, { params })
  return data
}

// ── Plan Suggestions ────────────────────────────────────────────────
export async function listPlanSuggestions(username: string): Promise<PlanSuggestion[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/plan-suggestions`)
  return Array.isArray(data) ? data : data.suggestions ?? []
}

export async function getPlanSuggestion(username: string, suggestionId: string): Promise<PlanSuggestion> {
  const { data } = await apiClient.get(`/graph-store/${username}/plan-suggestions/${suggestionId}`)
  return data
}

export async function decidePlanSuggestion(username: string, suggestionId: string, payload: { decision: string }): Promise<unknown> {
  const { data } = await apiClient.post(`/graph-store/${username}/plan-suggestions/${suggestionId}/decision`, payload)
  return data
}

// ── Task Specs ──────────────────────────────────────────────────────
export async function listTaskSpecs(username: string, params?: { offset?: number; limit?: number }): Promise<TaskSpec[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/task-specs`, { params })
  return Array.isArray(data) ? data : data.task_specs ?? []
}

export async function getTaskSpec(username: string, taskId: string): Promise<TaskSpec> {
  const { data } = await apiClient.get(`/graph-store/${username}/task-specs/${taskId}`)
  return data
}

export async function createTaskSpec(username: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.post(`/graph-store/${username}/task-specs`, payload)
  return data
}

// ── Bids ────────────────────────────────────────────────────────────
export async function listBids(username: string, params?: { plan_id?: string; task_id?: string }): Promise<Bid[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/bids`, { params })
  return Array.isArray(data) ? data : data.bids ?? []
}

export async function getBidStats(username: string): Promise<unknown> {
  const { data } = await apiClient.get(`/graph-store/${username}/bids/stats`)
  return data
}

// ── Ethics ──────────────────────────────────────────────────────────
export async function listEthicsPolicies(username: string): Promise<EthicsPolicy[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/ethics-policies`)
  return Array.isArray(data) ? data : data.policies ?? []
}

export async function getActiveEthicsPolicy(username: string): Promise<EthicsPolicy | null> {
  try {
    const { data } = await apiClient.get(`/graph-store/${username}/ethics-policies/active`)
    return data
  } catch {
    return null
  }
}

export async function listEthicsChecks(username: string): Promise<EthicsCheck[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/ethics-checks`)
  return Array.isArray(data) ? data : data.checks ?? []
}

// ── Audit ───────────────────────────────────────────────────────────
export async function listAuditLogs(username: string, params?: { offset?: number; limit?: number }): Promise<AuditLog[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/audit-logs`, { params })
  return Array.isArray(data) ? data : data.logs ?? []
}

export async function listEscalations(username: string): Promise<Escalation[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/escalations`)
  return Array.isArray(data) ? data : data.escalations ?? []
}

// ── Human Approvals ─────────────────────────────────────────────────
export async function listHumanApprovals(username: string): Promise<HumanApproval[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/human-approvals`)
  return Array.isArray(data) ? data : data.approvals ?? []
}

// ── Execution Reports ───────────────────────────────────────────────
export async function listExecutionReports(username: string): Promise<ExecutionReport[]> {
  const { data } = await apiClient.get(`/graph-store/${username}/execution-reports`)
  return Array.isArray(data) ? data : data.reports ?? []
}

// ── Stats ───────────────────────────────────────────────────────────
export async function getGraphStoreStats(username: string): Promise<GraphStoreStat> {
  const { data } = await apiClient.get(`/graph-store/${username}/stats`)
  return data
}
