// Type definitions for PulseRelay Dashboard

export interface SourceHealth {
  state: string
  last_event_at: string
  last_error: string
  reconnect_count: number
}

export interface SourceManifest {
  id: string
  name: string
  version: string
  description: string
  capabilities: {
    supports_websocket: boolean
    supports_webhook: boolean
    supports_streaming: boolean
    supports_ack: boolean
    supports_replay: boolean
  }
  tags: string[]
}

export interface Source {
  id: string
  name: string
  manifest: SourceManifest
  health: SourceHealth
  enabled: boolean
}

export interface DeliveryState {
  state: string
  enabled: boolean
  last_error: string
}

export interface DeliveryManifest {
  id: string
  name: string
  version: string
  description: string
  capabilities: {
    supports_text: boolean
    supports_markdown: boolean
    supports_html: boolean
    supports_files: boolean
    supports_streaming: boolean
    supports_actions: boolean
  }
  tags: string[]
}

export interface Delivery {
  id: string
  name: string
  manifest: DeliveryManifest
  state: DeliveryState
  enabled: boolean
}

export interface EventSource {
  type: string
  id: string
  tenant_id: string
  name: string
}

export interface EventSender {
  id: string
  name: string
  email: string
  role: string
  trust_level: string
}

export interface EventMeta {
  type: string
  action: string
  timestamp: string
  dedupe_key: string
}

export interface EventContent {
  title: string
  text: string
  html: string
  files: Array<Record<string, unknown>>
  raw: Record<string, unknown>
}

export interface EventContext {
  conversation_id: string
  project_id: string
  repo: string
  channel_id: string
  thread_id: string
  url: string
  extra: Record<string, unknown>
}

export interface EventPermissions {
  allowed_tools: string[]
  allowed_agents: string[]
  requires_approval: boolean
  max_risk_level: string
}

export interface EventRouting {
  priority: string
  labels: string[]
  target_agent: string
}

export interface Event {
  id: string
  source: EventSource
  sender: EventSender
  event: EventMeta
  content: EventContent
  context: EventContext
  permissions: EventPermissions
  routing: EventRouting
}

export interface Job {
  id: string
  status: string
  session_id: string
  priority: string
  created_at: string
  started_at: string
  completed_at: string
  error: string
  retry_count: number
  max_retries: number
  metadata: Record<string, unknown>
}

export interface JobStats {
  running: number
  total_jobs: number
  pending: number
  completed: number
  failed: number
  cancelled: number
  max_concurrent: number
}

export interface DashboardStats {
  sources_healthy: number
  sources_total: number
  deliveries_active: number
  deliveries_total: number
  events_total: number
  jobs_stats: JobStats
}
