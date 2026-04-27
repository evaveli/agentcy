import { ref, computed } from 'vue'

export function usePagination(pageSize = 20) {
  const offset = ref(0)
  const total = ref(0)

  const page = computed(() => Math.floor(offset.value / pageSize) + 1)
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
  const hasNext = computed(() => offset.value + pageSize < total.value)
  const hasPrev = computed(() => offset.value > 0)

  function next() {
    if (hasNext.value) offset.value += pageSize
  }

  function prev() {
    if (hasPrev.value) offset.value = Math.max(0, offset.value - pageSize)
  }

  function reset() {
    offset.value = 0
  }

  return {
    offset,
    limit: pageSize,
    total,
    page,
    totalPages,
    hasNext,
    hasPrev,
    next,
    prev,
    reset,
  }
}
