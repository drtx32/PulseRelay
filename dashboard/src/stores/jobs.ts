import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Job, JobStats } from '@/types'

export const useJobsStore = defineStore('jobs', () => {
  const jobs = ref<Job[]>([])
  const stats = ref<JobStats>({
    running: 0,
    total_jobs: 0,
    pending: 0,
    completed: 0,
    failed: 0,
    cancelled: 0,
    max_concurrent: 10
  })
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchJobs() {
    loading.value = true
    error.value = null
    try {
      const response = await fetch('/api/jobs')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      jobs.value = data.jobs || []
      stats.value = data.stats || stats.value
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch jobs'
      console.error('Failed to fetch jobs:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    jobs,
    stats,
    loading,
    error,
    fetchJobs
  }
})
