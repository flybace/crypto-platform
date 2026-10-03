export type User = {
  username: string;
  display_name: string;
  role: string;
};

export type PublicProxyChannelSettings = {
  configured: boolean;
  display: string | null;
  mode: 'direct' | 'proxy' | string;
};

export type PublicNetworkVenueSettings = {
  venue_id: 'binance' | 'okx' | 'bybit' | string;
  public_rest_base_url: string;
  public_ws_base_url: string;
  history_base_url: string;
  instruments_path: string;
  tickers_path: string;
  order_book_path: string;
  candles_path: string;
  time_path: string;
  http_proxy: PublicProxyChannelSettings;
  ws_proxy: PublicProxyChannelSettings;
};

export type PublicNetworkSettings = {
  schema_version: string;
  source: 'environment' | 'saved' | 'invalid_file' | string;
  updated_at: string | null;
  file_error: string | null;
  venues: PublicNetworkVenueSettings[];
  apply_policy: {
    backend: string;
    history_worker: string;
    scheduler: string;
    market_worker: string;
  };
  supported_proxy_schemes: string[];
  supported_endpoint_schemes: Record<string, string[]>;
  applied?: boolean;
  apply_errors?: string[];
};

export type PublicNetworkProbe = {
  venue_id: string;
  channel: 'HTTP' | 'WebSocket' | string;
  state: 'REACHABLE' | 'BLOCKED' | 'REJECTED' | string;
  http_status: number | null;
  latency_ms: number;
  used_proxy: boolean;
  endpoint: string;
  error: { kind: string; message: string } | null;
};

export type VenueStatus = {
  venue_id: string;
  state: 'CONNECTED' | 'DISCONNECTED' | 'DEGRADED' | 'STALE' | string;
  last_received_at: string | null;
  last_sequence: number | null;
  reason: string;
};

export type MarketOverview = {
  execution_mode: string;
  venues: VenueStatus[];
};

export type MarketPriceLevel = {
  price: string;
  quantity: string;
};

export type MarketSnapshot = {
  instrument_key: string;
  venue_id: string;
  native_symbol: string;
  exchange_timestamp: string;
  received_timestamp: string;
  sequence: number;
  bids: MarketPriceLevel[];
  asks: MarketPriceLevel[];
};

export type MarketSummaryQuote = {
  venue_id: string;
  venue_name: string;
  symbol: string;
  interval: string;
  dataset_id: string;
  close: string;
  change_pct: string;
  quote_volume: string;
  open_time: string;
  close_time: string;
  row_count: number;
  data_start_at: string;
  data_end_at: string;
};

export type MarketSummarySpread = {
  symbol: string;
  interval: string;
  buy_venue_id: string;
  buy_venue_name: string;
  sell_venue_id: string;
  sell_venue_name: string;
  buy_close: string;
  sell_close: string;
  spread_pct: string;
  venue_count: number;
  research_only: boolean;
};

export type MarketSummary = {
  interval: string;
  as_of: string | null;
  execution_mode: string;
  market_mode: string;
  research_only: boolean;
  coverage: {
    verified_dataset_count: number;
    expected_dataset_count: number;
    row_count: number;
    coverage_ratio_pct: string;
    quality_blocked_dataset_count: number;
  };
  venue_count: number;
  venues: Array<{ venue_id: string; venue_name: string; dataset_count: number; symbols: string[]; latest_at: string | null }>;
  symbols: string[];
  quotes: MarketSummaryQuote[];
  top_gainers: MarketSummaryQuote[];
  top_losers: MarketSummaryQuote[];
  spreads: MarketSummarySpread[];
};

export type HistoricalSpreadObservation = {
  timestamp: string;
  buy_close: string;
  sell_close: string;
  gross_spread_pct: string;
  net_spread_pct: string;
  net_spread_bps: string;
};

