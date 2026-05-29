<template>
  <div class="sources-page">
    <v-row>
      <v-col cols="12">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <template #prepend>
              <v-btn icon="mdi-refresh" variant="text" @click="sourcesStore.fetchSources()" />
            </template>
            <v-card-title>Source Adapters</v-card-title>
            <v-card-subtitle>Manage and monitor event sources</v-card-subtitle>
          </v-card-item>
          <v-card-text>
            <v-data-table
              :headers="headers"
              :items="sourcesStore.sources"
              :loading="sourcesStore.loading"
              item-value="id"
              class="elevation-1"
            >
              <template #item.name="{ item }">
                <div class="d-flex align-center">
                  <v-icon :icon="getStateIcon(item.health.state)" :color="getStateColor(item.health.state)" class="mr-2" />
                  <span>{{ item.name }}</span>
                </div>
              </template>

              <template #item.state="{ item }">
                <v-chip :color="getStateColor(item.health.state)" size="small" variant="tonal">
                  {{ item.health.state }}
                </v-chip>
              </template>

              <template #item.last_event_at="{ item }">
                {{ formatTime(item.health.last_event_at) }}
              </template>

              <template #item.capabilities="{ item }">
                <div class="d-flex gap-1 flex-wrap">
                  <v-chip
                    v-if="item.manifest.capabilities.supports_webhook"
                    size="x-small"
                    color="info"
                    variant="outlined"
                  >
                    webhook
                  </v-chip>
                  <v-chip
                    v-if="item.manifest.capabilities.supports_websocket"
                    size="x-small"
                    color="info"
                    variant="outlined"
                  >
                    websocket
                  </v-chip>
                  <v-chip
                    v-if="item.manifest.capabilities.supports_streaming"
                    size="x-small"
                    color="info"
                    variant="outlined"
                  >
                    streaming
                  </v-chip>
                </div>
              </template>

              <template #item.error="{ item }">
                <span v-if="item.health.last_error" class="text-error text-caption">
                  {{ item.health.last_error }}
                </span>
                <span v-else class="text-success">-</span>
              </template>

              <template #no-data>
                <v-alert type="info" variant="tonal" class="ma-4">
                  No sources registered. Sources will appear here when they are initialized.
                </v-alert>
              </template>
            </v-data-table>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-4" v-if="sourcesStore.sources.length > 0">
      <v-col cols="12">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title>Source Health Details</v-card-title>
          </v-card-item>
          <v-card-text>
            <v-expansion-panels variant="accordion">
              <v-expansion-panel
                v-for="source in sourcesStore.sources"
                :key="source.id"
                :title="source.name"
              >
                <template #text>
                  <v-row dense>
                    <v-col cols="12" sm="6">
                      <v-list density="compact">
                        <v-list-item>
                          <v-list-item-title class="text-caption text-grey">State</v-list-item-title>
                          <v-list-item-subtitle>{{ source.health.state }}</v-list-item-subtitle>
                        </v-list-item>
                        <v-list-item>
                          <v-list-item-title class="text-caption text-grey">Reconnect Count</v-list-item-title>
                          <v-list-item-subtitle>{{ source.health.reconnect_count }}</v-list-item-subtitle>
                        </v-list-item>
                      </v-list>
                    </v-col>
                    <v-col cols="12" sm="6">
                      <v-list density="compact">
                        <v-list-item>
                          <v-list-item-title class="text-caption text-grey">Last Event</v-list-item-title>
                          <v-list-item-subtitle>{{ formatTime(source.health.last_event_at) }}</v-list-item-subtitle>
                        </v-list-item>
                        <v-list-item>
                          <v-list-item-title class="text-caption text-grey">Enabled</v-list-item-title>
                          <v-list-item-subtitle>{{ source.enabled ? 'Yes' : 'No' }}</v-list-item-subtitle>
                        </v-list-item>
                      </v-list>
                    </v-col>
                  </v-row>
                  <v-alert v-if="source.health.last_error" type="error" variant="tonal" class="mt-2">
                    {{ source.health.last_error }}
                  </v-alert>
                </template>
              </v-expansion-panel>
            </v-expansion-panels>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useSourcesStore } from '@/stores/sources'

const sourcesStore = useSourcesStore()

const headers = [
  { title: 'Name', key: 'name', sortable: true },
  { title: 'ID', key: 'id', sortable: true },
  { title: 'State', key: 'state', sortable: true },
  { title: 'Last Event', key: 'last_event_at', sortable: true },
  { title: 'Capabilities', key: 'capabilities', sortable: false },
  { title: 'Last Error', key: 'error', sortable: false }
]

onMounted(() => {
  sourcesStore.fetchSources()
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
