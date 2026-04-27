<script setup lang="ts">
import { ref } from 'vue'
import JsonViewer from '@/components/JsonViewer.vue'
import { executeSparql } from '@/api/semantic'

const query = ref(`SELECT ?s ?p ?o
WHERE {
  ?s ?p ?o .
}
LIMIT 20`)

const results = ref<unknown>(null)
const error = ref<string | null>(null)
const executing = ref(false)

async function execute() {
  error.value = null
  results.value = null
  executing.value = true
  try {
    results.value = await executeSparql(query.value)
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    executing.value = false
  }
}
</script>

<template>
  <div>
    <router-link to="/semantic" class="text-sm text-muted-foreground hover:underline">&larr; Semantic Layer</router-link>
    <h1 class="text-2xl font-bold mt-1 mb-6">SPARQL Explorer</h1>

    <div class="mb-4">
      <label class="block text-sm font-medium mb-2">SPARQL Query</label>
      <textarea
        v-model="query"
        rows="8"
        class="w-full rounded-lg border border-input bg-background px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-ring"
      />
    </div>

    <button
      :disabled="executing"
      class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 mb-4"
      @click="execute"
    >
      {{ executing ? 'Executing...' : 'Execute Query' }}
    </button>

    <div v-if="error" class="mb-4 rounded-lg border border-destructive/50 bg-destructive/10 p-4">
      <p class="text-sm text-destructive">{{ error }}</p>
    </div>

    <div v-if="results" class="rounded-lg border border-border bg-card p-4">
      <h2 class="font-semibold mb-3">Results</h2>
      <!-- Try to render as table if results have bindings -->
      <template v-if="(results as any)?.results?.bindings">
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-border bg-muted/50">
                <th v-for="v in ((results as any).head?.vars || [])" :key="v" class="px-3 py-2 text-left font-medium text-muted-foreground">
                  {{ v }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(binding, i) in (results as any).results.bindings" :key="i" class="border-b border-border last:border-0">
                <td v-for="v in ((results as any).head?.vars || [])" :key="v" class="px-3 py-2 font-mono text-xs">
                  {{ binding[v]?.value ?? '' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="mt-2 text-xs text-muted-foreground">
          {{ (results as any).results.bindings.length }} result(s)
        </p>
      </template>
      <JsonViewer v-else :data="results" />
    </div>
  </div>
</template>
