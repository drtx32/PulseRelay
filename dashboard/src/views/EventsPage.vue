<template>
  <div class="events-page">
    <v-row>
      <v-col cols="12">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <template #prepend>
              <v-btn icon="mdi-refresh" variant="text" @click="eventsStore.fetchEvents()" />
            </template>
            <v-card-title>Event Stream</v-card-title>
            <v-card-subtitle>Recent events processed by PulseRelay</v-card-subtitle>
          </v-card-item>
          <v-card-text>
            <v-row class="mb-4">
              <v-col cols="12" sm="4">
                <v-select
                  v-model="filterSource"
                  :items="sourceOptions"
                  label="Filter by Source"
                  density="compact"
                  clearable
                  hide-details
                />
              </v-col>
              <v-col cols="12" sm="4">
                <v-select
                  v-model="filterType"
                  :items="typeOptions"
                  label="Filter by Type"
                  density="compact"
                  clearable
                  hide-details
                />
              </v-col>
              <v-col cols="12" sm="4">
                <v-text-field
                  v-model="searchText"
                  label="Search"
                  density="compact"
                  clearable
                  hide-details
                  prepend-inner-icon="mdi-magnify"
                />
              </v-col>
            </v-row>

            <v-data-table
              :headers="headers"
              :items="filteredEvents"
              :loading="eventsStore.loading"
              item-value="id"
              :items-per-page="25"
              class="elevation-1"
            >
              <template #item.id="{ item }">
                <span class="font-mono text-caption">{{ item.id.substring(0, 16) }}...</span>
              </template>

              <template #item.source="{ item }">
                <v-chip size="x-small" color="primary" variant="tonal">
                  {{ item.source.type }}
                </v-chip>
              </template>

              <template #item.type="{ item }">
                <v-chip size="x-small" :color="getEventColor(item.event.type)" variant="tonal">
                  {{ item.event.type }}
                </v-chip>
              </template>

              <template #item.sender="{ item }">
                <span class="text-caption">{{ item.sender.name || item.sender.id || '-' }}</span>
              </template>

              <template #item.content="{ item }">
                <span class="text-caption text-truncate d-inline-block" style="max-width: 300px;">
                  {{ item.content.text || item.content.title || '-' }}
                </span>
              </template>

              <template #item.timestamp="{ item }">
                {{ formatTime(item.event.timestamp) }}
              </template>

              <template #item.actions="{ item }">
                <v-btn icon="mdi-information" size="small" variant="text" @click="showEventDetails(item)" />
              </template>

              <template #no-data>
                <v-alert type="info" variant="tonal" class="ma-4">
                  No events recorded. Events will appear here as they are processed.
                </v-alert>
              </template>
            </v-data-table>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-dialog v-model="detailsDialog" max-width="800">
      <v-card v-if="selectedEvent">
        <v-card-item>
          <v-card-title>Event Details</v-card-title>
        </v-card-item>
        <v-card-text>
          <v-row>
            <v-col cols="12" sm="6">
              <v-list density="compact">
                <v-list-item>
                  <v-list-item-title class="text-caption text-grey">Event ID</v-list-item-title>
                  <v-list-item-subtitle class="font-mono">{{ selectedEvent.id }}</v-list-item-subtitle>
                </v-list-item>
                <v-list-item>
                  <v-list-item-title class="text-caption text-grey">Source</v-list-item-title>
                  <v-list-item-subtitle>{{ selectedEvent.source.type }} / {{ selectedEvent.source.id }}</v-list-item-subtitle>
                </v-list-item>
                <v-list-item>
                  <v-list-item-title class="text-caption text-grey">Type</v-list-item-title>
                  <v-list-item-subtitle>{{ selectedEvent.event.type }}</v-list-item-subtitle>
                </v-list-item>
                <v-list-item>
                  <v-list-item-title class="text-caption text-grey">Timestamp</v-list-item-title>
                  <v-list-item-subtitle>{{ selectedEvent.event.timestamp }}</v-list-item-subtitle>
                </v-list-item>
              </v-list>
            </v-col>
            <v-col cols="12" sm="6">
              <v-list density="compact">
                <v-list-item>
                  <v-list-item-title class="text-caption text-grey">Sender</v-list-item-title>
                  <v-list-item-subtitle>{{ selectedEvent.sender.name }} ({{ selectedEvent.sender.id }})</v-list-item-subtitle>
                </v-list-item>
                <v-list-item>
                  <v-list-item-title class="text-caption text-grey">Trust Level</v-list-item-title>
                  <v-list-item-subtitle>{{ selectedEvent.sender.trust_level }}</v-list-item-subtitle>
                </v-list-item>
                <v-list-item>
                  <v-list-item-title class="text-caption text-grey">Risk Level</v-list-item-title>
                  <v-list-item-subtitle>{{ selectedEvent.permissions.max_risk_level }}</v-list-item-subtitle>
                </v-list-item>
                <v-list-item>
                  <v-list-item-title class="text-caption text-grey">Routing</v-list-item-title>
                  <v-list-item-subtitle>{{ selectedEvent.routing.priority }} / {{ selectedEvent.routing.target_agent || 'auto' }}</v-list-item-subtitle>
                </v-list-item>
              </v-list>
            </v-col>
          </v-row>

          <v-divider class="my-4" />

          <v-list density="compact">
            <v-list-item>
              <v-list-item-title class="text-caption text-grey">Title</v-list-item-title>
              <v-list-item-subtitle>{{ selectedEvent.content.title || '-' }}</v-list-item-subtitle>
            </v-list-item>
            <v-list-item>
              <v-list-item-title class="text-caption text-grey">Content</v-list-item-title>
              <v-list-item-subtitle class="text-wrap">{{ selectedEvent.content.text || '-' }}</v-list-item-subtitle>
            </v-list-item>
          </v-list>

          <v-expansion-panels variant="accordion" class="mt-4" v-if="Object.keys(selectedEvent.content.raw).length > 0">
            <v-expansion-panel title="Raw Payload">
              <template #text>
                <pre class="text-caption font-mono">{{ JSON.stringify(selectedEvent.content.raw, null, 2) }}</pre>
              </template>
            </v-expansion-panel>
          </v-expansion-panels>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="primary" variant="text" @click="detailsDialog = false">Close</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useEventsStore } from '@/stores/events'
