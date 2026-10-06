export type CrossType = 'confirmed' | 'imminent' | 'pending' | string
export type MaType = 'type1' | 'type2' | 'type3' | string
export type PriceInteractionType = 'support' | 'crossover' | string

export interface Signal {
  symbol: string
  fyers_symbol: string
  date: string
  close: number
  sma44: number
  sma_dist_pct: number
  macd: number
  macd_signal: number
  macd_histogram: number
  cross_type: CrossType
  change_pct: number
  crossover_bars_ago?: number
  ma_type?: MaType
  is_double_bottom?: boolean
  price_interaction_type?: PriceInteractionType
  weekly_rising?: boolean | null
  promoted?: boolean
  watchlist_since?: string
  is_new_alert?: boolean
  trade_ready_at?: string
  ready_at?: string
  became_trade_ready_at?: string
  logged_at?: string
  alert_time?: string

  pct_slope?: number
  atr_slope?: number
  slope_first_half_pct?: number
  slope_second_half_pct?: number
  c1a_point_pass?: boolean
  c1a_linreg_pass?: boolean
  c1a_recovering_pass?: boolean
  c2a_low_proximity_pass?: boolean
  c2b_close_above_sma_pass?: boolean
  hist_consecutive_rising?: number
  imminent_gap_ratio?: number
  is_imminent_crossover?: boolean
}

export interface SignalPick {
  id: string
  symbol: string
  rationale: string
  date_added: string
  added_by?: string | null
  active?: boolean
  enabled?: boolean
  // Publication state (admin view only). Missing = legacy record = Published.
  published?: boolean
  published_at?: string | null
  published_by?: string | null
  // Manual-notification summary (admin view only).
  push_state?: SignalPushState
  // Entry-reached facts (published by the admin; the app sees only these three).
  entry_reached_at?: string | null
  entry_reached_price?: number | null
  entry_reached_extended?: boolean
  featured?: boolean
  pinned?: boolean
  display_order?: number
  category?: string | null
  image_url?: string | null
  start_at?: string | null
  end_at?: string | null
  tags?: string[]
  last_price?: number | null
  change_pct?: number | null
  entry_price?: number | null
  exit_price?: number | null
  stop_loss?: number | null
}

export interface SignalPushState {
  announced: boolean
  announced_at?: string | null
  update_sent_at?: string | null
  changed_since: boolean
}

// One detected entry hit (/api/entries/hits). Admin website only.
export interface EntryHit {
  id: string
  day: string
  kind: 'admin' | 'scanner'
  symbol: string
  signal_id?: string | null
  level?: number | null
  direction?: 'up' | 'down' | 'touch' | null
  detected_at: string
  exact_minute?: string | null
  price_at_detection?: number | null
  source?: string
  extended?: boolean
  late_start?: boolean
  status: 'new' | 'reviewed' | 'dismissed' | 'draft_created' | 'entry_reached_published'
  age_minutes: number | null
  price_now?: number | null
  now_extended_pct?: number | null
  stale: boolean
  stale_reasons: string[]
  signal_state?: 'Draft' | 'Published' | 'Hidden'
  entry_reached_live?: boolean
  draft_signal_id?: string | null
}

export interface EntryHitsResponse {
  hits: EntryHit[]
  stale_minutes: number
  market_open: boolean
  detection: { enabled: boolean; armed_admin: number; armed_scanner: number; last_hit?: { symbol: string; at: string } | null }
}

// Per-signal detection state (/api/entries/signal-states). Admin only.
export interface SignalEntryState {
  armed: boolean
  done: boolean
  direction?: 'up' | 'down' | null
  hit?: { id: string; status: string; detected_at: string; level?: number | null } | null
  entry_reached_published: boolean
  entry_reached_at?: string | null
}

// One row of the manual-send audit log (/api/push/status → audit).
export interface PushAuditEntry {
  id: string
  at: string
  admin?: string
  type: string
  key?: string
  status: 'sending' | 'done' | 'refused'
  audience?: number
  attempted?: number
  sent?: number
  failed?: number
  reason?: string
}

export interface PushStatus {
  configured: boolean
  devices: number
  signal_devices: number
  sends_today: number
  daily_cap: number
  duplicate_window_sec: number
  audit: PushAuditEntry[]
}

// One row of /api/exits — an exit call sent to the app's Alerts section.
// Only three values matter: stock, profit (negative = loss) and exit price.
export interface ExitCall {
  id: string
  symbol: string
  profit: number
  exit_price: number
  created_at: string
  added_by?: string | null
}

// One row of /api/stocks — the admin panel's search-as-you-type source.
export interface StockDirectoryEntry {
  symbol: string
  name: string
}

