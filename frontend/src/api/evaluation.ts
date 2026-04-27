import apiClient from './client'

// ── E4: Ethics Detection ─────────────────────────────────────────────

export interface E4DatasetSummary {
  total: number
  violation_cases: number
  clean_cases: number
  by_category: Record<string, number>
}

export interface E4TestCase {
  case_id: string
  category: string
  expected_detected: boolean
  expected_severity: string | null
  description: string
  task_description: string
  risk_level: string
}

export interface E4CategoryMetrics {
  tp: number
  fp: number
  fn: number
  tn: number
  precision: number
  recall: number
  f1: number
}

export interface E4DetailedResult {
  case_id: string
  expected: boolean
  predicted: boolean
  correct: boolean
  category: string
  severity: string | null
  violations: Record<string, unknown>[]
}

export interface E4StubResponse {
  mode: string
  total_cases: number
  accuracy: number
  overall: E4CategoryMetrics
  per_category: Record<string, E4CategoryMetrics>
  detailed_results: E4DetailedResult[]
}

export async function getE4Dataset(): Promise<{ summary: E4DatasetSummary; cases: E4TestCase[] }> {
  const { data } = await apiClient.get('/api/evaluation/e4/dataset')
  return data
}

export async function runE4Stub(): Promise<E4StubResponse> {
  const { data } = await apiClient.get('/api/evaluation/e4/stub')
  return data
}

// ── E1: Agent Quality ────────────────────────────────────────────────

export interface E1GroundTruth {
  deals: Record<string, unknown>
  clients: Record<string, unknown>
  warehouses: Record<string, string>
  agents: Record<string, {
    count: number
    cases: Record<string, unknown>[]
  }>
}

export interface E1ScoreEmailRequest {
  deal_id: number
  output: Record<string, unknown>
}

export interface E1ScoreResult {
  deal_id: number
  scores: Record<string, unknown>
}

export async function getE1GroundTruth(): Promise<E1GroundTruth> {
  const { data } = await apiClient.get('/api/evaluation/e1/ground-truth')
  return data
}

export async function scoreEmail(req: E1ScoreEmailRequest): Promise<E1ScoreResult> {
  const { data } = await apiClient.post('/api/evaluation/e1/score-email', req)
  return data
}

// ── E3: Ablation Study ───────────────────────────────────────────────

export interface E3Config {
  description: string
  env_vars: Record<string, string>
}

export interface E3AblationRequest {
  configs: string[]
  deal_ids: number[]
  inject_violations: boolean
  inject_failures: boolean
  failure_rate: number
}

export interface E3ConfigSummary {
  description: string
  n_deals: number
  mean_latency_ms: number
  total_ethics_violations: number
  mean_violations_per_deal: number
  mean_corrections: number
  mean_recovery_rate: number | null
  quality_per_agent: Record<string, Record<string, number>>
}

export interface E3AblationResponse {
  configs_tested: string[]
  deals_per_config: number
  summary: Record<string, E3ConfigSummary>
  detailed: Record<string, Record<string, unknown>[]>
}

export async function getE3Configs(): Promise<Record<string, E3Config>> {
  const { data } = await apiClient.get('/api/evaluation/e3/configs')
  return data
}

export async function runE3Ablation(req: E3AblationRequest): Promise<E3AblationResponse> {
  const { data } = await apiClient.post('/api/evaluation/e3/run', req, { timeout: 300000 })
  return data
}

export async function getE3GroundTruth(): Promise<E3GroundTruthResponse> {
  const { data } = await apiClient.get('/api/evaluation/e3/ground-truth')
  return data
}

// ── Compliance Agent (standalone) ────────────────────────────────────

export interface ComplianceViolation {
  client: string
  warehouse: string
  rule: string
  severity: string
  detail: string
}

export interface SeededScenarioResult {
  scenario_id: string
  client: string
  warehouse: string
  should_block: boolean
  actually_blocked: boolean
  violations_found: ComplianceViolation[]
  correct: boolean
}

export interface ComplianceResponse {
  checked_pairs: number
  total_violations: number
  blocks: number
  warnings: number
  ground_truth_violations: number
  ground_truth_detected: number
  ground_truth_detection_rate: number
  violations: ComplianceViolation[]
  seeded_scenario_results: SeededScenarioResult[]
}

export interface E3GroundTruthResponse {
  assignments: {
    client_id: number
    client_name: string
    correct_warehouse_agent: string
    correct_estimator: string
    warehouse_rationale: string
    estimator_rationale: string
    priority: string
  }[]
  known_violations: {
    client_name: string
    warehouse_name: string
    rule: string
    severity: string
    detail: string
  }[]
  seeded_scenarios: {
    scenario_id: string
    client_name: string
    warehouse_name: string
    should_block: boolean
    expected_rules: string[]
    description: string
  }[]
  hypotheses: Record<string, string>
}

export async function runComplianceTest(): Promise<ComplianceResponse> {
  const { data } = await apiClient.get('/api/evaluation/compliance/run')
  return data
}

// ── Pipeline Launch (C0 Ablation Scenarios) ─────────────────────────────

export interface PipelineClientInfo {
  key: string
  pipeline_name: string
  description: string
  tasks: { id: string; name: string; service: string }[]
}

export interface PipelineLaunchResult {
  client: string
  pipeline_id?: string
  run_id?: string
  status: string
  pipeline_name?: string
  error?: string
}

export async function getPipelineClients(): Promise<{ clients: PipelineClientInfo[] }> {
  const { data } = await apiClient.get('/api/evaluation/pipeline/clients')
  return data
}

export async function launchPipeline(client: string, username = 'default'): Promise<PipelineLaunchResult> {
  const { data } = await apiClient.post('/api/evaluation/pipeline/launch', { client, username }, { timeout: 30000 })
  return data
}

export async function launchAllPipelines(username = 'default'): Promise<{ results: PipelineLaunchResult[] }> {
  const { data } = await apiClient.post('/api/evaluation/pipeline/launch-all', { username }, { timeout: 120000 })
  return data
}