export type HistoricalSpreadResearch = {
  kind: 'historical_spread_research' | string;
  status: string;
  research_only: boolean;
  symbol: string;
  interval: string;
  buy_venue_id: string;
  sell_venue_id: string;
  buy_dataset: HistoryDataset;
  sell_dataset: HistoryDataset;
  requested_start_at: string | null;
  requested_end_at: string | null;
  aligned_candle_count: number;
  alignment_ratio_pct: string;
  skipped_price_count: number;
  start_at: string;
  end_at: string;
  positive_count: number;
  opportunity_count: number;
  opportunity_ratio_pct: string;
  average_gross_spread_pct: string;
  average_net_spread_pct: string;
  max_net_spread_pct: string;
  min_net_spread_pct: string;
  cost_model: {
    fee_bps_per_leg: string;
    slippage_bps_per_leg: string;
    total_cost_bps: string;
    min_net_spread_bps: string;
    transfer_cost_included: boolean;
    inventory_cost_included: boolean;
  };
  top_observations: HistoricalSpreadObservation[];
  recent_observations: HistoricalSpreadObservation[];
  note: string;
};

export type OpportunityRecord = {
  record_id: string;
  state: 'VALIDATED' | 'BLOCKED' | 'EXPIRED' | string;
  blocking_reason: string;
  symbol: string;
  buy_venue_id: string;
  sell_venue_id: string;
  instrument_key: string;
  quantity: string;
  buy_price: string;
  sell_price: string;
  gross_edge_quote: string;
  fees_quote: string;
  slippage_quote: string;
  other_costs_quote: string;
  net_edge_quote: string;
  created_at: string;
  expires_at: string;
  observed_at: string;
  alerted: boolean;
  notification_id: string | null;
  note: string;
};

export type HistoryDataset = {
  dataset_id: string;
  venue_id: string;
  native_symbol: string;
  market_type: string;
  instrument_key: string;
  data_level: string;
  interval: string;
  start_at: string;
  end_at: string;
  file_format: string;
  source: string;
  row_count: number;
  gap_count: number;
  duplicate_count: number;
  content_sha256: string;
  storage_key: string;
};

export type HistoryCoverage = {
  dataset_count: number;
  row_count: number;
  by_venue: Record<string, { datasets: number; rows: number }>;
  datasets: HistoryDataset[];
  expected_dataset_count: number;
  verified_dataset_count: number;
  blocked_dataset_count: number;
  failed_dataset_count: number;
  in_progress_dataset_count: number;
  interrupted_dataset_count: number;
  missing_dataset_count: number;
  coverage_ratio_pct: string;
  matrix: HistoryCoverageEntry[];
  metadata?: HistoryMetadataStatus;
};

export type HistoryMetadataStatus = {
  enabled: boolean;
  required: boolean;
  ok: boolean;
  status: string;
  backend: string;
  dataset_count: number;
  updated_at: string | null;
  message?: string;
};

export type HistoryArchiveItem = {
  dataset_id: string;
  storage_key: string;
  archive_key: string;
  state: 'ARCHIVED' | 'MISSING' | 'STALE' | 'UNAVAILABLE' | string;
  row_count: number;
  size_bytes: number;
};

export type HistoryArchiveStatus = {
  status: 'READY' | 'PARTIAL' | 'MISSING' | 'EMPTY' | 'UNAVAILABLE' | string;
  available: boolean;
  error: string | null;
  archive_root: string;
  dataset_count: number;
  archived_count: number;
  stale_count: number;
  missing_count: number;
  failed_count?: number;
  items: HistoryArchiveItem[];
};

export type HistoryCoverageEntry = {
  venue_id: string;
  symbol: string;
  interval: string;
  status: 'VERIFIED' | 'BLOCKED' | 'MISSING' | string;
  dataset_id: string | null;
  row_count: number;
  start_at: string | null;
  end_at: string | null;
  gap_count: number;
  duplicate_count: number;
  source: string | null;
  reason: string;
};

