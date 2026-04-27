<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { listAgents } from '@/api/agents'
import { createTaskSpec } from '@/api/graphStore'
import { createPipeline, startPipelineRun, listRuns, getRun, getTaskOutput } from '@/api/pipelines'
import { triggerCnpCycle, getCnpCycle, listCnpCycles } from '@/api/cnp'
import StatusBadge from '@/components/StatusBadge.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import type { Agent, WizardTaskSpec, RiskLevel } from '@/api/types'

const router = useRouter()
const auth = useAuthStore()

// ── Wizard state ──────────────────────────────────────────────────────
const currentStep = ref(1)
const totalSteps = 5

const stepLabels = [
  'Describe Work',
  'Define Tasks',
  'Select Agents',
  'Trigger CNP',
  'Monitor Results',
]

// Step 1: Natural language description
const nlDescription = ref('')

// Step 2: Task specs
const taskSpecs = ref<WizardTaskSpec[]>([])
const showAddTask = ref(false)

// Step 3: Agent selection
const availableAgents = ref<Agent[]>([])
const agentsLoading = ref(false)

// Step 4: CNP config & trigger
const maxRounds = ref(3)
const bidTimeout = ref(300)
const triggering = ref(false)
const triggerError = ref('')

// Step 5: Monitoring
const pipelineId = ref('')
const cycleId = ref('')
const cycleStatus = ref<Record<string, unknown> | null>(null)
const monitorInterval = ref<ReturnType<typeof setInterval> | null>(null)
const monitorError = ref('')

// ── Task editing form ─────────────────────────────────────────────────
const editingTask = ref<WizardTaskSpec | null>(null)
const editForm = ref({
  task_id: '',
  description: '',
  capabilities: [] as string[],
  capInput: '',
  tags: [] as string[],
  tagInput: '',
  risk_level: 'medium' as RiskLevel,
  requires_human_approval: false,
})

const suggestedCapabilities = [
  'transcription', 'call_analysis', 'extraction',
  'deal_summary', 'aggregation', 'email_analysis',
  'form_filling', 'client_analysis', 'requirements',
  'proposal_generation', 'deal_proposal', 'template',
  'warehouse_matching', 'recommendation', 'location_analysis',
]

// ── Capability → business-domain agent service name mapping ──────────
const capToService: Record<string, string> = {
  // Call Transcription Agent
  transcription: 'call-transcription', call_analysis: 'call-transcription',
  extraction: 'call-transcription', nlp: 'call-transcription',
  voice: 'call-transcription', call: 'call-transcription',
  // Deal Summary Agent
  deal_summary: 'deal-summary', aggregation: 'deal-summary',
  email_analysis: 'deal-summary', synthesis: 'deal-summary',
  analytics: 'deal-summary', reporting: 'deal-summary',
  // Client Necessity Form Agent
  form_filling: 'client-necessity-form', client_analysis: 'client-necessity-form',
  requirements: 'client-necessity-form', validation: 'client-necessity-form',
  data_processing: 'client-necessity-form',
  // Proposal Template Agent
  proposal_generation: 'proposal-template', deal_proposal: 'proposal-template',
  template: 'proposal-template', document_generation: 'proposal-template',
  // Warehouse Suggestion Agent
  warehouse_matching: 'warehouse-suggestion', recommendation: 'warehouse-suggestion',
  location_analysis: 'warehouse-suggestion', warehouse: 'warehouse-suggestion',
  storage: 'warehouse-suggestion', infrastructure: 'warehouse-suggestion',
}

function resolveServiceName(capabilities: string[]): string {
  for (const cap of capabilities) {
    const svc = capToService[cap]
    if (svc) return svc
  }
  return capabilities.join(',') || 'any'
}

// ── Pipeline execution state ─────────────────────────────────────────
const executionPhase = ref(false)
const executionRunId = ref('')
const executionData = ref<Record<string, unknown> | null>(null)
const executionPolling = ref<ReturnType<typeof setInterval> | null>(null)
const executionError = ref('')
const startingExecution = ref(false)
const taskOutputs = ref<Record<string, string>>({})

// ── Computed ──────────────────────────────────────────────────────────
const canProceed = computed(() => {
  switch (currentStep.value) {
    case 1: return nlDescription.value.trim().length > 10
    case 2: return taskSpecs.value.length > 0
    case 3: return availableAgents.value.length > 0
    case 4: return !triggering.value
    default: return true
  }
})

const capabilityMatchMap = computed(() => {
  const map: Record<string, string[]> = {}
  for (const spec of taskSpecs.value) {
    map[spec.task_id] = availableAgents.value
      .filter(a => spec.required_capabilities.some(c => a.capabilities.includes(c)))
      .map(a => a.agent_id)
  }
  return map
})

const allTasksCovered = computed(() =>
  taskSpecs.value.every(spec => (capabilityMatchMap.value[spec.task_id]?.length ?? 0) > 0),
)

