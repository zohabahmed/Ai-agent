export interface OpenBBQuantMetrics {
  confluence_score: number;
  bias: "BULLISH" | "BEARISH" | "NEUTRAL" | string;
  hurst_exponent: number;
  market_character: "TRENDING" | "MEAN_REVERTING" | "RANDOM_WALK" | string;
  rolling_sharpe: number;
  sortino_ratio: number;
  omega_ratio: number;
  value_at_risk_95: number;
  cvar_95: number;
  kelly_optimal_fraction: number;
  volatility_zscore: number;
  volume_pressure: number;
  confidence_boost: number;
  allow_long: boolean;
  allow_short: boolean;
  rationale: string;
}

export interface TradingViewRatingMetrics {
  overall_rating: "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL" | string;
  rating_score: number;
  ma_rating: string;
  ma_score: number;
  ma_buy_count: number;
  ma_neutral_count: number;
  ma_sell_count: number;
  oscillator_rating: string;
  oscillator_score: number;
  osc_buy_count: number;
  osc_neutral_count: number;
  osc_sell_count: number;
  signals: Record<string, string>;
}

export interface DerivativesSentimentMetrics {
  symbol: string;
  source: string;
  funding_rate: number;
  funding_rate_pct: number;
  annualized_funding_pct: number;
  mark_price: number;
  index_price: number;
  basis_pct: number;
  open_interest_coins: number;
  open_interest_usd: number;
  long_account_pct: number;
  short_account_pct: number;
  long_short_ratio: number;
  taker_buy_sell_ratio: number;
  crowding_state: string;
  derivatives_bias: string;
  confidence_boost: number;
  allow_long: boolean;
  allow_short: boolean;
  rationale: string;
}

export interface EnrichedCandle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ema_20?: number;
  ema_50?: number;
  bb_upper?: number;
  bb_lower?: number;
  rsi_14?: number;
}

export interface MarketSymbolData {
  symbol: string;
  timeframe: string;
  ticker: Record<string, unknown> | null;
  openbb_quant: OpenBBQuantMetrics;
  tradingview_rating: TradingViewRatingMetrics;
  derivatives_sentiment: DerivativesSentimentMetrics;
  regime: {
    regime: string;
    confidence: number;
    adx: number;
    atr_pct: number;
    volatility_ratio: number;
    trend_slope: number;
  };
  candles: EnrichedCandle[];
}

export interface DashboardData {
  account: {
    id: string;
    name: string;
    exchange: string;
    market_type: "SPOT" | "FUTURES" | string;
    leverage: number;
    trading_mode: "PAPER" | "TESTNET" | "LIVE_READ_ONLY" | "LIVE_TRADING" | string;
    autonomy_mode: string;
    is_simulated: boolean;
    is_trading_active: boolean;
    emergency_stopped: boolean;
    live_trading_authorized: boolean;
  };
  overview: {
    initial_capital: number;
    day_start_equity?: number;
    day_start_date?: string | null;
    daily_pnl_pct?: number;
    current_daily_loss_pct?: number;
    current_equity: number;
    available_balance: number;
    locked_balance: number;
    open_position_value: number;
    realized_pnl: number;
    unrealized_pnl: number;
    total_trading_costs: number;
    net_return_pct: number;
    current_drawdown_pct: number;
  };
  survival: {
    state: string;
    kelly_mode: string;
    kelly_multiplier: number;
    max_risk_per_trade: number;
    max_daily_loss: number;
    max_drawdown: number;
    max_open_positions: number;
    max_single_position_pct?: number;
    max_same_side_positions?: number;
    open_positions_count: number;
    emergency_stopped: boolean;
  };
  market_tickers: Record<string, Record<string, unknown>>;
  strategies: Array<{
    name: string;
    display_name: string;
    category: string;
    status: string;
    enabled_in_registry: boolean;
    preferred_regimes: string[];
  }>;
  withdrawals: {
    realized_withdrawable_profit: number;
    reserve_capital: number;
    withdrawal_threshold: number;
  };
  pending_approvals_count: number;
  recent_risk_decisions: Array<{
    id: string;
    symbol: string;
    strategy: string;
    regime: string;
    decision: string;
    kelly_fraction: number;
    expected_value: number;
    rejection_reasons: string[];
    created_at: string;
  }>;
  recent_events: Array<{
    id: string;
    event_type: string;
    severity: string;
    component: string;
    message: string;
    created_at: string;
  }>;
  system: {
    database: string;
    cache: Record<string, unknown>;
    market_worker: Record<string, unknown> | null;
    trading_worker: Record<string, unknown> | null;
    monitoring_worker: Record<string, unknown> | null;
  };
}

