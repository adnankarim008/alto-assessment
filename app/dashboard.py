from __future__ import annotations

"""Minimal but polished HTML dashboard for the case-study presentation layer."""

import asyncio
import json
import time

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, StreamingResponse

from app.service import PositionService


MAX_DASHBOARD_POSITIONS = 50
MAX_DASHBOARD_HISTOGRAM_BARS = 10


def _dashboard_snapshot(service: PositionService) -> dict[str, object]:
    """Build one dashboard payload from the in-memory read model."""

    positions = service.positions().model_dump(by_alias=True)
    stats = service.engine.stats()
    all_positions = positions["positions"]
    top_positions = sorted(
        all_positions,
        key=lambda item: abs(int(item["netPosition"])),
        reverse=True,
    )[:MAX_DASHBOARD_POSITIONS]
    buy_histogram = sorted(
        stats["buyHistogram"],
        key=lambda item: int(item["quantity"]),
        reverse=True,
    )[:MAX_DASHBOARD_HISTOGRAM_BARS]
    sell_histogram = sorted(
        stats["sellHistogram"],
        key=lambda item: int(item["quantity"]),
        reverse=True,
    )[:MAX_DASHBOARD_HISTOGRAM_BARS]
    latest_created_at = service.store.latest_created_at()
    now = time.time()

    return {
        "watermark": positions["watermark"],
        "positions": top_positions,
        "totalInstruments": len(all_positions),
        "totalGrossBuy": sum(int(item["totalBuys"]) for item in all_positions),
        "totalGrossSell": sum(int(item["totalSells"]) for item in all_positions),
        "positionLimit": MAX_DASHBOARD_POSITIONS,
        "buyHistogram": buy_histogram,
        "sellHistogram": sell_histogram,
        "histogramLimit": MAX_DASHBOARD_HISTOGRAM_BARS,
        "serverTime": now,
        "latestEventAgeMs": None if latest_created_at is None else int((now - latest_created_at) * 1000),
    }