// ── Step 1 → 2: Parse NL into task spec stubs ────────────────────────
function parseDescription() {
  const text = nlDescription.value.trim()
  const sentences = text
    .split(/[.\n]+/)
    .map(s => s.trim())
    .filter(s => s.length > 5)

  taskSpecs.value = sentences.map((sentence, idx) => {
    const id = `task_${Date.now().toString(36)}_${idx}`
    const caps = inferCapabilities(sentence)
    return {
      task_id: id,
      description: sentence,
      required_capabilities: caps,
      tags: [],
      risk_level: 'medium' as RiskLevel,
      requires_human_approval: false,
    }
  })

  if (taskSpecs.value.length === 0) {
    taskSpecs.value.push({
      task_id: `task_${Date.now().toString(36)}_0`,
      description: text,
      required_capabilities: [],
      tags: [],
      risk_level: 'medium',
      requires_human_approval: false,
    })
  }

  currentStep.value = 2
}

function inferCapabilities(text: string): string[] {
  const lower = text.toLowerCase()
  const caps: string[] = []
  const patterns: [string, string[]][] = [
    // Call Transcription Agent
    ['transcription', ['transcri', 'call', 'phone', 'voice', 'record', 'conversation']],
    ['call_analysis', ['call analy', 'extract info', 'key information']],
    // Deal Summary Agent
    ['deal_summary', ['deal', 'summary', 'summar', 'aggregate', 'consolidat']],
    ['email_analysis', ['email', 'mail', 'correspondence', 'hubspot', 'crm']],
    // Client Necessity Form Agent
    ['form_filling', ['form', 'necessit', 'requirement', 'client need', 'fill']],
    ['client_analysis', ['client', 'customer', 'profile', 'company']],
    // Proposal Template Agent
    ['proposal_generation', ['proposal', 'offer', 'template', 'quote', 'bid']],
    ['deal_proposal', ['deal proposal', 'generate proposal', 'create proposal']],
    // Warehouse Suggestion Agent
    ['warehouse_matching', ['warehouse', 'suggest', 'recommend', 'match', 'space']],
    ['location_analysis', ['location', 'region', 'area', 'proximity', 'logistics']],
  ]
  for (const [cap, keywords] of patterns) {
    if (keywords.some(kw => lower.includes(kw))) {
      caps.push(cap)
    }
  }
  return caps
}

// ── Step 2: Task spec management ─────────────────────────────────────
function openAddTask() {
  editingTask.value = null
  editForm.value = {
    task_id: `task_${Date.now().toString(36)}_${taskSpecs.value.length}`,
    description: '',
    capabilities: [],
    capInput: '',
    tags: [],
    tagInput: '',
    risk_level: 'medium',
    requires_human_approval: false,
  }
  showAddTask.value = true
}

function openEditTask(spec: WizardTaskSpec) {
  editingTask.value = spec
  editForm.value = {
    task_id: spec.task_id,
    description: spec.description,
    capabilities: [...spec.required_capabilities],
    capInput: '',
    tags: [...spec.tags],
    tagInput: '',
    risk_level: spec.risk_level,
    requires_human_approval: spec.requires_human_approval,
  }
  showAddTask.value = true
}

function saveTask() {
  const spec: WizardTaskSpec = {
    task_id: editForm.value.task_id,
    description: editForm.value.description,
    required_capabilities: editForm.value.capabilities,
    tags: editForm.value.tags,
    risk_level: editForm.value.risk_level,
    requires_human_approval: editForm.value.requires_human_approval,
  }
  if (editingTask.value) {
    const idx = taskSpecs.value.findIndex(t => t.task_id === editingTask.value!.task_id)
    if (idx >= 0) taskSpecs.value[idx] = spec
  } else {
    taskSpecs.value.push(spec)
  }
  showAddTask.value = false
}

function removeTask(taskId: string) {
  taskSpecs.value = taskSpecs.value.filter(t => t.task_id !== taskId)
}

function addFormCap(cap?: string) {
  const value = (cap ?? editForm.value.capInput).trim().toLowerCase()
  if (value && !editForm.value.capabilities.includes(value)) {
    editForm.value.capabilities.push(value)
  }
  editForm.value.capInput = ''
}

function removeFormCap(cap: string) {
  editForm.value.capabilities = editForm.value.capabilities.filter(c => c !== cap)
}

function addFormTag() {
  const value = editForm.value.tagInput.trim().toLowerCase()
  if (value && !editForm.value.tags.includes(value)) {
    editForm.value.tags.push(value)
  }
  editForm.value.tagInput = ''
}

function removeFormTag(t: string) {
  editForm.value.tags = editForm.value.tags.filter(v => v !== t)
}

// ── Step 3: Load agents ──────────────────────────────────────────────
async function loadAgents() {
  agentsLoading.value = true
  try {
    availableAgents.value = await listAgents(auth.username)
  } finally {
    agentsLoading.value = false
  }
}

watch(currentStep, (step) => {
  if (step === 3) loadAgents()
})