export type HistoryJobItem = {
  venue_id: string;
  instrument_key: string;
  native_symbol: string;
  interval: string;
  start_at: string;
  end_at: string;
  status: string;
  fetched_rows?: number;
  dataset?: Pick<HistoryDataset, 'dataset_id' | 'row_count' | 'gap_count' | 'duplicate_count' | 'storage_key'>;
  error?: { kind: string; status_code?: number | null; retry_after_seconds?: number | null; message: string };
};

export type HistoryJob = {
  job_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  total: number;
  completed: number;
  blocked: number;
  failed: number;
  interrupted?: number;
  cancelled?: number;
  request_fingerprint?: string;
  deduplicated?: boolean;
  items: HistoryJobItem[];
};

export type HistorySyncPlanItem = {
  venue_id: string;
  symbol: string;
  native_symbol: string;
  instrument_key: string;
  interval: string;
  status: string;
  download: boolean;
  start_at: string;
  end_at: string;
  existing: { dataset_id: string; row_count: number; start_at: string; end_at: string; gap_count: number; duplicate_count: number } | null;
  reason: string;
};

export type HistorySyncPlan = {
  plan_id: string;
  mode: 'incremental' | 'backfill' | string;
  created_at: string;
  end_at: string;
  lookback_days: number;
  target_count: number;
  download_count: number;
  up_to_date_count: number;
  verified_dataset_count: number;
  verified_row_count: number;
  missing_count: number;
  quality_blocked_count: number;
  status: 'UP_TO_DATE' | 'ACTIONABLE' | string;
  last_job_id: string | null;
  items: HistorySyncPlanItem[];
};

export type HistorySyncResponse = {
  plan: HistorySyncPlan;
  job: HistoryJob | null;
  status: string;
};

export type HistorySchedulerStatus = {
  version: number;
  automatic: boolean;
  interval_seconds: number;
  status: string;
  last_run_at: string | null;
  last_result: string | null;
  last_error: { kind?: string; message?: string } | null;
  last_plan_id: string | null;
  last_job_id: string | null;
  last_job_status: string | null;
  last_failure_job_id: string | null;
  last_failure_status: string | null;
  consecutive_failures: number;
  next_attempt_at: string | null;
  updated_at: string | null;
};

export type HistoryTaskResponse = {
  task: PlatformTask;
  detail: HistoryJob;
};

export type HistoryCandle = {
  open_time: string;
  close_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  quote_volume: string;
  trade_count: number | null;
};

export type HistoryCandleResponse = {
  dataset: HistoryDataset;
  items: HistoryCandle[];
  count: number;
  total_count: number;
  truncated: boolean;
};

export type MarketHistorySeries = {
  venue_id: string;
  status: 'loading' | 'ready' | 'missing' | 'error';
  items: HistoryCandle[];
  message?: string;
};

export type MarketInstrument = {
  instrument_key: string;
  venue_id: string;
  market_type: string;
  base_asset: string;
  quote_asset: string;
  native_symbol: string;
  canonical_symbol: string;
  status: string;
  price_tick: string;
  quantity_step: string;
  min_quantity: string;
  min_notional: string;
  base_asset_precision?: number | null;
  quote_asset_precision?: number | null;
  base_precision?: string | null;
  quote_precision?: string | null;
};

export type MarketInstrumentCatalog = {
  venue_id: string;
  venue_name: string;
  status: 'LIVE' | 'CACHED' | 'BLOCKED' | string;
  source: string;
  quote_asset: string;
  fetched_at: string | null;
  cache_ttl_seconds: number;
  total_count: number;
  returned_count: number;
  truncated: boolean;
  items: MarketInstrument[];
  last_error: { kind: string; status_code: number | null; retry_after_seconds: number | null } | null;
};

export type PublicTickerMarket = {
  venue_id: string;
  venue_name: string;
  symbol: string;
  native_symbol: string;
  base_asset: string;
  quote_asset: string;
  last_price: string;
  open_24h: string | null;
  high_24h: string;
  low_24h: string;
  change_pct: string;
  volume_24h: string;
  quote_volume_24h: string;
  range_position_pct: string;
  trend: 'up' | 'down' | 'flat' | string;
  timestamp: string | null;
};

