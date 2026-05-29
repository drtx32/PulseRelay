import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Delivery } from '@/types'

export const useDeliveriesStore = defineStore('deliveries', () => {
  const deliveries = ref<Delivery[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  const activeDeliveries = computed(() =>
    deliveries.value.filter(d => d.enabled && d.state.state === 'ready')
  )

  const inactiveDeliveries = computed(() =>
    deliveries.value.filter(d => !d.enabled || d.state.state !== 'ready')
  )

  async function fetchDeliveries() {
    loading.value = true
    error.value = null
    try {
      const response = await fetch('/api/deliveries')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      deliveries.value = data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch deliveries'
      console.error('Failed to fetch deliveries:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    deliveries,
    loading,
    error,
    activeDeliveries,
    inactiveDeliveries,
    fetchDeliveries
  }
})
