import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Source } from '@/types'

export const useSourcesStore = defineStore('sources', () => {
  const sources = ref<Source[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  const healthySources = computed(() =>
    sources.value.filter(s => s.health.state === 'running')
  )

  const unhealthySources = computed(() =>
    sources.value.filter(s => s.health.state !== 'running')
  )

  async function fetchSources() {
    loading.value = true
    error.value = null
    try {
      const response = await fetch('/api/sources')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      sources.value = data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch sources'
      console.error('Failed to fetch sources:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    sources,
    loading,
    error,
    healthySources,
    unhealthySources,
    fetchSources
  }
})