// ── Step 4: Create specs, pipeline, trigger CNP ──────────────────────
async function triggerCnp() {
  triggering.value = true
  triggerError.value = ''
  try {
    // 1. Create task specs via graph store
    const taskIds: string[] = []
    for (const spec of taskSpecs.value) {
      const now = new Date().toISOString()
      await createTaskSpec(auth.username, {
        task_id: spec.task_id,
        username: auth.username,
        description: spec.description,
        required_capabilities: spec.required_capabilities,
        tags: spec.tags,
        risk_level: spec.risk_level,
        requires_human_approval: spec.requires_human_approval,
        metadata: { source: 'wizard' },
        created_at: now,
      })
      taskIds.push(spec.task_id)
    }

    // 2. Create a lightweight pipeline container
    const pid = `wizard_${Date.now().toString(36)}`
    const lastIdx = taskSpecs.value.length - 1
    const pipelineResult = await createPipeline(auth.username, {
      vhost: auth.username,
      name: pid,
      pipeline_name: `Wizard: ${nlDescription.value.substring(0, 60)}`,
      description: nlDescription.value,
      dag: {
        tasks: taskSpecs.value.map((spec, idx) => ({
          id: spec.task_id,
          name: spec.description.substring(0, 60),
          available_services: resolveServiceName(spec.required_capabilities),
          action: 'process',
          is_entry: idx === 0,
          description: spec.description,
          // Chain tasks linearly: each task depends on the previous
          inputs: idx > 0
            ? { dependencies: [taskSpecs.value[idx - 1].task_id] }
            : {},
          is_final_task: idx === lastIdx,
        })),
      },
      error_handling: {
        retry_policy: { max_retries: 2, backoff_strategy: 'exponential' },
        on_failure: 'stop',
      },
    }) as { pipeline_id: string }
    // Use the UUID returned by the API, not the local name
    pipelineId.value = pipelineResult.pipeline_id

    // 3. Trigger CNP cycle (returns request_id, not cycle_id — we discover cycle_id via polling)
    await triggerCnpCycle(auth.username, {
      pipeline_id: pipelineResult.pipeline_id,
      task_ids: taskIds,
      max_rounds: maxRounds.value,
      bid_timeout_seconds: bidTimeout.value,
    })

    // cycle_id will be discovered by pollCycle via listCnpCycles
    cycleId.value = ''

    currentStep.value = 5
    startMonitoring()
  } catch (e: unknown) {
    triggerError.value = e instanceof Error ? e.message : 'Failed to trigger CNP cycle'
  } finally {
    triggering.value = false
  }
}

// ── Step 5: Monitor cycle ────────────────────────────────────────────
async function pollCycle() {
  try {
    // If we don't have a cycle_id yet, discover it by listing cycles for our pipeline
    if (!cycleId.value && pipelineId.value) {
      const cycles = await listCnpCycles(auth.username, {
        pipeline_id: pipelineId.value,
      } as Record<string, unknown>)
      if (cycles.length > 0) {
        const latest = cycles[cycles.length - 1] as Record<string, unknown>
        cycleId.value = String(latest.cycle_id ?? '')
      }
    }
    if (!cycleId.value) return
    const result = await getCnpCycle(auth.username, cycleId.value)
    cycleStatus.value = result as Record<string, unknown>
    const s = String(result.status ?? '')
    if (s === 'completed' || s === 'failed') {
      stopMonitoring()
    }
  } catch {
    // Cycle may not be created yet, retry
  }
}

function startMonitoring() {
  pollCycle()
  monitorInterval.value = setInterval(pollCycle, 3000)
}

function stopMonitoring() {
  if (monitorInterval.value) {
    clearInterval(monitorInterval.value)
    monitorInterval.value = null
  }
}

function goToStep(step: number) {
  if (step < currentStep.value) {
    if (step < 5) stopMonitoring()
    currentStep.value = step
  }
}

function viewPlanDrafts() {
  router.push({ name: 'plan-drafts' })
}

function viewCnpMonitor() {
  router.push({ name: 'cnp-overview' })
}

// ── Pipeline execution ────────────────────────────────────────────────
async function executePlan() {
  startingExecution.value = true
  executionError.value = ''
  try {
    await startPipelineRun(auth.username, pipelineId.value)
    executionPhase.value = true
    startExecutionPolling()
  } catch (e: unknown) {
    executionError.value = e instanceof Error ? e.message : 'Failed to start pipeline run'
  } finally {
    startingExecution.value = false
  }
}

