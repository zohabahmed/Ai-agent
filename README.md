# Autonomous Survival-First Crypto Trading Agent

A modular, survival-first autonomous cryptocurrency trading intelligence platform built with **FastAPI**, **Python 3.12+**, **SQLAlchemy 2.0**, **Alembic**, **PostgreSQL**, **Redis**, **CCXT**, **NumPy/Pandas/SciPy/Scikit-learn**, and **Next.js / React / TypeScript / Tailwind CSS / Recharts**.

## Core Philosophy

1. **Survival First** — Independent `SurvivalController` has final authority over every order and cannot be overridden by strategies, AI advisors, or adaptive engines.
2. **Capital Preservation Second** — Starting with `$1.00 USD`, if calculated risk-budgeted order sizes fall below exchange minimums (`min_notional` / `min_quantity`), the agent preserves capital rather than forcing oversized trades.
3. **Statistical Edge Third** — Bayesian Beta-Binomial probability estimation, confidence interval lower bounds, sample-size requirements, and fee/spread/slippage-adjusted Expected Value (`EV = pW - (1-p)L - C`).
4. **Fractional Kelly Sizing Fourth** — Conservative default `0.10` (`MAX_RISK_PER_TRADE = 0.0025`, `MAX_DAILY_LOSS = 0.01`, `MAX_DRAWDOWN = 0.05`, `MAX_OPEN_POSITIONS = 1`, `MAX_LEVERAGE = 1`, Spot only).
5. **Adaptive Governance Fifth** — CUSUM, Page-Hinkley, Kolmogorov-Smirnov distribution-shift detection, walk-forward out-of-sample validation, versioned strategy switching, and instant rollback.

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

Automatic: market data collection, regime classification, signal evaluation, Kelly sizing, unsafe trade rejection, paper/risk-approved spot execution, deterioration detection.

Explicit Human Approval Required:
- Activating `LIVE_TRADING` for the first time
- Increasing risk limits or leverage
- Introducing new live strategies
- Verifying withdrawal destination addresses
- Executing withdrawals (`WITHDRAWAL_APPROVAL = REQUIRED`)
