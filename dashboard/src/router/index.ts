import { createRouter, createWebHashHistory } from 'vue-router'
import DashboardPage from '@/views/DashboardPage.vue'
import SourcesPage from '@/views/SourcesPage.vue'
import DeliveriesPage from '@/views/DeliveriesPage.vue'
import EventsPage from '@/views/EventsPage.vue'

const routes = [
  {
    path: '/',
    name: 'Dashboard',
    component: DashboardPage
  },
  {
    path: '/sources',
    name: 'Sources',
    component: SourcesPage
  },
  {
    path: '/deliveries',
    name: 'Deliveries',
    component: DeliveriesPage
  },
  {
    path: '/events',
    name: 'Events',
    component: EventsPage
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