def create_dashboard_router(service: PositionService) -> APIRouter:
    """Create the dashboard route bound to the live position service."""

    router = APIRouter()

    @router.get("/events/stream")
    async def dashboard_stream() -> StreamingResponse:
        """Stream dashboard snapshots with Server-Sent Events.

        The stream emits immediately on connect, then emits a new snapshot when
        the processing watermark changes. A heartbeat keeps proxies/browsers from
        treating the connection as idle.
        """

        async def events():
            last_watermark: int | None = None
            heartbeat_ticks = 0

            while True:
                watermark = service.engine.watermark
                if watermark != last_watermark:
                    payload = json.dumps(_dashboard_snapshot(service), separators=(",", ":"))
                    yield f"event: snapshot\ndata: {payload}\n\n"
                    last_watermark = watermark
                    heartbeat_ticks = 0
                elif heartbeat_ticks >= 40:
                    yield ": heartbeat\n\n"
                    heartbeat_ticks = 0

                heartbeat_ticks += 1
                await asyncio.sleep(0.25)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        """Render a live browser dashboard backed by the JSON API."""

        return """
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Position Engine Risk Console</title>
            <style>
              :root {
                --ink: #172033;
                --muted: #647084;
                --panel: rgba(255, 255, 255, 0.94);
                --line: #c8d1dc;
                --buy: #137a4b;
                --sell: #b13a2f;
                --amber: #8a5a00;
                --blue: #1f4f82;
                --bg: #edf1f5;
                --shadow: rgba(23, 32, 51, 0.08);
              }

              * { box-sizing: border-box; }

              body {
                margin: 0;
                min-height: 100vh;
                color: var(--ink);
                font-family: "Avenir Next", "Helvetica Neue", Helvetica, sans-serif;
                background: var(--bg);
                overflow-x: hidden;
              }

              body::before {
                content: none;
              }

              .shell {
                width: min(1220px, calc(100vw - 32px));
                margin: 0 auto;
                padding: 28px 0 48px;
              }

              .hero {
                display: grid;
                grid-template-columns: 1.25fr 0.75fr;
                gap: 20px;
                align-items: stretch;
                margin-bottom: 20px;
              }

              .title-card, .status-card, .panel {
                border: 1px solid var(--line);
                background: var(--panel);
                box-shadow: 0 14px 36px var(--shadow);
              }

              .title-card {
                padding: 30px;
                border-radius: 0;
                min-height: 190px;
              }

              .eyebrow {
                display: inline-flex;
                align-items: center;
                gap: 10px;
                color: var(--amber);
                font: 700 12px/1.2 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                letter-spacing: 0.18em;
                text-transform: uppercase;
              }

              .pulse {
                width: 8px;
                height: 8px;
                border-radius: 999px;
                background: var(--buy);
                box-shadow: 0 0 0 0 rgba(19, 122, 75, 0.45);
                animation: pulse 1.7s infinite;
              }

              h1 {
                margin: 18px 0 0;
                max-width: 780px;
                color: #000000;
                font-size: clamp(38px, 6vw, 82px);
                line-height: 0.95;
                letter-spacing: -0.06em;
                font-weight: 800;
              }

              h2 {
                margin: 8px 0 0;
                font-size: 26px;
                letter-spacing: -0.04em;
              }

              .subtitle {
                max-width: 660px;
                margin: 18px 0 0;
                color: var(--muted);
                font-size: 16px;
              }

              .status-card {
                border-radius: 0;
                padding: 24px;
                display: grid;
                align-content: space-between;
                gap: 22px;
              }

              .watermark-number {
                font: 800 clamp(42px, 6vw, 78px)/0.9 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                letter-spacing: -0.08em;
              }

              .status-grid {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 12px;
              }

              .mini-stat {
                border: 1px solid rgba(255, 255, 255, 0.08);
                background: #f4f7fa;
                border-color: var(--line);
                border-radius: 0;
                padding: 14px;
              }

              .mini-label, .section-label {
                color: var(--muted);
                font: 700 11px/1.2 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                letter-spacing: 0.14em;
                text-transform: uppercase;
              }

              .mini-value {
                display: block;
                margin-top: 8px;
                font: 800 24px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              }

              .grid {
                display: grid;
                grid-template-columns: minmax(0, 1.1fr) minmax(340px, 0.9fr);
                gap: 20px;
              }

              .panel {
                border-radius: 0;
                padding: 22px;
              }

              .panel-head {
                display: flex;
                justify-content: space-between;
                gap: 16px;
                align-items: baseline;
                margin-bottom: 18px;
              }

              .refresh-state {
                color: var(--amber);
                font: 700 12px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              }

              .view-note {
                margin-top: 6px;
                color: var(--muted);
                font: 700 11px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                text-transform: uppercase;
                letter-spacing: 0.08em;
              }

              .positions, .histograms {
                display: grid;
                gap: 12px;
              }

              .position-row {
                display: grid;
                grid-template-columns: 92px 1fr 110px 110px 120px;
                gap: 12px;
                align-items: center;
                padding: 16px;
                border: 1px solid var(--line);
                border-radius: 0;
                background: #ffffff;
                transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;
              }

              .position-row:hover {
                transform: none;
                border-color: var(--blue);
                background: #f5f8fb;
              }

              .symbol {
                font: 900 23px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                letter-spacing: -0.05em;
              }

              .net-bar {
                position: relative;
                height: 12px;
                border-radius: 0;
                background: #dce3eb;
                overflow: hidden;
              }

              .net-bar span {
                position: absolute;
                top: 0;
                bottom: 0;
                left: 50%;
                width: var(--width);
                background: var(--color);
                box-shadow: none;
              }

              .net-bar .negative {
                left: auto;
                right: 50%;
              }

              .number {
                text-align: right;
                font: 800 18px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              }

              .buy { color: var(--buy); }
              .sell { color: var(--sell); }
              .flat { color: var(--muted); }

              .histogram-block {
                border: 1px solid var(--line);
                border-radius: 0;
                padding: 16px;
                background: #ffffff;
              }

              .bar-row {
                display: grid;
                grid-template-columns: 64px 1fr 70px;
                gap: 10px;
                align-items: center;
                margin-top: 12px;
                font: 700 12px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              }

              .bar-track {
                height: 10px;
                overflow: hidden;
                border-radius: 0;
                background: #dce3eb;
              }

              .bar-fill {
                height: 100%;
                width: var(--width);
                border-radius: 0;
                background: var(--color);
                box-shadow: none;
                transition: width 220ms ease;
              }

              .empty {
                padding: 44px 18px;
                border: 1px dashed var(--line);
                border-radius: 0;
                color: var(--muted);
                text-align: center;
              }

              .error { color: var(--sell); }

              @keyframes pulse {
                0% { box-shadow: 0 0 0 0 rgba(19, 122, 75, 0.45); }
                70% { box-shadow: 0 0 0 10px rgba(19, 122, 75, 0); }
                100% { box-shadow: 0 0 0 0 rgba(19, 122, 75, 0); }
              }

              @media (max-width: 900px) {
                .hero, .grid { grid-template-columns: 1fr; }
                .position-row { grid-template-columns: 72px 1fr 82px; }
                .position-row .extra { display: none; }
              }
            </style>
          </head>
          <body>
            <main class="shell">
              <section class="hero">
                <div class="title-card">
                  <div class="eyebrow"><span class="pulse"></span> Live risk surface</div>
                  <h1>Position Engine Console</h1>
                  <p class="subtitle">A low-latency read view for current exposure, buy/sell pressure, and processing freshness across instruments.</p>
                </div>
                <aside class="status-card">
                  <div>
                    <div class="section-label">Processing watermark</div>
                    <div id="watermark" class="watermark-number">0</div>
                  </div>
                  <div class="status-grid">
                    <div class="mini-stat"><span class="mini-label">Instruments</span><span id="instrumentCount" class="mini-value">0</span></div>
                    <div class="mini-stat"><span class="mini-label">Gross Buy</span><span id="grossBuy" class="mini-value buy">0</span></div>
                    <div class="mini-stat"><span class="mini-label">Gross Sell</span><span id="grossSell" class="mini-value sell">0</span></div>
                    <div class="mini-stat"><span class="mini-label">Event Age</span><span id="eventAge" class="mini-value">n/a</span></div>
                  </div>
                </aside>
              </section>

              <section class="grid">
                <div class="panel">
                  <div class="panel-head">
                    <div>
                      <div class="section-label">Instrument-wise current value</div>
                      <h2>Live Positions</h2>
                      <div id="positionLimitNote" class="view-note">Showing top instruments by absolute exposure</div>
                    </div>
                    <div id="refreshState" class="refresh-state">connecting</div>
                  </div>
                  <div id="positions" class="positions"></div>
                </div>

                <div class="panel">
                  <div class="panel-head">
                    <div>
                      <div class="section-label">Total buys and sells</div>
                      <h2>Exposure Histograms</h2>
                      <div id="histogramLimitNote" class="view-note">Showing top buy and sell instruments</div>
                    </div>
                  </div>
                  <div class="histograms">
                    <div class="histogram-block">
                      <div class="section-label buy">Buy quantity</div>
                      <div id="buyHistogram"></div>
                    </div>
                    <div class="histogram-block">
                      <div class="section-label sell">Sell quantity</div>
                      <div id="sellHistogram"></div>
                    </div>
                  </div>
                </div>
              </section>
            </main>

            <script>
              const formatNumber = new Intl.NumberFormat('en-US');
              const state = { source: null, lastWatermark: null };

              function signedClass(value) {
                if (value > 0) return 'buy';
                if (value < 0) return 'sell';
                return 'flat';
              }

              function signedValue(value) {
                if (value > 0) return `+${formatNumber.format(value)}`;
                return formatNumber.format(value);
              }

              function formatAge(ageMs) {
                if (ageMs === null || ageMs === undefined) return 'n/a';
                if (ageMs < 1000) return `${ageMs}ms`;
                if (ageMs < 60000) return `${(ageMs / 1000).toFixed(1)}s`;
                return `${Math.floor(ageMs / 60000)}m ${Math.floor((ageMs % 60000) / 1000)}s`;
              }

              function renderPositions(payload) {
                const container = document.getElementById('positions');
                const positions = payload.positions || [];
                const maxAbs = Math.max(1, ...positions.map((item) => Math.abs(item.netPosition)));

                document.getElementById('watermark').textContent = formatNumber.format(payload.watermark || 0);
                document.getElementById('instrumentCount').textContent = formatNumber.format(payload.totalInstruments ?? positions.length);
                document.getElementById('grossBuy').textContent = formatNumber.format(payload.totalGrossBuy ?? positions.reduce((sum, item) => sum + item.totalBuys, 0));
                document.getElementById('grossSell').textContent = formatNumber.format(payload.totalGrossSell ?? positions.reduce((sum, item) => sum + item.totalSells, 0));
                document.getElementById('eventAge').textContent = formatAge(payload.latestEventAgeMs);
                document.getElementById('positionLimitNote').textContent = `Showing top ${payload.positionLimit || positions.length} of ${payload.totalInstruments ?? positions.length} instruments by absolute exposure`;

                if (!positions.length) {
                  container.innerHTML = '<div class="empty">No trade events ingested yet. POST events to <code>/events</code> or <code>/events/batch</code>.</div>';
                  return;
                }

                container.innerHTML = positions.map((item) => {
                  const width = `${Math.max(4, Math.round((Math.abs(item.netPosition) / maxAbs) * 50))}%`;
                  const direction = item.netPosition < 0 ? 'negative' : 'positive';
                  const color = item.netPosition < 0 ? 'var(--sell)' : 'var(--buy)';
                  return `
                    <div class="position-row">
                      <div class="symbol">${item.instrument}</div>
                      <div class="net-bar"><span class="${direction}" style="--width:${width}; --color:${color}"></span></div>
                      <div class="number ${signedClass(item.netPosition)}">${signedValue(item.netPosition)}</div>
                      <div class="number buy extra">${formatNumber.format(item.totalBuys)}</div>
                      <div class="number sell extra">${formatNumber.format(item.totalSells)}</div>
                    </div>
                  `;
                }).join('');
              }

              function renderHistogram(elementId, data, color) {
                const element = document.getElementById(elementId);
                const max = Math.max(1, ...data.map((item) => item.quantity));
                if (!data.length) {
                  element.innerHTML = '<div class="empty">No histogram data</div>';
                  return;
                }

                element.innerHTML = data.map((item) => `
                  <div class="bar-row">
                    <div>${item.instrument}</div>
                    <div class="bar-track"><div class="bar-fill" style="--width:${Math.round((item.quantity / max) * 100)}%; --color:${color}"></div></div>
                    <div class="number">${formatNumber.format(item.quantity)}</div>
                  </div>
                `).join('');
              }

              function connectStream() {
                const status = document.getElementById('refreshState');
                const source = new EventSource('/events/stream');
                state.source = source;

                source.onopen = () => {
                  status.textContent = 'stream connected';
                  status.classList.remove('error');
                };

                source.addEventListener('snapshot', (event) => {
                  const snapshot = JSON.parse(event.data);
                  const previousWatermark = state.lastWatermark;
                  renderPositions(snapshot);
                  document.getElementById('histogramLimitNote').textContent = `Showing top ${snapshot.histogramLimit || 10} buy and sell instruments`;
                  renderHistogram('buyHistogram', snapshot.buyHistogram || [], 'var(--buy)');
                  renderHistogram('sellHistogram', snapshot.sellHistogram || [], 'var(--sell)');

                  if (previousWatermark === null) {
                    status.textContent = `loaded ${new Date().toLocaleTimeString()}`;
                  } else if (snapshot.watermark !== previousWatermark) {
                    status.textContent = `updated ${new Date().toLocaleTimeString()}`;
                  } else {
                    status.textContent = 'stream alive';
                  }

                  state.lastWatermark = snapshot.watermark;
                  status.classList.remove('error');
                });

                source.onerror = () => {
                  status.textContent = 'feed interrupted';
                  status.classList.add('error');
                };
              }

              connectStream();
            </script>
          </body>
        </html>
        """

    return router
