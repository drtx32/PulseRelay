<template>
  <div class="dashboard">
    <v-row>
      <v-col cols="12" sm="6" lg="3">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <template #prepend>
              <v-avatar color="success" size="40">
                <v-icon icon="mdi-source-branch" />
              </v-avatar>
            </template>
            <v-card-title>Sources Healthy</v-card-title>
            <v-card-subtitle>{{ sourcesStore.healthySources.length }} / {{ sourcesStore.sources.length }}</v-card-subtitle>
          </v-card-item>
        </v-card>
      </v-col>

      <v-col cols="12" sm="6" lg="3">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <template #prepend>
              <v-avatar color="info" size="40">
                <v-icon icon="mdi-truck-delivery" />
              </v-avatar>
            </template>
            <v-card-title>Deliveries Active</v-card-title>
            <v-card-subtitle>{{ deliveriesStore.activeDeliveries.length }} / {{ deliveriesStore.deliveries.length }}</v-card-subtitle>
          </v-card-item>
        </v-card>
      </v-col>

      <v-col cols="12" sm="6" lg="3">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <template #prepend>
              <v-avatar color="warning" size="40">
                <v-icon icon="mdi-lightning-bolt" />
              </v-avatar>
            </template>
            <v-card-title>Events</v-card-title>
            <v-card-subtitle>{{ eventsStore.events.length }} total</v-card-subtitle>
          </v-card-item>
        </v-card>
      </v-col>

      <v-col cols="12" sm="6" lg="3">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <template #prepend>
              <v-avatar color="primary" size="40">
                <v-icon icon="mdi-jobs" />
              </v-avatar>
            </template>
            <v-card-title>Jobs</v-card-title>
            <v-card-subtitle>{{ jobsStore.stats.running }} running / {{ jobsStore.stats.total_jobs }} total</v-card-subtitle>
          </v-card-item>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-4">
      <v-col cols="12" lg="6">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title>Sources Health</v-card-title>
          </v-card-item>
          <v-card-text>
            <v-list density="compact" v-if="sourcesStore.sources.length > 0">
              <v-list-item
                v-for="source in sourcesStore.sources"
                :key="source.id"
                :prepend-icon="getStateIcon(source.health.state)"
                :title="source.name"
                :subtitle="`State: ${source.health.state}`"
              >
                <template #append>
                  <v-chip
                    :color="getStateColor(source.health.state)"
                    size="small"
                    variant="tonal"
                  >
                    {{ source.health.state }}
                  </v-chip>
                </template>
              </v-list-item>
            </v-list>
            <v-alert v-else type="info" variant="tonal">No sources registered</v-alert>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" lg="6">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title>Deliveries Status</v-card-title>
          </v-card-item>
          <v-card-text>
            <v-list density="compact" v-if="deliveriesStore.deliveries.length > 0">
              <v-list-item
                v-for="delivery in deliveriesStore.deliveries"
                :key="delivery.id"
                :prepend-icon="delivery.enabled ? 'mdi-check-circle' : 'mdi-close-circle'"
                :title="delivery.name"
                :subtitle="`State: ${delivery.state.state}`"
              >
                <template #append>
                  <v-chip
                    :color="delivery.enabled ? 'success' : 'error'"
                    size="small"
                    variant="tonal"
                  >
                    {{ delivery.enabled ? 'enabled' : 'disabled' }}
                  </v-chip>
                </template>
              </v-list-item>
            </v-list>
            <v-alert v-else type="info" variant="tonal">No deliveries registered</v-alert>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-4">
      <v-col cols="12" lg="6">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title>Recent Events</v-card-title>
          </v-card-item>
          <v-card-text>
            <v-list density="compact" v-if="eventsStore.recentEvents.length > 0">
              <v-list-item
                v-for="event in eventsStore.recentEvents.slice(0, 10)"
                :key="event.id"
                :prepend-icon="getEventIcon(event.event.type)"
                :title="event.content.title || event.event.type"
                :subtitle="formatTime(event.event.timestamp)"
              >
                <template #append>
                  <v-chip size="small" variant="tonal" :color="getEventColor(event.event.type)">
                    {{ event.event.type }}
                  </v-chip>
                </template>
              </v-list-item>
            </v-list>
            <v-alert v-else type="info" variant="tonal">No events recorded</v-alert>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" lg="6">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title>Job Statistics</v-card-title>
          </v-card-item>
          <v-card-text>
            <v-row>
              <v-col cols="6" sm="4">
                <v-sheet color="success" rounded class="pa-2 text-center">
                  <div class="text-h6">{{ jobsStore.stats.completed }}</div>
                  <div class="text-caption">Completed</div>
                </v-sheet>
              </v-col>
              <v-col cols="6" sm="4">
                <v-sheet color="error" rounded class="pa-2 text-center">
                  <div class="text-h6">{{ jobsStore.stats.failed }}</div>
                  <div class="text-caption">Failed</div>
                </v-sheet>
              </v-col>
              <v-col cols="6" sm="4">
                <v-sheet color="warning" rounded class="pa-2 text-center">
                  <div class="text-h6">{{ jobsStore.stats.pending }}</div>
                  <div class="text-caption">Pending</div>
                </v-sheet>
              </v-col>
              <v-col cols="6" sm="4">
                <v-sheet color="primary" rounded class="pa-2 text-center">
                  <div class="text-h6">{{ jobsStore.stats.running }}</div>
                  <div class="text-caption">Running</div>
                </v-sheet>
              </v-col>
              <v-col cols="6" sm="4">
                <v-sheet color="grey" rounded class="pa-2 text-center">
                  <div class="text-h6">{{ jobsStore.stats.cancelled }}</div>
                  <div class="text-caption">Cancelled</div>
                </v-sheet>
              </v-col>
              <v-col cols="6" sm="4">
                <v-sheet color="info" rounded class="pa-2 text-center">
                  <div class="text-h6">{{ jobsStore.stats.max_concurrent }}</div>
                  <div class="text-caption">Max</div>
                </v-sheet>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-4" v-if="jobsStore.jobs.length > 0">
      <v-col cols="12">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title>Recent Jobs</v-card-title>
          </v-card-item>
          <v-card-text>
            <v-table density="compact">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Status</th>
                  <th>Priority</th>
                  <th>Created</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="job in jobsStore.jobs.slice(0, 10)" :key="job.id">
                  <td class="font-mono text-caption">{{ job.id }}</td>
                  <td>
                    <v-chip :color="getJobStatusColor(job.status)" size="small" variant="tonal">
                      {{ job.status }}
                    </v-chip>
                  </td>
                  <td>{{ job.priority }}</td>
                  <td>{{ formatTime(job.created_at) }}</td>
                  <td class="text-error text-caption">{{ job.error || '-' }}</td>
                </tr>
              </tbody>
            </v-table>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useSourcesStore } from '@/stores/sources'