export interface PortfolioData {
  account_id: string;
  trading_mode: string;
  market_type: "SPOT" | "FUTURES" | string;
  leverage: number;
  is_simulated: boolean;
  equity: {
    initial_capital: number;
    available_balance: number;
    locked_balance: number;
    open_position_value: number;
    total_equity: number;
    realized_pnl: number;
    unrealized_pnl: number;
    total_fees: number;
    net_return_pct: number;
    drawdown_pct: number;
  };
  profit: {
    realized_net_profit: number;
    unrealized_pnl: number;
    reserve_capital_required: number;
    withdrawable_profit: number;
    retained_growth_capital: number;
  };
  balances: Array<{
    asset: string;
    available: number;
    locked: number;
    total: number;
    reconciled_at: string;
  }>;
  open_positions: Array<{
    id: string;
    symbol: string;
    strategy_name: string;
    side: "LONG" | "SHORT" | string;
    market_type: "SPOT" | "FUTURES" | string;
    leverage: number;
    margin_used: number;
    quantity: number;
    entry_price: number;
    current_price: number;
    stop_loss: number | null;
    take_profit: number | null;
    unrealized_pnl: number;
    opened_at: string;
  }>;
  equity_curve: Array<{
    timestamp: string;
    total_equity: number;
    available_balance: number;
    open_position_value: number;
    realized_pnl: number;
    unrealized_pnl: number;
    drawdown_pct: number;
    is_simulated: boolean;
  }>;
}

export interface ExchangeStatusData {
  exchange_id: string;
  supported_exchanges?: string[];
  trading_mode: "PAPER" | "TESTNET" | "LIVE_READ_ONLY" | "LIVE_TRADING" | string;
  market_type: "SPOT" | "FUTURES";
  leverage: number;
  live_trading_authorized: boolean;
  credentials_configured: boolean;
  api_key_mask: string | null;
  sandbox: boolean;
  verified: boolean;
  last_verified_at: string | null;
  last_error: string | null;
  sync?: {
    verified: boolean;
    error?: string | null;
    available_quote: number;
    locked_quote: number;
    total_quote: number;
    non_zero_assets: Array<{
      asset: string;
      available: number;
      locked: number;
      total: number;
    }>;
  };
}

export interface TradesData {
  trades: Array<{
    id: string;
    symbol: string;
    side: string;
    trading_mode: string;
    strategy_name: string;
    market_regime: string;
    quantity: number;
    price: number;
    notional: number;
    fee: number;
    slippage_bps: number;
    realized_pnl: number | null;
    is_simulated: boolean;
    executed_at: string;
  }>;
  orders: Array<{
    id: string;
    client_order_id: string;
    exchange_order_id: string | null;
    symbol: string;
    side: string;
    order_type: string;
    status: string;
    requested_quantity: number;
    filled_quantity: number;
    average_fill_price: number | null;
    fee_paid: number;
    created_at: string;
  }>;
}

export interface StrategiesData {
  strategies: Array<{
    id: string;
    name: string;
    display_name: string;
    category: string;
    status: string;
    active_version?: string;
    parameters: Record<string, unknown>;
    preferred_regimes: string[];
    enabled_in_runtime?: boolean;
    enabled_in_registry?: boolean;
    realized_overall?: {
      trade_count: number;
      wins: number;
      losses: number;
      win_rate: number;
      avg_win_pct: number;
      avg_loss_pct: number;
      net_expectancy_pct: number;
    } | null;
  }>;
  realized_regime_stats?: Array<{
    strategy_name: string;
    regime: string;
    trade_count: number;
    wins: number;
    losses: number;
    win_rate: number;
    avg_win_pct: number;
    avg_loss_pct: number;
    net_expectancy_pct: number;
    auto_disabled: boolean;
  }>;
  recent_metrics: Array<{
    id: string;
    strategy_name: string;
    version: string;
    window_trades: number;
    win_rate: number;
    profit_factor: number;
    expectancy: number;
    sharpe_ratio: number;
    max_drawdown: number;
    recorded_at: string;
  }>;
}