async function pollExecution() {
  try {
    // Discover run ID if we don't have one yet
    if (!executionRunId.value && pipelineId.value) {
      const runs = await listRuns(auth.username, pipelineId.value)
      if (runs.length > 0) {
        executionRunId.value = runs[runs.length - 1]
      }
    }
    if (!executionRunId.value) return

    const run = await getRun(auth.username, pipelineId.value, executionRunId.value)
    executionData.value = run as unknown as Record<string, unknown>

    // Fetch actual LLM output for newly completed tasks
    if (run.tasks) {
      for (const [taskId, task] of Object.entries(run.tasks as unknown as Record<string, Record<string, unknown>>)) {
        if (String(task.status) === 'COMPLETED' && !taskOutputs.value[taskId]) {
          try {
            const output = await getTaskOutput(auth.username, pipelineId.value, executionRunId.value, taskId)
            taskOutputs.value[taskId] = typeof output.raw_output === 'string'
              ? output.raw_output
              : JSON.stringify(output, null, 2)
          } catch {
            // Output not available yet — will retry next poll
          }
        }
      }
    }

    const s = String(run.status ?? '')
    if (s === 'completed' || s === 'failed') {
      stopExecutionPolling()
    }
  } catch {
    // Run may not be created yet
  }
}

function startExecutionPolling() {
  pollExecution()
  executionPolling.value = setInterval(pollExecution, 3000)
}

function stopExecutionPolling() {
  if (executionPolling.value) {
    clearInterval(executionPolling.value)
    executionPolling.value = null
  }
}

function startOver() {
  stopMonitoring()
  stopExecutionPolling()
  nlDescription.value = ''
  taskSpecs.value = []
  pipelineId.value = ''
  cycleId.value = ''
  cycleStatus.value = null
  triggerError.value = ''
  monitorError.value = ''
  executionPhase.value = false
  executionRunId.value = ''
  executionData.value = null
  executionError.value = ''
  taskOutputs.value = {}
  currentStep.value = 1
}
</script>

