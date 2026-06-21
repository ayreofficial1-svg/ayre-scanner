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

export interface ScanState {
  scanning: boolean
  scan_time: string | null
  total_scanned: number
  total_attempted?: number
  signals: Signal[]
  watchlist_items: Signal[]
  error: string | null
  debug?: {
    requested_date?: string
    resolved_date?: string
    window_start?: string
    runtime_seconds?: number
    status_counts?: Record<string, number>
    stage_counts?: Record<string, number>
  }
}