export interface ApprovalsData {
  approvals: Array<{
    id: string;
    operation_type: string;
    status: string;
    requested_by: string;
    reviewed_by: string | null;
    reason: string;
    payload: Record<string, unknown>;
    review_comment: string | null;
    expires_at: string;
    created_at: string;
    reviewed_at: string | null;
  }>;
}

export interface WithdrawalsData {
  eligibility: {
    eligible: boolean;
    withdrawable_amount: number;
    reserve_capital_preserved: number;
    rejection_reason: string | null;
  };
  withdrawals: Array<{
    id: string;
    approval_id: string | null;
    asset: string;
    network: string;
    destination_address: string;
    requested_amount: number;
    network_fee: number;
    net_amount: number;
    status: string;
    tx_id: string | null;
    created_at: string;
    executed_at: string | null;
  }>;
  whitelist_addresses: Array<{
    id: string;
    label: string;
    exchange_or_wallet: string;
    asset: string;
    network: string;
    address: string;
    is_whitelisted: boolean;
  }>;
}

export interface ActiveTradeDetailed {
  id: string;
  symbol: string;
  exchange: string;
  trading_mode: string;
  is_simulated: boolean;
  market_type: "SPOT" | "FUTURES" | string;
  side: "LONG" | "SHORT" | string;
  leverage: number;
  margin_mode: string;
  strategy_name: string;
  quantity: number;
  entry_price: number;
  mark_price: number;
  breakeven_price: number;
  entry_notional_usd: number;
  current_notional_usd: number;
  initial_margin_usd: number;
  maintenance_margin_usd: number;
  maintenance_margin_rate_pct: number;
  margin_balance_usd: number;
  margin_ratio_pct: number;
  unrealized_pnl: number;
  gross_pnl: number;
  entry_fee_usd: number;
  estimated_exit_fee_usd: number;
  roi_pct: number;
  unleveraged_return_pct: number;
  liquidation_price: number;
  distance_to_liq_pct: number;
  stop_loss: number | null;
  take_profit: number | null;
  sl_order_id?: string | null;
  tp_order_id?: string | null;
  exchange_protected?: boolean;
  scaled_out?: boolean;
  exit_reason?: string | null;
  market_regime?: string | null;
  distance_to_sl_pct: number | null;
  distance_to_tp_pct: number | null;
  expected_tp_profit_usd: number | null;
  expected_tp_roi_pct: number | null;
  expected_sl_loss_usd: number | null;
  expected_sl_roi_pct: number | null;
  reward_risk_ratio: number | null;
  opened_at: string;
  duration_seconds: number;
  why_taken: {
    signal_reason: string;
    strategy_name: string;
    market_regime: string;
    signal_confidence_pct: number;
    regime_adx: number;
    regime_volatility_pct: number;
    kelly_and_risk: {
      decision: string;
      survival_state: string;
      win_probability_pct: number;
      ci_low_pct: number;
      net_ev_pct: number;
      full_kelly_pct: number;
      fractional_kelly_pct: number;
      approved_notional_usd: number;
      estimated_fees_usd: number;
      ai_explanation: string[];
    };
    openbb_quant: Record<string, unknown>;
    tradingview_rating: Record<string, unknown>;
    derivatives_sentiment: Record<string, unknown>;
    execution_audit: {
      order_id: string | null;
      client_order_id: string | null;
      exchange_order_id: string | null;
      trade_id: string | null;
      slippage_bps: number;
      fee_paid_usd: number;
      executed_at: string;
    };
  };
}

export interface ActiveTradesResponse {
  account_summary: {
    account_id: string;
    exchange: string;
    trading_mode: string;
    market_type: string;
    leverage: number;
    survival_state: string;
    is_trading_active: boolean;
    available_usdt: number;
    locked_usdt: number;
    active_positions_count: number;
    total_notional_usd: number;
    total_margin_used_usd: number;
    total_unrealized_pnl_usd: number;
    total_roi_pct: number;
    max_hold_minutes?: number;
    fast_book_profit_pct?: number;
  };
  active_trades: ActiveTradeDetailed[];
  recently_closed: Array<{
    id: string;
    symbol: string;
    side: string;
    market_type: string;
    leverage: number;
    strategy_name: string;
    market_regime?: string;
    exit_reason?: string;
    net_pnl_pct?: number | null;
    quantity: number;
    entry_price: number;
    exit_price: number;
    realized_pnl: number;
    opened_at: string;
    closed_at: string | null;
  }>;
}
