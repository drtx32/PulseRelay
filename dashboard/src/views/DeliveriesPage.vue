<template>
  <div class="deliveries-page">
    <v-row>
      <v-col cols="12">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <template #prepend>
              <v-btn icon="mdi-refresh" variant="text" @click="deliveriesStore.fetchDeliveries()" />
            </template>
            <v-card-title>Delivery Handlers</v-card-title>
            <v-card-subtitle>Monitor outbound message delivery</v-card-subtitle>
          </v-card-item>
          <v-card-text>
            <v-data-table
              :headers="headers"
              :items="deliveriesStore.deliveries"
              :loading="deliveriesStore.loading"
              item-value="id"
              class="elevation-1"
            >
              <template #item.name="{ item }">
                <div class="d-flex align-center">
                  <v-icon :icon="item.enabled ? 'mdi-check-circle' : 'mdi-close-circle'" :color="item.enabled ? 'success' : 'error'" class="mr-2" />
                  <span>{{ item.name }}</span>
                </div>
              </template>

              <template #item.state="{ item }">
                <v-chip :color="getStateColor(item.state.state)" size="small" variant="tonal">
                  {{ item.state.state }}
                </v-chip>
              </template>

              <template #item.enabled="{ item }">
                <v-switch
                  v-model="item.enabled"
                  color="success"
                  hide-details
                  density="compact"
                  disabled
                />
              </template>

              <template #item.capabilities="{ item }">
                <div class="d-flex gap-1 flex-wrap">
                  <v-chip v-if="item.manifest.capabilities.supports_text" size="x-small" color="info" variant="outlined">text</v-chip>
                  <v-chip v-if="item.manifest.capabilities.supports_markdown" size="x-small" color="info" variant="outlined">markdown</v-chip>
                  <v-chip v-if="item.manifest.capabilities.supports_html" size="x-small" color="info" variant="outlined">html</v-chip>
                  <v-chip v-if="item.manifest.capabilities.supports_files" size="x-small" color="info" variant="outlined">files</v-chip>
                  <v-chip v-if="item.manifest.capabilities.supports_streaming" size="x-small" color="info" variant="outlined">streaming</v-chip>
                </div>
              </template>

              <template #item.last_error="{ item }">
                <span v-if="item.state.last_error" class="text-error text-caption">
                  {{ item.state.last_error }}
                </span>
                <span v-else class="text-success">-</span>
              </template>

              <template #no-data>
                <v-alert type="info" variant="tonal" class="ma-4">
                  No delivery handlers registered. Delivery handlers will appear here when they are initialized.
                </v-alert>
              </template>
            </v-data-table>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-4">
      <v-col cols="12" sm="4">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title class="text-h6 text-success">Active</v-card-title>
          </v-card-item>
          <v-card-text class="text-center">
            <div class="text-h4">{{ deliveriesStore.activeDeliveries.length }}</div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" sm="4">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title class="text-h6 text-error">Inactive</v-card-title>
          </v-card-item>
          <v-card-text class="text-center">
            <div class="text-h4">{{ deliveriesStore.inactiveDeliveries.length }}</div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" sm="4">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title class="text-h6 text-info">Total</v-card-title>
          </v-card-item>
          <v-card-text class="text-center">
            <div class="text-h4">{{ deliveriesStore.deliveries.length }}</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-4" v-if="deliveriesStore.deliveries.length > 0">
      <v-col cols="12">
        <v-card color="surface" variant="elevated">
          <v-card-item>
            <v-card-title>Delivery Capabilities</v-card-title>
          </v-card-item>
          <v-card-text>
            <v-expansion-panels variant="accordion">
              <v-expansion-panel
                v-for="delivery in deliveriesStore.deliveries"
                :key="delivery.id"
                :title="delivery.name"
              >
                <template #text>
                  <v-list density="compact">
                    <v-list-item>
                      <v-list-item-title class="text-caption text-grey">Description</v-list-item-title>
                      <v-list-item-subtitle>{{ delivery.manifest.description || 'N/A' }}</v-list-item-subtitle>
                    </v-list-item>
                    <v-list-item>
                      <v-list-item-title class="text-caption text-grey">Version</v-list-item-title>
                      <v-list-item-subtitle>{{ delivery.manifest.version }}</v-list-item-subtitle>
                    </v-list-item>
                    <v-list-item>
                      <v-list-item-title class="text-caption text-grey">State</v-list-item-title>
                      <v-list-item-subtitle>{{ delivery.state.state }}</v-list-item-subtitle>
                    </v-list-item>
                    <v-list-item>
                      <v-list-item-title class="text-caption text-grey">Tags</v-list-item-title>
                      <v-list-item-subtitle>{{ delivery.manifest.tags.join(', ') || 'None' }}</v-list-item-subtitle>
                    </v-list-item>
                  </v-list>
                  <v-alert v-if="delivery.state.last_error" type="error" variant="tonal" class="mt-2">
                    {{ delivery.state.last_error }}
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
import { useDeliveriesStore } from '@/stores/deliveries'

const deliveriesStore = useDeliveriesStore()

const headers = [
  { title: 'Name', key: 'name', sortable: true },
  { title: 'ID', key: 'id', sortable: true },
  { title: 'State', key: 'state', sortable: true },
  { title: 'Enabled', key: 'enabled', sortable: false },
  { title: 'Capabilities', key: 'capabilities', sortable: false },
  { title: 'Last Error', key: 'last_error', sortable: false }
]

onMounted(() => {
  deliveriesStore.fetchDeliveries()
})

function getStateColor(state: string): string {
  const colors: Record<string, string> = {
    ready: 'success',
    disabled: 'grey',
    error: 'error'
  }
  return colors[state] || 'grey'
}
</script>
