<script setup lang="ts">
import { useRoute } from 'vue-router'
import { computed } from 'vue'
import {
  LayoutDashboard,
  GitBranch,
  Bot,
  FileCode,
  Handshake,
  Database,
  Brain,
  Server,
  Shield,
  ChevronRight,
  Wand2,
  UserPlus,
  FlaskConical,
} from 'lucide-vue-next'

defineProps<{ collapsed: boolean }>()

const route = useRoute()

const navGroups = [
  {
    label: 'Overview',
    items: [
      { name: 'Dashboard', path: '/', icon: LayoutDashboard },
      { name: 'Work Wizard', path: '/wizard', icon: Wand2 },
    ],
  },
  {
    label: 'Orchestration',
    items: [
      { name: 'Pipelines', path: '/pipelines', icon: GitBranch },
      { name: 'Agents', path: '/agents', icon: Bot },
      { name: 'Register Agent', path: '/agents/register', icon: UserPlus },
      { name: 'Templates', path: '/templates', icon: FileCode },
    ],
  },
  {
    label: 'Contract Net',
    items: [
      { name: 'CNP Monitor', path: '/cnp', icon: Handshake },
    ],
  },
  {
    label: 'Graph Store',
    items: [
      { name: 'Plan Drafts', path: '/graph-store/plans', icon: Database },
      { name: 'Revisions', path: '/graph-store/revisions', icon: Database },
      { name: 'Suggestions', path: '/graph-store/suggestions', icon: Database },
      { name: 'Task Specs', path: '/graph-store/task-specs', icon: Database },
      { name: 'Bids', path: '/graph-store/bids', icon: Database },
      { name: 'Ethics', path: '/graph-store/ethics', icon: Shield },
    ],
  },
  {
    label: 'Knowledge Graph',
    items: [
      { name: 'Semantic', path: '/semantic', icon: Brain },
      { name: 'SPARQL', path: '/semantic/sparql', icon: Brain },
      { name: 'Domain', path: '/semantic/domain', icon: Brain },
      { name: 'Recommend', path: '/semantic/recommend', icon: Brain },
    ],
  },
  {
    label: 'Evaluation',
    items: [
      { name: 'Overview', path: '/evaluation', icon: FlaskConical },
      { name: 'E1 Agent Quality', path: '/evaluation/e1', icon: FlaskConical },
      { name: 'E3 Ablation', path: '/evaluation/e3', icon: FlaskConical },
      { name: 'E4 Ethics', path: '/evaluation/e4', icon: FlaskConical },
      { name: 'Pipeline Monitor', path: '/evaluation/monitor', icon: FlaskConical },
    ],
  },
  {
    label: 'Infrastructure',
    items: [
      { name: 'Services', path: '/services', icon: Server },
      { name: 'Audit Logs', path: '/audit', icon: Shield },
      { name: 'Escalations', path: '/audit/escalations', icon: Shield },
    ],
  },
]

function isActive(path: string) {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}
</script>

<template>
  <aside
    class="flex flex-col border-r border-border bg-card transition-all duration-200"
    :class="collapsed ? 'w-16' : 'w-64'"
  >
    <div class="flex h-14 items-center gap-2 border-b border-border px-4">
      <GitBranch class="h-6 w-6 text-primary shrink-0" />
      <span v-if="!collapsed" class="font-semibold text-sm truncate">
        Pipeline Orchestrator
      </span>
    </div>

    <nav class="flex-1 overflow-y-auto py-2">
      <div v-for="group in navGroups" :key="group.label" class="mb-2">
        <p
          v-if="!collapsed"
          class="px-4 py-1 text-xs font-medium uppercase tracking-wider text-muted-foreground"
        >
          {{ group.label }}
        </p>
        <router-link
          v-for="item in group.items"
          :key="item.path"
          :to="item.path"
          class="flex items-center gap-3 px-4 py-2 text-sm transition-colors hover:bg-accent"
          :class="isActive(item.path) ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground'"
        >
          <component :is="item.icon" class="h-4 w-4 shrink-0" />
          <span v-if="!collapsed" class="truncate">{{ item.name }}</span>
        </router-link>
      </div>
    </nav>
  </aside>
</template>