export type PublicTickerItem = {
  symbol: string;
  base_asset: string;
  quote_asset: string;
  markets: Record<string, PublicTickerMarket | null>;
  available_venues: string[];
  best_venue_id: string | null;
  best_price: string | null;
  highest_venue_id: string | null;
  highest_price: string | null;
  spread_pct: string;
  change_pct: string;
  quote_volume_24h: string;
  trend: 'up' | 'down' | 'flat' | string;
  timestamp: string | null;
};

export type PublicTickerResponse = {
  kind: string;
  quote_asset: string;
  search: string;
  as_of: string | null;
  refresh_seconds: number;
  research_only: boolean;
  total_count: number;
  returned_count: number;
  offset: number;
  limit: number;
  truncated: boolean;
  sort_by: 'volume' | 'change' | 'spread' | 'symbol' | string;
  venues: Array<{
    venue_id: string;
    venue_name: string;
    status: string;
    fetched_at: string | null;
    total_count: number;
    last_error: { kind: string; status_code: number | null; retry_after_seconds: number | null } | null;
  }>;
  items: PublicTickerItem[];
};

export type StrategyDefinition = {
  strategy_id: string;
  name: string;
  description: string;
  modes: string[];
  version?: string;
  parameter_schema?: Record<string, unknown>[];
  supports_parameter_search?: boolean;
  data_level?: string;
  enabled?: boolean;
  default_parameters?: Record<string, string | number | null>;
  management_note?: string;
};

export type StrategyManagement = {
  strategy_id: string;
  name: string;
  description: string;
  version: string;
  available_modes: string[];
  modes: string[];
  enabled: boolean;
  default_parameters: Record<string, string | number | null>;
  note: string;
  updated_at: string | null;
  source: string;
};

export type StrategyPackage = {
  package_id: string;
  name: string;
  version: string;
  strategy_ids: string[];
  source_type: string;
  entrypoint: string | null;
  status: string;
  runnable: boolean;
  modes?: string[];
  data_level?: string;
  note?: string;
  execution_boundary?: string;
  updated_at?: string | null;
};

export type StrategyPackageSummary = {
  package_count: number;
  builtin_count: number;
  custom_count: number;
  runnable_count: number;
  items: StrategyPackage[];
  execution_boundary: string;
};

export type StrategyMatrix = {
  matrix_id: string;
  name: string;
  strategy_ids: string[];
  dataset_ids: string[];
  interval: string;
  base_config: Record<string, string | number>;
  parameter_sets: Record<string, Record<string, unknown>>;
  status: string;
  last_run: StrategyMatrixRun | null;
  created_at: string;
  updated_at: string;
};

export type StrategyMatrixRunItem = {
  strategy_id: string;
  dataset_id: string;
  status: string;
  total_return_pct?: string;
  max_drawdown_pct?: string;
  fees_quote?: string;
  orders?: number;
  round_trips?: number;
  win_rate_pct?: string;
  final_equity?: string;
  parameters?: Record<string, unknown>;
  reason?: string;
};

export type StrategyMatrixRun = {
  run_id: string;
  status: string;
  created_at: string;
  item_count: number;
  completed_count: number;
  blocked_count: number;
  items: StrategyMatrixRunItem[];
  best: StrategyMatrixRunItem | null;
  research_only: boolean;
};

export type StrategyMatrixSummary = {
  matrix_count: number;
  run_count: number;
  completed_run_count: number;
  items: StrategyMatrix[];
};

export type NewsAdvice = {
  symbol: string;
  action: string;
  action_text: string;
  urgency: string;
  urgency_text: string;
  suggested_duration_hours: number;
  position_guidance: string;
  strategy_notes: Record<string, string>;
  reason: string;
  evidence: string[];
  impact_score: number;
  heat: number;
  published_at: string;
  title: string;
  topics: string[];
  sentiment: string;
  sources: string[];
};

