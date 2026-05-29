import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Event } from '@/types'

export const useEventsStore = defineStore('events', () => {
  const events = ref<Event[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  const recentEvents = computed(() =>
    [...events.value]
      .sort((a, b) => new Date(b.event.timestamp).getTime() - new Date(a.event.timestamp).getTime())
      .slice(0, 50)
  )

  const eventsBySource = computed(() => {
    const bySource: Record<string, Event[]> = {}
    for (const event of events.value) {
      const key = event.source.type || 'unknown'
      if (!bySource[key]) bySource[key] = []
      bySource[key].push(event)
    }
    return bySource
  })

  async function fetchEvents(limit = 100) {
    loading.value = true
    error.value = null
    try {
      const response = await fetch(`/api/events?limit=${limit}`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      events.value = data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch events'
      console.error('Failed to fetch events:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    events,
    loading,
    error,
    recentEvents,
    eventsBySource,
    fetchEvents
  }
})