<template>
  <div class="max-w-4xl">
    <h1 class="text-2xl font-bold mb-2">Work Submission Wizard</h1>
    <p class="text-sm text-muted-foreground mb-6">
      Describe what you need done, define task specs, select agents, and let CNP handle the rest.
    </p>

    <!-- Step indicator -->
    <div class="flex items-center gap-1 mb-8">
      <template v-for="(label, idx) in stepLabels" :key="idx">
        <button
          class="flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium transition-colors"
          :class="[
            idx + 1 === currentStep
              ? 'bg-primary text-primary-foreground'
              : idx + 1 < currentStep
                ? 'bg-primary/20 text-primary cursor-pointer hover:bg-primary/30'
                : 'bg-secondary text-muted-foreground',
          ]"
          :disabled="idx + 1 >= currentStep"
          @click="goToStep(idx + 1)"
        >
          <span class="flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold"
            :class="idx + 1 <= currentStep ? 'bg-primary-foreground text-primary' : 'bg-muted-foreground/30 text-muted-foreground'"
          >
            <template v-if="idx + 1 < currentStep">&#10003;</template>
            <template v-else>{{ idx + 1 }}</template>
          </span>
          <span class="hidden sm:inline">{{ label }}</span>
        </button>
        <div v-if="idx < stepLabels.length - 1" class="h-px w-4 bg-border" />
      </template>
    </div>

    <!-- ── Step 1: Describe Work ──────────────────────────────────────── -->
    <div v-if="currentStep === 1">
      <div class="rounded-lg border border-border bg-card p-6">
        <h2 class="text-lg font-semibold mb-2">Describe Your Work</h2>
        <p class="text-sm text-muted-foreground mb-4">
          Write a natural language description of what you need done. Each sentence will become a task spec.
          Be specific about the steps — mention data processing, validation, notifications, etc.
        </p>
        <textarea
          v-model="nlDescription"
          rows="8"
          placeholder="Example: Scrape product data from the supplier website. Validate the data against our product schema and flag any anomalies. Transform prices to USD using the latest exchange rates. Store the cleaned data in the warehouse. Send a summary report to the operations team via email."
          class="w-full rounded-md border border-input bg-background px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring resize-none"
        />
        <div class="flex items-center justify-between mt-4">
          <p class="text-xs text-muted-foreground">
            {{ nlDescription.trim().split(/[.\n]+/).filter(s => s.trim().length > 5).length }} task(s) detected
          </p>
          <button
            :disabled="!canProceed"
            class="rounded-md bg-primary px-6 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            @click="parseDescription"
          >
            Continue &rarr;
          </button>
        </div>
      </div>
    </div>

    <!-- ── Step 2: Define Task Specs ──────────────────────────────────── -->
    <div v-else-if="currentStep === 2">
      <div class="rounded-lg border border-border bg-card p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="text-lg font-semibold">Review Task Specs</h2>
            <p class="text-sm text-muted-foreground">
              Edit, add, or remove tasks. Each task will be broadcast as a Call For Proposals to matching agents.
            </p>
          </div>
          <button
            class="rounded-md bg-secondary px-3 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80"
            @click="openAddTask"
          >
            + Add Task
          </button>
        </div>

        <div class="space-y-3">
          <div
            v-for="spec in taskSpecs"
            :key="spec.task_id"
            class="rounded-lg border border-border p-4 hover:border-primary/30 transition-colors"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 mb-1">
                  <span class="font-mono text-xs text-muted-foreground">{{ spec.task_id }}</span>
                  <span
                    class="rounded px-1.5 py-0.5 text-[10px] font-medium uppercase"
                    :class="{
                      'bg-green-100 text-green-800': spec.risk_level === 'low',
                      'bg-yellow-100 text-yellow-800': spec.risk_level === 'medium',
                      'bg-red-100 text-red-800': spec.risk_level === 'high',
                    }"
                  >
                    {{ spec.risk_level }}
                  </span>
                  <span v-if="spec.requires_human_approval" class="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-800">
                    Approval Required
                  </span>
                </div>
                <p class="text-sm">{{ spec.description }}</p>
                <div class="flex flex-wrap gap-1 mt-2" v-if="spec.required_capabilities.length">
                  <span
                    v-for="cap in spec.required_capabilities"
                    :key="cap"
                    class="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary"
                  >
                    {{ cap }}
                  </span>
                </div>
                <p v-else class="text-xs text-muted-foreground mt-2">No capabilities specified</p>
              </div>
              <div class="flex gap-1 shrink-0">
                <button
                  class="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                  @click="openEditTask(spec)"
                >
                  Edit
                </button>
                <button
                  class="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                  @click="removeTask(spec.task_id)"
                >
                  Remove
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- Task edit dialog -->
        <div v-if="showAddTask" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div class="w-full max-w-lg rounded-lg border border-border bg-card p-6 shadow-xl max-h-[80vh] overflow-y-auto">
            <h3 class="text-lg font-semibold mb-4">
              {{ editingTask ? 'Edit Task' : 'Add Task' }}
            </h3>
            <div class="space-y-4">
              <div>
                <label class="block text-sm font-medium mb-1">Task ID</label>
                <input
                  v-model="editForm.task_id"
                  class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  :disabled="!!editingTask"
                />
              </div>
              <div>
                <label class="block text-sm font-medium mb-1">Description</label>
                <textarea
                  v-model="editForm.description"
                  rows="3"
                  class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring resize-none"
                />
              </div>
              <div>
                <label class="block text-sm font-medium mb-1">Required Capabilities</label>
                <div class="flex flex-wrap gap-1 mb-2" v-if="editForm.capabilities.length">
                  <span
                    v-for="cap in editForm.capabilities"
                    :key="cap"
                    class="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary"
                  >
                    {{ cap }}
                    <button type="button" @click="removeFormCap(cap)">&times;</button>
                  </span>
                </div>
                <div class="flex gap-2">
                  <input
                    v-model="editForm.capInput"
                    placeholder="Add capability..."
                    class="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                    @keydown.enter.prevent="addFormCap()"
                  />
                </div>
                <div class="flex flex-wrap gap-1 mt-2">
                  <button
                    v-for="s in suggestedCapabilities.filter(c => !editForm.capabilities.includes(c))"
                    :key="s"
                    type="button"
                    class="rounded bg-secondary px-2 py-0.5 text-[10px] text-secondary-foreground hover:bg-secondary/80"
                    @click="addFormCap(s)"
                  >
                    + {{ s }}
                  </button>
                </div>
              </div>
              <div>
                <label class="block text-sm font-medium mb-1">Tags</label>
                <div class="flex flex-wrap gap-1 mb-2" v-if="editForm.tags.length">
                  <span
                    v-for="t in editForm.tags"
                    :key="t"
                    class="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-xs"
                  >
                    {{ t }}
                    <button type="button" @click="removeFormTag(t)">&times;</button>
                  </span>
                </div>
                <input
                  v-model="editForm.tagInput"
                  placeholder="Add tag..."
                  class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  @keydown.enter.prevent="addFormTag()"
                />
              </div>
              <div class="grid grid-cols-2 gap-4">
                <div>
                  <label class="block text-sm font-medium mb-1">Risk Level</label>
                  <select
                    v-model="editForm.risk_level"
                    class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </div>
                <div>
                  <label class="block text-sm font-medium mb-1">Human Approval</label>
                  <label class="flex items-center gap-2 mt-2 cursor-pointer">
                    <input type="checkbox" v-model="editForm.requires_human_approval" class="rounded" />
                    <span class="text-sm">Require approval</span>
                  </label>
                </div>
              </div>
            </div>
            <div class="flex justify-end gap-3 mt-6">
              <button
                class="rounded-md border border-input px-4 py-2 text-sm hover:bg-accent"
                @click="showAddTask = false"
              >
                Cancel
              </button>
              <button
                :disabled="!editForm.description.trim()"
                class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                @click="saveTask"
              >
                {{ editingTask ? 'Save Changes' : 'Add Task' }}
              </button>
            </div>
          </div>
        </div>

        <div class="flex justify-between mt-6">
          <button
            class="rounded-md border border-input px-4 py-2 text-sm hover:bg-accent"
            @click="currentStep = 1"
          >
            &larr; Back
          </button>
          <button
            :disabled="!canProceed"
            class="rounded-md bg-primary px-6 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            @click="currentStep = 3"
          >
            Continue &rarr;
          </button>
        </div>
      </div>
    </div>

    <!-- ── Step 3: Select Agents ──────────────────────────────────────── -->
    <div v-else-if="currentStep === 3">
      <div class="rounded-lg border border-border bg-card p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="text-lg font-semibold">Available Agents</h2>
            <p class="text-sm text-muted-foreground">
              Review registered agents and their capability coverage for your tasks.
            </p>
          </div>
          <div class="flex gap-2">
            <button
              class="rounded-md bg-secondary px-3 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80"
              @click="loadAgents"
            >
              Refresh
            </button>
            <router-link
              to="/agents/register"
              class="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              + Register Agent
            </router-link>
          </div>
        </div>

        <div v-if="agentsLoading" class="flex items-center gap-2 text-muted-foreground py-8 justify-center">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          Loading agents...
        </div>

        <template v-else>
          <!-- Coverage summary -->
          <div class="mb-4 rounded-lg border p-3" :class="allTasksCovered ? 'border-green-300 bg-green-50' : 'border-yellow-300 bg-yellow-50'">
            <p class="text-sm font-medium" :class="allTasksCovered ? 'text-green-800' : 'text-yellow-800'">
              {{ allTasksCovered ? 'All tasks have matching agents' : 'Some tasks have no matching agents' }}
            </p>
            <div class="mt-2 space-y-1">
              <div v-for="spec in taskSpecs" :key="spec.task_id" class="flex items-center gap-2 text-xs">
                <span :class="(capabilityMatchMap[spec.task_id]?.length ?? 0) > 0 ? 'text-green-700' : 'text-yellow-700'">
                  {{ (capabilityMatchMap[spec.task_id]?.length ?? 0) > 0 ? '&#10003;' : '&#9888;' }}
                </span>
                <span class="font-mono">{{ spec.task_id }}</span>
                <span class="text-muted-foreground">
                  ({{ spec.required_capabilities.join(', ') || 'any' }})
                </span>
                <span class="font-medium">
                  &rarr; {{ capabilityMatchMap[spec.task_id]?.length ?? 0 }} agent(s)
                </span>
              </div>
            </div>
          </div>

          <!-- Agent list -->
          <div v-if="availableAgents.length === 0" class="text-center py-8">
            <p class="text-muted-foreground mb-2">No agents registered yet.</p>
            <router-link
              to="/agents/register"
              class="text-sm text-primary hover:underline"
            >
              Register your first agent &rarr;
            </router-link>
          </div>

          <div v-else class="space-y-2">
            <div
              v-for="agent in availableAgents"
              :key="agent.agent_id"
              class="flex items-center justify-between rounded-lg border border-border p-3"
            >
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <span class="font-medium text-sm">{{ agent.service_name }}</span>
                  <StatusBadge :status="agent.status" />
                </div>
                <span class="text-xs text-muted-foreground font-mono">{{ agent.agent_id }}</span>
                <div class="flex flex-wrap gap-1 mt-1">
                  <span
                    v-for="cap in agent.capabilities"
                    :key="cap"
                    class="rounded-full px-2 py-0.5 text-[10px] font-medium"
                    :class="taskSpecs.some(s => s.required_capabilities.includes(cap))
                      ? 'bg-green-100 text-green-800'
                      : 'bg-secondary text-secondary-foreground'"
                  >
                    {{ cap }}
                  </span>
                </div>
              </div>
              <div class="text-right shrink-0 ml-3">
                <p class="text-xs text-muted-foreground">Matches</p>
                <p class="text-lg font-bold">
                  {{ taskSpecs.filter(s => s.required_capabilities.some(c => agent.capabilities.includes(c))).length }}
                </p>
              </div>
            </div>
          </div>
        </template>

        <div class="flex justify-between mt-6">
          <button
            class="rounded-md border border-input px-4 py-2 text-sm hover:bg-accent"
            @click="currentStep = 2"
          >
            &larr; Back
          </button>
          <button
            :disabled="!canProceed"
            class="rounded-md bg-primary px-6 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            @click="currentStep = 4"
          >
            Continue &rarr;
          </button>
        </div>
      </div>
    </div>

    <!-- ── Step 4: Trigger CNP ────────────────────────────────────────── -->
    <div v-else-if="currentStep === 4">
      <div class="rounded-lg border border-border bg-card p-6">
        <h2 class="text-lg font-semibold mb-2">Trigger Contract Net Protocol</h2>
        <p class="text-sm text-muted-foreground mb-6">
          This will create task specs, a pipeline container, and trigger the CNP bidding cycle.
        </p>

        <!-- Summary -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <div class="rounded-lg border border-border p-4 text-center">
            <p class="text-2xl font-bold">{{ taskSpecs.length }}</p>
            <p class="text-sm text-muted-foreground">Task Specs</p>
          </div>
          <div class="rounded-lg border border-border p-4 text-center">
            <p class="text-2xl font-bold">{{ availableAgents.length }}</p>
            <p class="text-sm text-muted-foreground">Available Agents</p>
          </div>
          <div class="rounded-lg border border-border p-4 text-center">
            <p class="text-2xl font-bold">{{ new Set(taskSpecs.flatMap(s => s.required_capabilities)).size }}</p>
            <p class="text-sm text-muted-foreground">Unique Capabilities</p>
          </div>
        </div>

        <!-- CNP Settings -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <div>
            <label class="block text-sm font-medium mb-1">Max Bidding Rounds</label>
            <input
              v-model.number="maxRounds"
              type="number"
              min="1"
              max="10"
              class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <p class="mt-1 text-xs text-muted-foreground">Number of CNP bidding rounds (1-10)</p>
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">Bid Timeout (seconds)</label>
            <input
              v-model.number="bidTimeout"
              type="number"
              min="10"
              max="3600"
              class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <p class="mt-1 text-xs text-muted-foreground">How long to wait for agent bids</p>
          </div>
        </div>

        <div v-if="triggerError" class="mb-4 rounded-lg border border-destructive bg-destructive/10 p-3 text-sm text-destructive">
          {{ triggerError }}
        </div>

        <div class="flex justify-between">
          <button
            class="rounded-md border border-input px-4 py-2 text-sm hover:bg-accent"
            @click="currentStep = 3"
          >
            &larr; Back
          </button>
          <button
            :disabled="triggering"
            class="rounded-md bg-primary px-8 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            @click="triggerCnp"
          >
            {{ triggering ? 'Creating Tasks & Triggering CNP...' : 'Launch CNP Cycle' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ── Step 5: Monitor Results ────────────────────────────────────── -->
    <div v-else-if="currentStep === 5">
      <!-- Phase A: CNP Cycle Progress -->
      <div v-if="!executionPhase" class="rounded-lg border border-border bg-card p-6 mb-6">
        <h2 class="text-lg font-semibold mb-2">CNP Cycle Progress</h2>
        <p class="text-sm text-muted-foreground mb-4">
          Monitoring the bidding cycle. Agents are evaluating tasks and submitting bids.
        </p>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <div class="rounded-lg border border-border p-4">
            <p class="text-sm text-muted-foreground mb-1">Pipeline</p>
            <p class="font-mono text-xs">{{ pipelineId || '--' }}</p>
          </div>
          <div class="rounded-lg border border-border p-4">
            <p class="text-sm text-muted-foreground mb-1">Cycle ID</p>
            <p class="font-mono text-xs">{{ cycleId || '--' }}</p>
          </div>
          <div class="rounded-lg border border-border p-4">
            <p class="text-sm text-muted-foreground mb-1">Status</p>
            <StatusBadge v-if="cycleStatus?.status" :status="String(cycleStatus.status)" />
            <div v-else class="flex items-center gap-2 text-muted-foreground">
              <div class="h-3 w-3 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span class="text-sm">Waiting...</span>
            </div>
          </div>
        </div>

        <!-- Cycle details -->
        <div v-if="cycleStatus" class="mb-6">
          <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <div class="rounded border border-border p-3 text-center">
              <p class="text-lg font-bold">{{ cycleStatus.current_round ?? 0 }}</p>
              <p class="text-xs text-muted-foreground">Current Round</p>
            </div>
            <div class="rounded border border-border p-3 text-center">
              <p class="text-lg font-bold">{{ cycleStatus.max_rounds ?? maxRounds }}</p>
              <p class="text-xs text-muted-foreground">Max Rounds</p>
            </div>
            <div class="rounded border border-border p-3 text-center">
              <p class="text-lg font-bold">{{ cycleStatus.total_bids ?? 0 }}</p>
              <p class="text-xs text-muted-foreground">Total Bids</p>
            </div>
            <div class="rounded border border-border p-3 text-center">
              <p class="text-lg font-bold font-mono text-xs">{{ cycleStatus.plan_id ?? '--' }}</p>
              <p class="text-xs text-muted-foreground">Plan ID</p>
            </div>
          </div>

          <div v-if="cycleStatus.error" class="rounded-lg border border-destructive bg-destructive/10 p-3 text-sm text-destructive mb-4">
            {{ cycleStatus.error }}
          </div>

          <div class="rounded-lg border border-border p-4">
            <h3 class="font-semibold mb-2 text-sm">Full Cycle Details</h3>
            <JsonViewer :data="cycleStatus" />
          </div>
        </div>

        <div v-if="monitorError" class="mb-4 rounded-lg border border-destructive bg-destructive/10 p-3 text-sm text-destructive">
          {{ monitorError }}
        </div>

        <div v-if="executionError" class="mb-4 rounded-lg border border-destructive bg-destructive/10 p-3 text-sm text-destructive">
          {{ executionError }}
        </div>

        <div class="flex flex-wrap gap-3">
          <!-- Execute Plan button — shown when CNP cycle completed -->
          <button
            v-if="String(cycleStatus?.status) === 'completed'"
            :disabled="startingExecution"
            class="rounded-md bg-green-600 px-6 py-2.5 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
            @click="executePlan"
          >
            {{ startingExecution ? 'Starting Execution...' : 'Execute Plan' }}
          </button>
          <button
            class="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80"
            @click="viewPlanDrafts"
          >
            View Plan Drafts
          </button>
          <button
            class="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80"
            @click="viewCnpMonitor"
          >
            CNP Monitor
          </button>
          <button
            class="rounded-md border border-input px-4 py-2 text-sm font-medium hover:bg-accent"
            @click="startOver"
          >
            Start New Wizard
          </button>
        </div>
      </div>

      <!-- Phase B: Pipeline Execution Monitor -->
      <div v-else class="rounded-lg border border-border bg-card p-6">
        <h2 class="text-lg font-semibold mb-2">Pipeline Execution</h2>
        <p class="text-sm text-muted-foreground mb-4">
          Tasks are being executed by Claude-powered agents. Each agent processes its task and chains the output to the next.
        </p>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <div class="rounded-lg border border-border p-4">
            <p class="text-sm text-muted-foreground mb-1">Pipeline</p>
            <p class="font-mono text-xs">{{ pipelineId || '--' }}</p>
          </div>
          <div class="rounded-lg border border-border p-4">
            <p class="text-sm text-muted-foreground mb-1">Run ID</p>
            <p class="font-mono text-xs">{{ executionRunId || 'discovering...' }}</p>
          </div>
          <div class="rounded-lg border border-border p-4">
            <p class="text-sm text-muted-foreground mb-1">Status</p>
            <StatusBadge v-if="executionData?.status" :status="String(executionData.status)" />
            <div v-else class="flex items-center gap-2 text-muted-foreground">
              <div class="h-3 w-3 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span class="text-sm">Starting...</span>
            </div>
          </div>
        </div>

        <!-- Task-by-task progress -->
        <div v-if="executionData?.tasks" class="space-y-3 mb-6">
          <h3 class="font-semibold text-sm">Task Progress</h3>
          <div
            v-for="(task, key) in (executionData.tasks as Record<string, Record<string, unknown>>)"
            :key="String(key)"
            class="rounded-lg border p-4 transition-colors"
            :class="{
              'border-green-300 bg-green-50/50': String(task.status) === 'COMPLETED',
              'border-blue-300 bg-blue-50/50': String(task.status) === 'RUNNING',
              'border-red-300 bg-red-50/50': String(task.status) === 'FAILED',
              'border-border': !['COMPLETED', 'RUNNING', 'FAILED'].includes(String(task.status)),
            }"
          >
            <div class="flex items-center justify-between mb-2">
              <div class="flex items-center gap-2">
                <span v-if="String(task.status) === 'COMPLETED'" class="text-green-600 text-sm">&#10003;</span>
                <span v-else-if="String(task.status) === 'RUNNING'" class="h-3 w-3 animate-spin rounded-full border-2 border-blue-500 border-t-transparent inline-block" />
                <span v-else-if="String(task.status) === 'FAILED'" class="text-red-600 text-sm">&#10007;</span>
                <span v-else class="text-muted-foreground text-sm">&#9679;</span>
                <span class="font-mono text-sm font-medium">{{ key }}</span>
              </div>
              <StatusBadge :status="String(task.status ?? 'pending')" />
            </div>
            <p v-if="task.service_name" class="text-xs text-muted-foreground mb-2">
              Agent: <span class="font-medium">{{ task.service_name }}</span>
            </p>
            <!-- Show agent's output when task is completed -->
            <div v-if="String(task.status) === 'COMPLETED'" class="mt-2">
              <details class="group">
                <summary class="cursor-pointer text-xs font-medium text-primary hover:underline">
                  View Output
                </summary>
                <div v-if="taskOutputs[String(key)]"
                     class="mt-2 rounded border border-border bg-background p-4 text-sm whitespace-pre-wrap max-h-96 overflow-y-auto leading-relaxed">
                  {{ taskOutputs[String(key)] }}
                </div>
                <div v-else class="mt-2 text-xs text-muted-foreground italic">
                  Loading output...
                </div>
              </details>
            </div>
            <p v-if="task.error" class="text-xs text-destructive mt-1">{{ task.error }}</p>
          </div>
        </div>

        <!-- Overall run data -->
        <div v-if="executionData" class="rounded-lg border border-border p-4 mb-6">
          <h3 class="font-semibold mb-2 text-sm">Full Run Details</h3>
          <JsonViewer :data="executionData" />
        </div>

        <div class="flex flex-wrap gap-3">
          <button
            class="rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80"
            @click="viewPlanDrafts"
          >
            View Plan Drafts
          </button>
          <button
            class="rounded-md border border-input px-4 py-2 text-sm font-medium hover:bg-accent"
            @click="startOver"
          >
            Start New Wizard
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
