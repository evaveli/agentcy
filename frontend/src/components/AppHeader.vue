<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'
import { Menu, User } from 'lucide-vue-next'
import { ref } from 'vue'

const emit = defineEmits<{ 'toggle-sidebar': [] }>()
const auth = useAuthStore()

const editingUsername = ref(false)
const usernameInput = ref(auth.username)

function saveUsername() {
  if (usernameInput.value.trim()) {
    auth.setUsername(usernameInput.value.trim())
  }
  editingUsername.value = false
}
</script>

<template>
  <header class="flex h-14 items-center justify-between border-b border-border bg-card px-4">
    <button
      class="rounded-md p-2 hover:bg-accent"
      @click="emit('toggle-sidebar')"
    >
      <Menu class="h-5 w-5" />
    </button>

    <div class="flex items-center gap-3">
      <div
        class="flex items-center gap-2 rounded-md bg-secondary px-3 py-1.5 text-sm"
      >
        <User class="h-4 w-4 text-muted-foreground" />
        <template v-if="!editingUsername">
          <span
            class="cursor-pointer hover:underline"
            @click="editingUsername = true"
          >
            {{ auth.username }}
          </span>
        </template>
        <template v-else>
          <input
            v-model="usernameInput"
            class="w-28 bg-transparent text-sm outline-none"
            @keyup.enter="saveUsername"
            @blur="saveUsername"
            autofocus
          />
        </template>
      </div>
    </div>
  </header>
</template>
