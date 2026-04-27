// ── Shared API types ─────────────────────────────────────────────────

export interface HealthResponse {
  status: string
}

export interface ReadyResponse {
  status: string
  checks: Record<string, string>
}

export interface PipelineConfig {
  pipeline_id: string
  name?: string
  description?: string
  payload?: Record<string, unknown>
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export interface PipelineRun {
  run_id: string
  pipeline_id: string
  status: string
  started_at?: string
  finished_at?: string
  tasks?: TaskResult[]
  [key: string]: unknown
}

export interface TaskResult {
  task_id: string
  status: string
  agent_id?: string
  started_at?: string
  finished_at?: string
  result?: unknown
  error?: string
}

export interface Agent {
  agent_id: string
  service_name: string
  status: string
  capabilities: string[]
  tags?: string[]
  metadata?: Record<string, unknown>
  last_heartbeat?: string
  registered_at?: string
  ttl?: number
}

export interface Template {
  template_id: string
  name: string
  description?: string
  version?: string
  capabilities?: string[]
  config?: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

export interface PlanDraft {
  plan_id: string
  status?: string
  specs?: TaskSpec[]
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export interface TaskSpec {
  task_id: string
  description?: string
  required_capabilities?: string[]
  dependencies?: string[]
  [key: string]: unknown
}

export interface Bid {
  bid_id?: string
  task_id: string
  plan_id: string
  agent_id: string
  score?: number
  capabilities?: string[]
  [key: string]: unknown
}

export interface CfpEntry {
  cfp_id?: string
  task_id: string
  plan_id: string
  required_capabilities?: string[]
  [key: string]: unknown
}

export interface ContractAward {
  task_id: string
  plan_id: string
  agent_id: string
  [key: string]: unknown
}

export interface CnpCycle {
  cycle_id: string
  status: string
  created_at?: string
  [key: string]: unknown
}

export interface CnpStats {
  total_cfps?: number
  total_bids?: number
  total_awards?: number
  [key: string]: unknown
}

export interface GraphStoreStat {
  [key: string]: unknown
}

export interface EthicsPolicy {
  policy_id: string
  name: string
  rules?: unknown[]
  active?: boolean
  [key: string]: unknown
}

export interface EthicsCheck {
  check_id?: string
  plan_id?: string
  result: string
  [key: string]: unknown
}

export interface AuditLog {
  log_id?: string
  action: string
  actor?: string
  timestamp?: string
  details?: Record<string, unknown>
}

export interface Escalation {
  escalation_id?: string
  reason: string
  status?: string
  [key: string]: unknown
}

export interface ServiceRegistration {
  service_id: string
  name: string
  version?: string
  status?: string
  [key: string]: unknown
}

export interface SemanticStatus {
  fuseki_enabled: boolean
  fuseki_url?: string
  ontology_version?: string
  shapes_version?: string
}

export interface GraphSummary {
  total_triples?: number
  [key: string]: unknown
}

export interface DomainEntity {
  name: string
  type: string
  description?: string
  [key: string]: unknown
}

export interface PlanRecommendation {
  similar_plans: SimilarPlan[]
  capability_stats: Record<string, CapabilityStat>
  recommended_templates: RecommendedTemplate[]
}

export interface SimilarPlan {
  plan_id: string
  shared_capabilities: number
  execution_summary?: {
    total: number
    successes: number
    avg_duration?: number
  }
}

export interface CapabilityStat {
  avg_duration: number
  sample_count: number
}

export interface RecommendedTemplate {
  template_id: string
  template_name?: string
  success_rate: number
}

export interface PlanSuggestion {
  suggestion_id: string
  plan_id?: string
  type?: string
  status?: string
  [key: string]: unknown
}

export interface HumanApproval {
  approval_id?: string
  plan_id?: string
  decision?: string
  [key: string]: unknown
}

export interface ExecutionReport {
  report_id?: string
  pipeline_run_id?: string
  [key: string]: unknown
}

export interface PaginatedParams {
  offset?: number
  limit?: number
}

// ── Agent Registration ────────────────────────────────────────────────
export type AgentStatus = 'online' | 'idle' | 'busy' | 'offline' | 'unhealthy'

export interface AgentRegistrationPayload {
  agent_id: string
  service_name: string
  owner?: string
  description?: string
  capabilities: string[]
  tags: string[]
  status: AgentStatus
  metadata: Record<string, unknown>
  policy: Record<string, unknown>
  registered_at: string
  last_heartbeat: string
  expires_at?: string | null
}

// ── CNP Cycle Request ─────────────────────────────────────────────────
export interface RunCNPCycleRequest {
  pipeline_id: string
  pipeline_run_id?: string
  task_ids: string[]
  max_rounds?: number
  bid_timeout_seconds?: number
}

// ── Task Spec Creation ────────────────────────────────────────────────
export type RiskLevel = 'low' | 'medium' | 'high'

export interface TaskSpecCreate {
  task_id: string
  username: string
  description: string
  required_capabilities: string[]
  tags: string[]
  risk_level: RiskLevel
  requires_human_approval: boolean
  metadata: Record<string, unknown>
  created_at: string
}

// ── Wizard state ──────────────────────────────────────────────────────
export interface WizardTaskSpec {
  task_id: string
  description: string
  required_capabilities: string[]
  tags: string[]
  risk_level: RiskLevel
  requires_human_approval: boolean
}
