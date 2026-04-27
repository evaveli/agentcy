import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/DashboardView.vue'),
    },
    // Wizard
    {
      path: '/wizard',
      name: 'work-wizard',
      component: () => import('@/views/wizard/WorkWizardView.vue'),
    },
    // Pipelines
    {
      path: '/pipelines',
      name: 'pipelines',
      component: () => import('@/views/pipelines/PipelineListView.vue'),
    },
    {
      path: '/pipelines/create',
      name: 'pipeline-create',
      component: () => import('@/views/pipelines/PipelineCreateView.vue'),
    },
    {
      path: '/pipelines/:pipelineId',
      name: 'pipeline-detail',
      component: () => import('@/views/pipelines/PipelineDetailView.vue'),
      props: true,
    },
    // Agents
    {
      path: '/agents',
      name: 'agents',
      component: () => import('@/views/agents/AgentListView.vue'),
    },
    {
      path: '/agents/register',
      name: 'agent-register',
      component: () => import('@/views/agents/AgentRegisterView.vue'),
    },
    {
      path: '/agents/:agentId',
      name: 'agent-detail',
      component: () => import('@/views/agents/AgentDetailView.vue'),
      props: true,
    },
    // Templates
    {
      path: '/templates',
      name: 'templates',
      component: () => import('@/views/templates/TemplateListView.vue'),
    },
    {
      path: '/templates/create',
      name: 'template-create',
      component: () => import('@/views/templates/TemplateCreateView.vue'),
    },
    {
      path: '/templates/:templateId',
      name: 'template-detail',
      component: () => import('@/views/templates/TemplateDetailView.vue'),
      props: true,
    },
    // CNP
    {
      path: '/cnp',
      name: 'cnp-overview',
      component: () => import('@/views/cnp/CnpOverviewView.vue'),
    },
    {
      path: '/cnp/cfps',
      name: 'cfp-list',
      component: () => import('@/views/cnp/CfpListView.vue'),
    },
    {
      path: '/cnp/awards',
      name: 'award-list',
      component: () => import('@/views/cnp/AwardListView.vue'),
    },
    // Graph Store
    {
      path: '/graph-store/plans',
      name: 'plan-drafts',
      component: () => import('@/views/graph-store/PlanDraftListView.vue'),
    },
    {
      path: '/graph-store/plans/:planId',
      name: 'plan-draft-detail',
      component: () => import('@/views/graph-store/PlanDraftDetailView.vue'),
      props: true,
    },
    {
      path: '/graph-store/revisions',
      name: 'plan-revisions',
      component: () => import('@/views/graph-store/PlanRevisionsView.vue'),
    },
    {
      path: '/graph-store/suggestions',
      name: 'plan-suggestions',
      component: () => import('@/views/graph-store/PlanSuggestionsView.vue'),
    },
    {
      path: '/graph-store/task-specs',
      name: 'task-specs',
      component: () => import('@/views/graph-store/TaskSpecListView.vue'),
    },
    {
      path: '/graph-store/bids',
      name: 'bids',
      component: () => import('@/views/graph-store/BidListView.vue'),
    },
    {
      path: '/graph-store/ethics',
      name: 'ethics',
      component: () => import('@/views/graph-store/EthicsView.vue'),
    },
    // Semantic
    {
      path: '/semantic',
      name: 'semantic-overview',
      component: () => import('@/views/semantic/SemanticOverviewView.vue'),
    },
    {
      path: '/semantic/sparql',
      name: 'sparql-explorer',
      component: () => import('@/views/semantic/SparqlExplorerView.vue'),
    },
    {
      path: '/semantic/domain',
      name: 'domain-entities',
      component: () => import('@/views/semantic/DomainEntitiesView.vue'),
    },
    {
      path: '/semantic/recommend',
      name: 'plan-recommend',
      component: () => import('@/views/semantic/PlanRecommendView.vue'),
    },
    // Services
    {
      path: '/services',
      name: 'services',
      component: () => import('@/views/services/ServiceListView.vue'),
    },
    {
      path: '/services/:serviceId',
      name: 'service-detail',
      component: () => import('@/views/services/ServiceDetailView.vue'),
      props: true,
    },
    // Evaluation
    {
      path: '/evaluation',
      name: 'evaluation-overview',
      component: () => import('@/views/evaluation/EvalOverviewView.vue'),
    },
    {
      path: '/evaluation/e1',
      name: 'eval-e1',
      component: () => import('@/views/evaluation/E1QualityView.vue'),
    },
    {
      path: '/evaluation/e3',
      name: 'eval-e3',
      component: () => import('@/views/evaluation/E3AblationView.vue'),
    },
    {
      path: '/evaluation/e4',
      name: 'eval-e4',
      component: () => import('@/views/evaluation/E4EthicsView.vue'),
    },
    {
      path: '/evaluation/monitor',
      name: 'eval-monitor',
      component: () => import('@/views/evaluation/PipelineMonitorView.vue'),
    },
    // Audit
    {
      path: '/audit',
      name: 'audit-logs',
      component: () => import('@/views/audit/AuditLogView.vue'),
    },
    {
      path: '/audit/escalations',
      name: 'escalations',
      component: () => import('@/views/audit/EscalationsView.vue'),
    },
  ],
})

export default router