import { useDeliveriesStore } from '@/stores/deliveries'
import { useEventsStore } from '@/stores/events'
import { useJobsStore } from '@/stores/jobs'

const sourcesStore = useSourcesStore()
const deliveriesStore = useDeliveriesStore()
const eventsStore = useEventsStore()
const jobsStore = useJobsStore()

onMounted(async () => {
  await Promise.all([
    sourcesStore.fetchSources(),
    deliveriesStore.fetchDeliveries(),
    eventsStore.fetchEvents(),
    jobsStore.fetchJobs()
  ])
})

function getStateIcon(state: string): string {
  const icons: Record<string, string> = {
    running: 'mdi-check-circle',
    error: 'mdi-alert-circle',
    stopped: 'mdi-stop-circle',
    starting: 'mdi-progress-clock',
    reconnecting: 'mdi-sync'
  }
  return icons[state] || 'mdi-help-circle'
}

function getStateColor(state: string): string {
  const colors: Record<string, string> = {
    running: 'success',
    error: 'error',
    stopped: 'grey',
    starting: 'warning',
    reconnecting: 'warning'
  }
  return colors[state] || 'grey'
}

function getEventIcon(type: string): string {
  if (type.includes('trigger')) return 'mdi-lightning-bolt'
  if (type.includes('message')) return 'mdi-message-text'
  if (type.includes('webhook')) return 'mdi-webhook'
  return 'mdi-lightning'
}

function getEventColor(type: string): string {
  if (type.includes('trigger')) return 'warning'
  if (type.includes('error')) return 'error'
  return 'info'
}

function getJobStatusColor(status: string): string {
  const colors: Record<string, string> = {
    completed: 'success',
    failed: 'error',
    pending: 'warning',
    running: 'primary',
    cancelled: 'grey',
    timeout: 'error'
  }
  return colors[status] || 'grey'
}

function formatTime(timestamp: string): string {
  if (!timestamp) return '-'
  try {
    const date = new Date(timestamp)
    return date.toLocaleString()
  } catch {
    return timestamp
  }
}
</script>