export type NewsRankingItem = {
  symbol: string;
  score: number;
  direction: 'positive' | 'neutral' | 'risk' | string;
  heat: number;
  positive: number;
  risk: number;
  top_title: string;
  top_impact: number;
};

export type NewsEvent = {
  event_id: string;
  event_key: string;
  title: string;
  summary: string;
  source: string;
  source_url: string | null;
  source_type: string;
  published_at: string;
  created_at: string;
  updated_at: string;
  symbols: string[];
  topics: string[];
  sentiment: 'positive' | 'neutral' | 'risk' | string;
  risk_level: 'low' | 'medium' | 'high' | string;
  impact_score: string;
};

export type NewsSummary = {
  module: string;
  status: string;
  event_count: number;
  returned_count: number;
  latest: NewsEvent | null;
  sentiment_counts: Record<string, number>;
  research_only: boolean;
  execution_eligible: boolean;
  source_mode: string;
  note: string;
};

export type NewsResonanceItem = {
  symbol: string;
  venue_id: string;
  dataset_id: string;
  close: string;
  change_pct: string;
  news_score: string;
  state: string;
  action: string;
  risk_level: string;
  event_ids: string[];
  event_titles: string[];
  research_only: boolean;
  execution_eligible: boolean;
};

export type NewsResonance = {
  module: string;
  status: string;
  interval: string;
  as_of: string | null;
  events_considered: number;
  items: NewsResonanceItem[];
  count: number;
  research_only: boolean;
  execution_eligible: boolean;
  note: string;
};

export type BacktestRun = {
  run_id: string;
  dataset_id: string;
  strategy_id: string;
  interval: string;
  candle_count: number;
  start_at: string;
  end_at: string;
  initial_equity: string;
  final_equity: string;
  total_return_pct: string;
  max_drawdown_pct: string;
  max_drawdown_quote: string;
  fees_quote: string;
  orders: number;
  filled_orders: number;
  round_trips: number;
  winning_round_trips: number;
  win_rate_pct: string;
  equity_curve: { timestamp: string; equity: string }[];
  trade_log: {
    side: string;
    timestamp: string;
    price: string;
    quantity: string;
    fee_quote: string;
    reason: string;
    pnl_quote: string;
  }[];
  status: string;
  created_at: string;
  updated_at: string;
  dataset: HistoryDataset;
  parameters: Record<string, string | number | null>;
  strategy_version?: string;
  data_level?: string;
};

export type BacktestSummary = {
  run_count: number;
  completed_count: number;
  strategy_count: number;
  trade_count: number;
  average_return_pct: string;
  best_run: BacktestRun | null;
  latest_run: BacktestRun | null;
};

export type PaperAutomation = {
  enabled: boolean;
  venue_id: string;
  symbol: string;
  interval: string;
  strategy_id: string;
  initial_quote: string;
  initial_base: string;
  fee_bps: string;
  slippage_bps: string;
  fast_window: number;
  slow_window: number;
  allocation_ratio: string;
  momentum_threshold_pct: string;
  strategy_parameters: Record<string, string | number | null>;
  last_run_id: string | null;
  updated_at: string | null;
};

export type PaperLiveTrade = {
  candle_time: string;
  signal: string | null;
  side: string;
  quantity: string;
  order_id: string | null;
  filled_price: string;
  created_at: string;
};

export type PaperLiveConfig = {
  enabled: boolean;
  venue_id: string;
  symbol: string;
  interval: string;
  strategy_id: string;
  strategy_parameters: Record<string, string | number | null>;
  allocation_ratio: string;
  max_position_ratio: string;
  stop_loss_pct: string;
  daily_max_loss_pct: string;
  last_candle_time: string | null;
  last_signal: string | null;
  last_tick_at: string | null;
  last_order_id: string | null;
  trade_count: number;
  entry_price: string | null;
  last_risk_event: string | null;
  updated_at: string | null;
  recent_trades: PaperLiveTrade[];
};