import type { Event } from '@/types'

const eventsStore = useEventsStore()

const filterSource = ref<string | null>(null)
const filterType = ref<string | null>(null)
const searchText = ref('')
const detailsDialog = ref(false)
const selectedEvent = ref<Event | null>(null)

const headers = [
  { title: 'ID', key: 'id', sortable: true },
  { title: 'Source', key: 'source', sortable: true },
  { title: 'Type', key: 'type', sortable: true },
  { title: 'Sender', key: 'sender', sortable: false },
  { title: 'Content', key: 'content', sortable: false },
  { title: 'Timestamp', key: 'timestamp', sortable: true },
  { title: 'Actions', key: 'actions', sortable: false }
]

const sourceOptions = computed(() => {
  const sources = new Set(eventsStore.events.map(e => e.source.type))
  return Array.from(sources)
})

const typeOptions = computed(() => {
  const types = new Set(eventsStore.events.map(e => e.event.type))
  return Array.from(types)
})

const filteredEvents = computed(() => {
  let events = eventsStore.events

  if (filterSource.value) {
    events = events.filter(e => e.source.type === filterSource.value)
  }

  if (filterType.value) {
    events = events.filter(e => e.event.type === filterType.value)
  }

  if (searchText.value) {
    const search = searchText.value.toLowerCase()
    events = events.filter(e =>
      e.content.text?.toLowerCase().includes(search) ||
      e.content.title?.toLowerCase().includes(search) ||
      e.sender.name?.toLowerCase().includes(search)
    )
  }

  return events
})

onMounted(() => {
  eventsStore.fetchEvents()
})

function getEventColor(type: string): string {
  if (type.includes('trigger')) return 'warning'
  if (type.includes('error')) return 'error'
  if (type.includes('message')) return 'info'
  return 'grey'
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

function showEventDetails(event: Event) {
  selectedEvent.value = event
  detailsDialog.value = true
}
</script>
