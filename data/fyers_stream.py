"""
data/fyers_stream.py
─────────────────────
Real-time market data via the Fyers Data WebSocket (v3), used as the
primary live-data source for the index board, constituents, market
breadth (advances/declines) and movers (gainers/losers/most-active).

Why a WebSocket instead of REST polling
────────────────────────────────────────
- ONE persistent connection carries live ticks for every symbol this app
  needs: 3 index symbols + the deduplicated union of Nifty 50, Sensex 30
  and Bank Nifty constituent equities (~140 unique symbols in practice).
  Fyers caps Data WebSocket subscriptions at 200 symbols per connection
  ("Understanding Symbol Subscription Limit in Data WebSocket",
  support.fyers.in) — this app's whole universe fits comfortably under
  that cap in a single connection.
- After the socket is open, no further REST calls are made to Fyers for
  market data at all. Every read — index_board(), constituents(),
  breadth(), movers() — is served from an in-memory cache that the
  socket's on_message callback updates as ticks stream in. This is what
  keeps Fyers request-per-second usage flat regardless of how often (or
  how many) frontend clients poll the Flask API.
- Reconnection/backoff on a dropped connection is handled by the SDK
  (reconnect=True). This module additionally re-subscribes the full
  symbol set on every on_connect, so a fresh connection — including the
  one opened after Fyers' daily token refresh — always asks for
  everything again.
- The socket is only started while `main.py`'s existing
  `_fyers_market_data_allowed()` gate says so (source-confirmed market
  hours), so nothing connects to Fyers outside trading hours, holidays or
  weekends. This module has no opinion on *when* to run — main.py decides
  that and calls start()/stop() accordingly.

Failure handling
─────────────────
Every read method returns None (never raises) when it has nothing
useful yet — no connection made, or no tick has arrived for that symbol
set. Callers (main.py) are expected to fall back to the existing
NSE/Fyers-REST/Yahoo waterfall in that case, so a slow-starting or
briefly-interrupted socket never produces a blank screen in the app.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time

from fyers_apiv3.FyersWebsocket import data_ws

log = logging.getLogger(__name__)

# Fyers' documented hard cap per Data WebSocket connection.
_MAX_WS_SYMBOLS = 200

# A tick older than this is treated as stale for the purposes of
# is_ready()/index_board()/breadth() — protects against silently serving
# frozen data forever if the feed goes quiet without the socket noticing.
_DEFAULT_STALE_AFTER_SECONDS = 45.0


class FyersMarketStream:
    """
    Thread-safe live-tick cache fed by a single Fyers Data WebSocket
    connection. One instance (see `stream` below) is shared for the whole
    process.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ticks: dict[str, dict] = {}                 # FYERS_SYMBOL -> normalized tick
        self._socket: data_ws.FyersDataSocket | None = None
        self._connected = False
        self._subscribed: list[str] = []
        self._index_symbols: dict[str, str] = {}           # market_key -> fyers index symbol
        self._market_symbols: dict[str, list[str]] = {}    # market_key -> [fyers equity symbols]
        self._last_tick_at: float | None = None

    # ── configuration ────────────────────────────────────────────────────
    def configure_universe(
        self,
        markets: list[dict],
        constituents_by_market: dict[str, list[str]],
    ) -> None:
        """
        Call once before start() (and again on every daily re-auth) with
        the current MARKETS config and the bare NSE symbol lists to track
        per market. Builds the deduplicated Fyers-format subscription
        list, capped at the documented 200-symbol limit — indices always
        win a slot; constituents are truncated if the union is too big.
        """
        with self._lock:
            self._index_symbols = {m["market_key"]: m["fyers_symbol"] for m in markets}
            self._market_symbols = {
                key: [f"NSE:{s.strip().upper()}-EQ" for s in syms if s and s.strip()]
                for key, syms in constituents_by_market.items()
            }

            ordered: list[str] = list(self._index_symbols.values())
            seen = set(ordered)
            for syms in self._market_symbols.values():
                for s in syms:
                    if s not in seen:
                        seen.add(s)
                        ordered.append(s)

            if len(ordered) > _MAX_WS_SYMBOLS:
                log.warning(
                    "fyers_stream: universe of %d symbols exceeds the %d "
                    "WebSocket cap — truncating extra constituents.",
                    len(ordered), _MAX_WS_SYMBOLS,
                )
                ordered = ordered[:_MAX_WS_SYMBOLS]

            self._subscribed = ordered
            log.info("fyers_stream: universe configured — %d symbols.", len(ordered))

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self, access_token_full: str) -> None:
        """
        Open the WebSocket connection and subscribe the configured
        universe. `access_token_full` must be in the SDK's
        "appid:accesstoken" format. Safe to call again (e.g. after the
        daily Fyers token refresh) — any previous socket is closed first.
        """
        self.stop()

        with self._lock:
            symbols = list(self._subscribed)

        if not symbols:
            log.warning("fyers_stream: no symbols configured — call configure_universe() first.")
            return

        def on_open():
            try:
                self._socket.subscribe(symbols=symbols, data_type="SymbolUpdate")
                log.info("fyers_stream: connected and subscribed to %d symbols.", len(symbols))
            except Exception as e:
                log.warning(f"fyers_stream: subscribe on connect failed: {e}")

        def on_message(message):
            self._handle_tick(message)

        def on_error(message):
            log.warning(f"fyers_stream: socket error: {message}")

        def on_close(message):
            log.info(f"fyers_stream: socket closed: {message}")
            with self._lock:
                self._connected = False

        socket = data_ws.FyersDataSocket(
            access_token=access_token_full,
            log_path="",
            litemode=False,          # full OHLC/change payload, not LTP-only
            write_to_file=False,
            reconnect=True,          # SDK handles reconnect/backoff on drop
            on_connect=on_open,
            on_close=on_close,
            on_error=on_error,
            on_message=on_message,
        )
        self._socket = socket
        # FyersDataSocket.connect() blocks the calling thread (it runs the
        # websocket event loop), so it must run in the background.
        threading.Thread(target=socket.connect, daemon=True, name="fyers-ws").start()

        with self._lock:
            self._connected = True

    def stop(self) -> None:
        """Close the current connection, if any. Safe to call repeatedly."""
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                socket.close_connection()
            except Exception:
                pass
        with self._lock:
            self._connected = False

    def is_ready(self, max_age_seconds: float = _DEFAULT_STALE_AFTER_SECONDS) -> bool:
        """True once the socket is connected AND a tick has arrived recently."""
        with self._lock:
            if not self._connected or self._last_tick_at is None:
                return False
            return (time.time() - self._last_tick_at) <= max_age_seconds

    # ── ingest (socket callback thread) ─────────────────────────────────
    def _handle_tick(self, message: dict) -> None:
        if not isinstance(message, dict):
            return
        symbol = str(message.get("symbol") or message.get("n") or "").upper().strip()
        if not symbol:
            return
        row = _normalize_tick(message)
        with self._lock:
            self._ticks[symbol] = row
            self._last_tick_at = time.time()

    # ── read side (Flask request threads) ───────────────────────────────
    def last_tick_at(self) -> float | None:
        """
        Unix time of the most recent tick received, or None if there has
        never been one. Unlike is_ready() this does not care whether the
        socket is still connected — the tick cache outlives stop(), which is
        what lets main.py take the end-of-session snapshot just after the
        close.
        """
        with self._lock:
            return self._last_tick_at

    def index_board(
        self,
        markets: list[dict],
        max_age_seconds: float = _DEFAULT_STALE_AFTER_SECONDS,
        last_known: bool = False,
    ) -> dict | None:
        """
        /api/market payload shape, built entirely from live ticks.
        Returns None if any of the 3 indices hasn't ticked yet (or is
        stale) — caller should fall back to the REST waterfall.

        last_known=True skips the connected-and-fresh check and reads
        whatever the tick cache last held. Used only for the end-of-session
        snapshot (main.py::_build_close_snapshot), where "the final tick we
        received" is exactly what is wanted even though the feed has gone
        quiet because the market is closed.
        """
        if not last_known and not self.is_ready(max_age_seconds):
            return None

        with self._lock:
            ticks = dict(self._ticks)
            idx_syms = dict(self._index_symbols)

        found: dict[str, dict] = {}
        for m in markets:
            sym = idx_syms.get(m["market_key"], m["fyers_symbol"]).upper()
            row = ticks.get(sym)
            if row and row.get("ltp") is not None:
                found[m["market_key"]] = {
                    "name"  : m["display_name"],
                    "value" : row["ltp"],
                    "change": row["chp"] if row.get("chp") is not None else 0.0,
                    "points": row["ch"]  if row.get("ch")  is not None else 0.0,
                }

        if len(found) < len(markets):
            return None

        markets_list = []
        for m in markets:
            entry = found[m["market_key"]].copy()
            entry["key"] = m["market_key"]
            markets_list.append(entry)

        return {
            "markets"   : markets_list,
            "source"    : "fyers_ws",
            "updated_at": datetime.datetime.now().isoformat(),
        }

    def constituents(self, market_key: str) -> list[dict] | None:
        """
        Live rows (rank/symbol/last_price/change_pct/...) for one market's
        tracked equities. Returns None if nothing has ticked for this
        market yet — caller should fall back to NSE/REST-Fyers.
        """
        with self._lock:
            ticks = dict(self._ticks)
            syms  = list(self._market_symbols.get(market_key, []))

        if not syms:
            return None

        rows = []
        for rank, sym in enumerate(syms, start=1):
            row = ticks.get(sym.upper())
            if not row or row.get("ltp") is None:
                continue
            bare = _bare_symbol(sym)
            rows.append({
                "rank"          : rank,
                "symbol"        : bare,
                "company_name"  : bare,
                "last_price"    : row.get("ltp"),
                "change_pct"    : row.get("chp"),
                "change_points" : row.get("ch"),
                "open"          : row.get("open"),
                "high"          : row.get("high"),
                "low"           : row.get("low"),
                "year_high"     : None,
                "year_low"      : None,
                "volume"        : row.get("volume"),
                "market_cap"    : None,
            })
        return rows or None

    def breadth(self) -> dict | None:
        """
        Advances/declines/unchanged across the deduplicated union of every
        tracked equity (all three markets combined — real market breadth,
        computed live). Returns None until enough ticks have arrived.
        """
        with self._lock:
            ticks = dict(self._ticks)
            all_syms = {s for syms in self._market_symbols.values() for s in syms}

        adv = dec = unch = 0
        seen_any = False
        for sym in all_syms:
            row = ticks.get(sym.upper())
            if not row or row.get("chp") is None:
                continue
            seen_any = True
            if row["chp"] > 0:
                adv += 1
            elif row["chp"] < 0:
                dec += 1
            else:
                unch += 1

        if not seen_any:
            return None
        return {"advances": adv, "declines": dec, "unchanged": unch}

    def movers(self, top_n: int = 10) -> dict | None:
        """
        Top gainers / losers / most-active by volume, across the
        deduplicated union of every tracked equity, from live ticks only
        (no extra API calls). Returns None until enough ticks have arrived.
        """
        with self._lock:
            ticks = dict(self._ticks)
            all_syms = {s for syms in self._market_symbols.values() for s in syms}

        rows = []
        for sym in all_syms:
            row = ticks.get(sym.upper())
            if not row or row.get("ltp") is None:
                continue
            rows.append({
                "symbol"       : _bare_symbol(sym),
                "last_price"   : row.get("ltp"),
                "change_pct"   : row.get("chp") or 0.0,
                "change_points": row.get("ch") or 0.0,
                "volume"       : row.get("volume") or 0,
            })

        if not rows:
            return None

        gainers = sorted(rows, key=lambda r: r["change_pct"], reverse=True)[:top_n]
        losers  = sorted(rows, key=lambda r: r["change_pct"])[:top_n]
        active  = sorted(rows, key=lambda r: r["volume"], reverse=True)[:top_n]
        return {"gainers": gainers, "losers": losers, "most_active": active}


