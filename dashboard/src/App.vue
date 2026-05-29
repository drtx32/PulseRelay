<template>
  <v-app :theme="theme">
    <v-app-bar color="primary" density="compact">
      <v-app-bar-title>
        <v-icon icon="mdi-pulse" class="mr-2" />
        PulseRelay Dashboard
      </v-app-bar-title>

      <v-spacer />

      <v-btn-toggle v-model="theme" mandatory density="compact">
        <v-btn value="dark" icon="mdi-weather-night" />
        <v-btn value="light" icon="mdi-weather-sunny" />
      </v-btn-toggle>
    </v-app-bar>

    <v-navigation-drawer permanent rail expand-on-hover color="surface">
      <v-list density="compact" nav>
        <v-list-item
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          :prepend-icon="item.icon"
          :title="item.title"
          density="compact"
        />
      </v-list>
    </v-navigation-drawer>

    <v-main>
      <v-container fluid class="pa-4">
        <router-view />
      </v-container>
    </v-main>
  </v-app>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const theme = ref('dark')

const navItems = [
  { title: 'Dashboard', icon: 'mdi-view-dashboard', path: '/' },
  { title: 'Sources', icon: 'mdi-source-branch', path: '/sources' },
  { title: 'Deliveries', icon: 'mdi-truck-delivery', path: '/deliveries' },
  { title: 'Events', icon: 'mdi-lightning-bolt', path: '/events' },
]
</script>

<style scoped>
.v-main {
  background-color: rgb(var(--v-theme-background));
}
</style>