export type PaperLiveInstance = PaperLiveConfig & {
  instance_id: string;
};

export type PaperFollowConfig = {
  enabled: boolean;
  interval_seconds: number;
  alert_min_return_pct: string;
  alert_max_drawdown_pct: string;
  alert_underperform_pct: string;
  last_follow_at: string | null;
  last_dispatched_at: string | null;
  last_follow_run_id: string | null;
  updated_at: string | null;
};

export type PaperFollowSnapshot = {
  at: string;
  run_id: string;
  strategy_run_id: string;
  benchmark_run_id: string;
  strategy_id: string;
  venue_id: string;
  symbol: string;
  interval: string;
  dataset_id: string;
  start_at: string;
  end_at: string;
  candle_count: number;
  strategy_return_pct: string;
  market_return_pct: string;
  excess_return_pct: string;
  max_drawdown_pct: string;
  orders: number;
  win_rate_pct: string;
  alerts: string[];
  strategy_parameters?: Record<string, string | number | null>;
};

export type PaperFollowState = {
  config: PaperFollowConfig;
  snapshots: PaperFollowSnapshot[];
  latest: PaperFollowSnapshot | null;
};

export type PoolMember = {
  dataset_id: string;
  venue_id: string;
  symbol: string;
  native_symbol: string;
  interval: string;
  row_count: number;
  start_at: string;
  end_at: string;
  latest_at: string;
  quality_passed: boolean;
  quality_status: string;
  coverage_status: string;
  venue_count: number;
  storage_key: string;
};

export type CoinPool = {
  pool_id: string;
  name: string;
  kind: 'research' | 'backtest' | 'paper' | string;
  description: string;
  interval: string | null;
  venue_ids: string[];
  symbols: string[];
  dataset_count: number;
  member_count: number;
  complete_symbol_count: number;
  members: PoolMember[];
  updated_at: string;
};

export type ScreeningItem = {
  dataset_id: string;
  venue_id: string;
  symbol: string;
  interval: string;
  row_count: number;
  start_at: string;
  end_at: string;
  last_close: string;
  period_return_pct: string;
  momentum_pct: string;
  volatility_pct: string;
  max_drawdown_pct: string;
  average_quote_volume: string;
  score: string;
  passed: boolean;
  reasons?: string[];
};

export type ScreeningRun = {
  run_id: string;
  pool_id: string;
  interval: string;
  lookback: number;
  filters: Record<string, string | null>;
  dataset_count: number;
  blocked_dataset_count: number;
  candidate_count: number;
  items: ScreeningItem[];
  created_at: string;
  status: string;
};

export type StrategyIncubatorStage = {
  key: string;
  label: string;
  summary: string;
  progress: number;
  next_action: string;
};

export type StrategyIncubatorCandidate = {
  candidate_id: string;
  dataset_id: string;
  venue_id: string;
  symbol: string;
  interval: string;
  score: string;
  period_return_pct: string;
  momentum_pct: string;
  volatility_pct: string;
  max_drawdown_pct: string;
  average_quote_volume: string;
  reason: string;
  source_run_id: string;
  first_seen_at?: string;
  updated_at: string;
};

export type StrategyIncubatorPool = {
  pool_id: string;
  name: string;
  description: string;
  kind: string;
  source_type: string;
  source_pool_id: string;
  source_ref: string;
  strategy_id: string;
  latest_screen_run_id: string | null;
  source_screen_run_ids: string[];
  candidate_count: number;
  dataset_count: number;
  symbol_count: number;
  venue_ids: string[];
  symbols: string[];
  items: StrategyIncubatorCandidate[];
  stage: StrategyIncubatorStage;
  created_at: string;
  updated_at: string;
  research_only: boolean;
};

