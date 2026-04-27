import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const username = ref(localStorage.getItem('po_username') || 'default')

  function setUsername(name: string) {
    username.value = name
    localStorage.setItem('po_username', name)
  }

  return { username, setUsername }
})