def _bare_symbol(fyers_symbol: str) -> str:
    s = fyers_symbol.upper().strip()
    if ":" in s:
        s = s.split(":", 1)[1]
    if s.endswith("-EQ"):
        s = s[:-3]
    return s


def _pick(d: dict, *keys: str):
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _num(d: dict, *keys: str) -> float | None:
    v = _pick(d, *keys)
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _int(d: dict, *keys: str) -> int | None:
    v = _pick(d, *keys)
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _normalize_tick(message: dict) -> dict:
    """
    Fyers' symbolUpdate payload field names have shifted slightly across
    SDK versions and between index vs equity symbols, so every field is
    picked defensively from several known aliases — the same approach
    already used for REST quotes in data/quotes.py and main.py.
    """
    return {
        "ltp"   : _num(message, "ltp", "lp"),
        "chp"   : _num(message, "chp", "change_percent", "change_pct"),
        "ch"    : _num(message, "ch", "change", "chn"),
        "open"  : _num(message, "open_price", "open"),
        "high"  : _num(message, "high_price", "high"),
        "low"   : _num(message, "low_price", "low"),
        "volume": _int(message, "vol_traded_today", "volume", "vol"),
        "ts"    : time.time(),
    }


# Process-wide singleton — one WebSocket connection for the whole app.
stream = FyersMarketStream()