export type IncubatorScreen = {
  run_id: string;
  pool_id: string;
  interval: string;
  lookback: number;
  status: string;
  candidate_count: number;
  created_at: string;
  target_pool_id: string;
  target_pool_name: string;
  incubator_item_count: number;
  existing_candidate_count: number;
  new_candidate_count: number;
  already_in_incubator: boolean;
  incubator_status: string;
};

export type StrategyIncubatorSnapshot = {
  items: StrategyIncubatorPool[];
  count: number;
  recent_screens: IncubatorScreen[];
  summary: {
    incubator_total: number;
    incubator_items: number;
    ready_for_retest: number;
    collecting: number;
    stage_thresholds: Record<string, number>;
  };
};

export type PaperPosition = { asset: string; quantity: string; mark_price: string | null; value_quote: string };
export type PaperAccountSummary = {
  venue_id: string;
  account_id: string;
  quote_asset: string;
  balances: Record<string, string>;
  positions: PaperPosition[];
  equity_quote: string;
  order_count: number;
  strategy_run_count: number;
};
export type PaperSummary = {
  account_id: string;
  primary_venue: string;
  quote_asset: string;
  balances: Record<string, string>;
  positions: PaperPosition[];
  equity_quote: string;
  accounts: PaperAccountSummary[];
  aggregate_balances: Record<string, string>;
  aggregate_positions: PaperPosition[];
  aggregate_equity_quote: string;
  venue_count: number;
  fee_bps: string;
  order_count: number;
  strategy_run_count?: number;
  execution_mode: string;
  real_execution_mode: string;
};

export type PaperOrder = {
  order_id: string;
  request_id: string;
  dataset_id: string;
  venue_id: string;
  symbol: string;
  side: string;
  order_type: string;
  status: string;
  quantity: string;
  filled_quantity: string;
  remaining_quantity: string;
  average_price: string | null;
  fee_quote: string;
  mark_price: string;
  reason: string | null;
  created_at: string;
  balances: Record<string, string>;
};

export type RiskGate = { key: string; label: string; passed: boolean; detail: string };
export type RiskSummary = {
  execution_mode: string;
  real_orders_allowed: boolean;
  state: string;
  policy: Record<string, any>;
  blocking_reasons: string[];
  gates: RiskGate[];
  next_action: string;
  updated_at: string;
};

export type PlatformTask = {
  task_id: string;
  kind: string;
  status: string;
  created_at: string;
  updated_at: string;
  progress: Record<string, string | number>;
  label: string;
  source_id?: string;
  detail_url?: string;
  cancellable?: boolean;
  retryable?: boolean;
};

export type QueuedTaskResponse = {
  queued: true;
  task_id: string;
  run_id?: string | null;
  kind?: string;
  status?: string;
  deduplicated?: boolean;
  task?: PlatformTask;
};

export type TaskLedgerRecord = {
  task_id: string;
  kind: string;
  title: string;
  status: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  error: Record<string, unknown>;
  progress: Record<string, string | number>;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  retry_of: string | null;
  cancel_requested: boolean;
  version: number;
};

export type TaskDetailResponse = {
  task: PlatformTask;
  detail: unknown;
  ledger: TaskLedgerRecord | null;
  events: Array<{
    event_id: number;
    event_type: string;
    status: string | null;
    message: string;
    payload: unknown;
    created_at: string;
  }>;
};

export type RuntimePlanStep = {
  id: string;
  title: string;
  window: string;
  owner: string;
  action: string;
  implementation: string;
  status: string;
  evidence: Record<string, string | number | boolean | null | undefined>;
};