export interface LearnArticle {
  id: string
  title: string
  body: string
  category?: string | null
  published: boolean
  enabled?: boolean
  featured?: boolean
  pinned?: boolean
  display_order?: number
  image_url?: string | null
  icon?: string | null
  tone?: string | null
  start_at?: string | null
  end_at?: string | null
  tags?: string[]
  created_at: string
  updated_at: string
}

export interface InsightContent {
  id: string
  title: string
  body: string
  category?: string | null
  enabled: boolean
  featured?: boolean
  pinned?: boolean
  display_order?: number
  image_url?: string | null
  icon?: string | null
  tone?: string | null
  start_at?: string | null
  end_at?: string | null
  tags?: string[]
  created_at: string
  updated_at: string
}

// Exactly two spellings, matching data/app_weekly_report.py's VALID_OUTCOMES
// and main.py's POST /api/weekly-report validation — the backend rejects
// anything else with 400, so this union is the full set, not illustrative.
export type WeeklyReportOutcome = 'target' | 'stop_loss'

// Everything below `outcome` is optional (Phase 6 — trade-card redesign).
// Older reports simply have these absent/blank; the Flutter app falls back
// to the pre-Phase-6 plain layout whenever `pnl_amount` is missing.
export interface WeeklyReportStock {
  symbol: string
  profit_pct: number
  outcome: WeeklyReportOutcome
  name?: string
  bullish?: boolean
  trade_label?: string
  entry_price?: number | null
  exit_price?: number | null
  pnl_amount?: number | null
  date_of_recommendation?: string
  exit_date?: string
  duration_days?: number | null
}

export interface WeeklyReport {
  id: string
  week_start: string
  week_end: string
  stocks: WeeklyReportStock[]
  enabled?: boolean
  display_order?: number
  created_at: string
  updated_at: string
}

export type DebugStatus = 'signal' | 'watchlist' | 'none' | 'no_data' | 'error' | string

export interface BacktestDebugResult {
  symbol: string
  status: DebugStatus
  stage: string
  reason: string
  category?: string
  explanation?: string
  values: Record<string, unknown> & Partial<Signal>
}

export interface ScanState {
  job_id?: string
  status?: string
  scanning: boolean
  scan_time: string | null
  total_scanned: number
  total_attempted?: number
  signals: Signal[]
  watchlist_items: Signal[]
  backtest_results?: BacktestDebugResult[]
  error: string | null
  notice?: string | null          // non-error message, e.g. "Scan stopped"
  scan_waiting?: boolean         // scheduled scan is waiting for a running backtest
  saved_at?: string              // when this (saved) backtest result was produced
  partial?: boolean              // some stocks could not be fetched from Fyers
  debug?: {
    requested_date?: string
    resolved_date?: string
    window_start?: string
    runtime_seconds?: number
    status_counts?: Record<string, number>
    stage_counts?: Record<string, number>
    daily_valid?: number
    prepared?: number
    dropped_short?: number
    universe_total?: number
    no_data_symbols?: number
    weekly_not_rising?: number
    quality_filtered?: number
    weekly_valid?: number
    weekly_no_data?: number
    weekly_filtered?: number
    failed?: number
    no_data?: number
    recovered?: number
    persistent_recovered?: number
    persistent_retries?: number
    evaluation_errors?: Array<{ symbol: string; error: string }>
    debug_outputs?: Record<string, string>
    attempted?: number
    failed_symbols?: string[]
    short_history?: number
    stale?: number
    skipped?: Record<string, { status: string; category: string; detail: string }>
  }
}

// ── Live scan progress ───────────────────────────────────────────────────────
// Served by GET /api/scan/progress. Every number is a real counter fed by the
// scan loops (the same counts shown in the Railway logs) — nothing estimated.
export type ScanProgressStage = 'idle' | 'fetch' | 'retry' | 'analyse' | 'done' | 'error' | 'cancelled'

export interface ScanProgressInfo {
  kind: 'live' | 'backtest'
  run_id: number
  active: boolean
  stage: ScanProgressStage
  stage_label: string
  percent: number          // completed fraction of the CURRENT stage, 0-100
  stage_done: number
  stage_total: number
  total: number            // symbols in the universe
  processed: number
  valid: number
  to_retry: number         // still being retried (0 once the scan has finished)
  no_data: number          // Fyers has no history for these
  failed: number           // still failing after retries
  not_scanned: number      // no_data (+ failed once the scan has finished)
  recovered: number
  retry_pass: number       // 0 = no retry was needed
  analysed: number
  analyse_total: number
  target_date: string | null
  started_at: string | null
  finished_at: string | null
  elapsed_seconds: number
  error: string | null
  stopping?: boolean       // Stop pressed, the scan is winding down
  cancelled?: boolean      // the last run was stopped by the user
}

export interface ScanProgressResponse {
  live: ScanProgressInfo
  backtest: ScanProgressInfo
}
