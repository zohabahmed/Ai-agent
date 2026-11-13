# Autonomous Survival-First Crypto Trading Agent

A modular, survival-first autonomous cryptocurrency trading intelligence platform built with **FastAPI**, **Python 3.12+**, **SQLAlchemy 2.0**, **Alembic**, **PostgreSQL**, **Redis**, **CCXT**, **NumPy/Pandas/SciPy/Scikit-learn**, and **Next.js / React / TypeScript / Tailwind CSS / Recharts**.

## Core Philosophy

1. **Survival First** — Independent `SurvivalController` has final authority over every order and cannot be overridden by strategies, AI advisors, or adaptive engines. Halts automatically on API/DB outage, stale feeds, unverified balances, `-1.0%` UTC start-of-day equity drop, or `-5.0%` peak drawdown.
2. **Capital Preservation & Realistic Exchange Rules Second** — `PAPER` mode enforces real exchange contract floors (`PAPER_ENFORCE_REAL_EXCHANGE_RULES=true`: `$5.00+` on Binance/Bybit, `$1.00+` on MEXC/KuCoin Futures/Gate). If calculated risk-budgeted order sizes fall below venue minimums, capital stays safely idle rather than simulating unrealistic micro fills. Recommended validation capital: `$200–$1,000`.
3. **Statistical Edge & Realized Payoff Third** — Bayesian Beta-Binomial probability estimation (Wilson lower bound), separated historical out-of-sample validation using unified live exit rules, and fee/spread/slippage/funding-adjusted Expected Value (`EV = pW - (1-p)L - C`) driven by realized net win/loss returns (with automatic strategy-regime disable after `>= 50` closed trades with negative net expectancy).
4. **Fractional Kelly Sizing & Diversification Fourth** — Default `NORMAL` mode (`KELLY_MULTIPLIER = 0.20`, `MAX_RISK_PER_TRADE = 0.015`, `MAX_DAILY_LOSS = 0.01` vs UTC `00:00` equity snapshot, `MAX_DRAWDOWN = 0.05`, `MAX_OPEN_POSITIONS = 3`, `MAX_LEVERAGE = 1` isolated USD-M Futures default, single-position concentration cap `40%`, max `2` same-side correlated positions / `65%` net directional exposure).
5. **Exchange-Protected Unified Exits Fifth** — ATR-scaled stops (`0.8%–2.5%`), minimum `2.0R` targets, exchange-side `reduceOnly` `STOP_MARKET` & `TAKE_PROFIT_MARKET` orders placed at entry and updated as trails ratchet, `50%` partial scale-out at `1.0R` moving stop to breakeven+fees, `1.2x ATR` runner trailing stop, `240m` trend / `60m` mean-reversion max hold, and `1h` HTF EMA + `BTC/USDT` regime + derivatives crowding entry gates.
6. **Adaptive Governance Sixth** — CUSUM, Page-Hinkley, Kolmogorov-Smirnov distribution-shift detection, walk-forward out-of-sample validation, versioned strategy switching, and instant rollback.

> **Important Risk Disclaimer:** Sound execution geometry, exchange-side stops, and separated validation are engineering prerequisites—they do **not** guarantee trading profits. Run extended realistic `PAPER` validation (`$200–$1,000`, `100+` trades) and verify positive net expectancy after all trading costs before arming `LIVE_TRADING`.

---

## Operating Modes

- `BACKTEST`: Reproducible historical bar-by-bar simulation with zero look-ahead bias.
- `PAPER` *(Default)*: Realistic paper simulator (bid/ask spread, order-book slippage, partial fills, maker/taker fees). Simulated results are strictly tagged `is_simulated = true`.
- `TESTNET`: Exchange sandbox/testnet execution where supported.
- `LIVE_READ_ONLY`: Real account monitoring without order submission permissions.
- `LIVE_TRADING`: Disabled by default; requires explicit human confirmation, verified config, and separate live credentials without withdrawal permissions.

---

## Quick Start (Local Development)

### 1. Environment Configuration
```bash
cp .env.example .env
```
Edit `.env` to set strong `SECRET_KEY` and `ADMIN_PASSWORD`. Never commit `.env` or live exchange keys.

### 2. Backend Setup
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
```

Initialize default `PAPER` account, admin user, and strategy registry:
```bash
python ../scripts/initialize_db.py
python ../scripts/seed_market_data.py
```

Run FastAPI server:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run automated test suite:
```bash
pytest -v
```

### 3. Frontend Dashboard
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:3000` and authenticate with your admin credentials.

---

## Docker Compose Production Deployment (PostgreSQL + Redis + Workers + Nginx)

```bash
cd deployment
docker compose up -d --build
```
Services included:
- `postgres`: PostgreSQL 16 database
- `redis`: Redis 7 cache and worker coordination
- `backend`: FastAPI API server (`:8000`)
- `market_worker`, `trading_worker`, `monitoring_worker`: Continuous background workers
- `frontend`: Next.js dashboard (`:3000`)
- `nginx`: Reverse proxy (`:80`) with rate limiting and security headers

Run database migrations inside container:
```bash
docker compose exec backend alembic upgrade head
```

---

## Human-in-the-Loop Controls (Hybrid Autonomy)

Automatic: market data collection, regime classification, multi-timeframe (`1h` + `15m`/`5m`) & BTC/derivatives confluence checks, separated OOS EV + Kelly sizing, unsafe trade rejection, exchange-protected isolated Futures execution (`STOP_MARKET`/`TAKE_PROFIT_MARKET` reduce-only orders + `1R` scale-out & `1.2x ATR` runner trail), and deterioration detection.

Explicit Human Approval Required:
- Activating `LIVE_TRADING` for the first time
- Increasing risk limits or leverage
- Introducing new live strategies
- Verifying withdrawal destination addresses
- Executing withdrawals (`WITHDRAWAL_APPROVAL = REQUIRED`)