export type RuntimePlan = {
  module: string;
  status: string;
  market_mode: string;
  phase: string;
  phase_label: string;
  now: string;
  execution_mode: string;
  activity: { title: string; detail: string; tone: string };
  steps: RuntimePlanStep[];
  current_step: RuntimePlanStep | null;
  next_step: RuntimePlanStep | null;
  summary: {
    verified_dataset_count: number;
    expected_dataset_count: number;
    row_count: number;
    coverage_ratio_pct: string;
    enabled_strategy_count: number;
    backtest_count: number;
    research_count: number;
    paper_order_count: number;
    paper_strategy_run_count: number;
    strategy_package_count?: number;
    strategy_matrix_count?: number;
  };
  checkpoints: {
    latest_backtest: Record<string, unknown> | null;
    latest_research: Record<string, unknown> | null;
    paper_last_run_id: string | null;
    paper_automation_enabled: boolean;
    risk_state: string;
    risk_blocking_reasons: string[];
  };
  risk: RiskSummary;
  paper: {
    equity_quote: string;
    order_count: number;
    strategy_run_count: number;
    execution_mode: string;
    real_execution_mode: string;
  };
  tasks: { total: number; active: number; failed: number; completed: number };
  recent_tasks: PlatformTask[];
  capabilities: {
    read_only: boolean;
    read_only_count: number;
    write_count: number;
    blocked_action_count: number;
    catalog_path: string;
  };
  strategy_orchestration?: {
    packages: StrategyPackageSummary;
    matrices: StrategyMatrixSummary;
    custom_execution_enabled: boolean;
    research_only: boolean;
  };
};

export type AdviceCandidate = {
  rank: number;
  symbol: string;
  name: string;
  venue_id: string | null;
  dataset_id: string | null;
  recommendation_score: string;
  recommendation_grade: 'focus' | 'watch' | 'avoid' | string;
  recommendation_label: string;
  risk_level: 'low' | 'medium' | 'high' | string;
  change_pct?: string | null;
  close?: string | null;
  quote_volume?: string | null;
  spread_pct?: string | null;
  spread_buy_venue_id?: string | null;
  spread_sell_venue_id?: string | null;
  reason: string;
  next_action: string;
  source: string;
  research_only: boolean;
  execution_eligible: boolean;
};

export type AdviceSummary = {
  module: string;
  status: string;
  interval: string;
  latest: { snapshot_id: string | null; title: string; created_at: string | null } | null;
  total: number;
  focus: number;
  watch: number;
  avoid: number;
  risk_counts: Record<string, number>;
  research_only: boolean;
  execution_eligible: boolean;
  note: string;
};

export type AssistantAction = {
  id: string;
  method: string;
  path: string;
  scope: string;
  description: string;
  risk_level: string;
  enabled: boolean;
};

export type AssistantSummary = {
  module: string;
  status: string;
  mode: string;
  actions: number;
  invocations: number;
  last_invocations: Array<Record<string, unknown>>;
  write_actions: string[];
  blocked_actions: Array<{ id: string; risk_level: string; reason: string }>;
};

export type NotificationItem = {
  id: string;
  title: string;
  message: string;
  event_type: string;
  severity: 'info' | 'warning' | 'critical' | string;
  action_link: string | null;
  read: boolean;
  created_at: string;
};

export type NotificationSummary = {
  module: string;
  status: string;
  config: { enabled: boolean; in_app_enabled: boolean; system_sound_enabled: boolean; event_preferences: Record<string, unknown> };
  readiness: { enabled: boolean; in_app_ready: boolean; external_channels: string; message: string };
  unread_count: number;
  items: NotificationItem[];
  count: number;
};

export type SystemServiceStatus = { name: string; status: string; ok: boolean; message: string };

export type SystemStatus = {
  module: string;
  status: string;
  checked_at: string;
  runtime: { mode: string; market_mode: string; version: string; execution_mode: string };
  services: SystemServiceStatus[];
  data: { status: string; coverage: HistoryCoverage; market: MarketSummary };
  strategy: { strategy_count: number; enabled_count: number };
  paper: PaperSummary;
  risk: RiskSummary;
  readiness: {
    overall: { status: string; research_ready: boolean; real_execution_allowed: boolean };
    checks: Array<{ key: string; label: string; passed: boolean; detail: string }>;
    next_actions: string[];
  };
};
