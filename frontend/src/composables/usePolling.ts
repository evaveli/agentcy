import { ref, onMounted, onUnmounted } from 'vue'

export function usePolling(fn: () => Promise<void>, intervalMs = 10000) {
  const loading = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  async function execute() {
    loading.value = true
    try {
      await fn()
    } finally {
      loading.value = false
    }
  }

  function start() {
    execute()
    timer = setInterval(execute, intervalMs)
  }

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onMounted(start)
  onUnmounted(stop)

  return { loading, refresh: execute, stop }
}
