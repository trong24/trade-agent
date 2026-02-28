/**
 * Trade-Agent Dashboard Frontend
 * Uses Lightweight Charts v4 for data visualization.
 * Includes: Candlestick + Volume + RSI pane (EMA9, WMA45)
 */

// -- State --
let chart, rsiChart;
let candleSeries, volumeSeries;
let rsiSeries, emaLine, wmaLine;

// -- Shared chart theme --
const theme = {
    layout: { background: { color: '#0b0e11' }, textColor: '#848e9c' },
    grid: { vertLines: { color: '#2b2f36' }, horzLines: { color: '#2b2f36' } },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor: '#2b2f36' },
    timeScale: { borderColor: '#2b2f36', timeVisible: true, secondsVisible: false },
};

// -- Initialization --
function init() {
    const { createChart: cc } = window.LightweightCharts;

    // ── Price chart ──────────────────────────────────────────────
    const priceEl = document.getElementById('chart');
    chart = cc(priceEl, { ...theme, width: priceEl.clientWidth, height: priceEl.clientHeight });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#0ecb81', downColor: '#f6465d',
        borderVisible: false,
        wickUpColor: '#0ecb81', wickDownColor: '#f6465d',
    });

    volumeSeries = chart.addHistogramSeries({
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    // ── RSI chart (slave – identical time domain to price chart) ──
    // By padding RSI data to cover all candle timestamps (warmup bars get the
    // first valid value), both charts always have the same number of bars →
    // identical bar widths → perfect pixel alignment on both edges forever.
    const rsiEl = document.getElementById('rsi-chart');
    rsiChart = cc(rsiEl, {
        ...theme,
        width: rsiEl.clientWidth,
        height: rsiEl.clientHeight,
        rightPriceScale: {
            borderColor: '#2b2f36',
            scaleMargins: { top: 0.05, bottom: 0.05 },
        },
        // Hide the RSI time axis — driven entirely by the price chart scroll
        timeScale: { ...theme.timeScale, visible: false },
        // Disable all user interaction so only the price chart controls panning
        handleScroll: false,
        handleScale: false,
    });

    rsiSeries = rsiChart.addLineSeries({
        color: '#e0e0e0', lineWidth: 1.5, title: 'RSI',
        priceFormat: { type: 'custom', formatter: v => v.toFixed(1) },
    });
    emaLine = rsiChart.addLineSeries({
        color: '#00bcd4', lineWidth: 1.5, title: 'EMA 9',
        priceFormat: { type: 'custom', formatter: v => v.toFixed(1) },
    });
    wmaLine = rsiChart.addLineSeries({
        color: '#ff9800', lineWidth: 1.5, title: 'WMA 45',
        priceFormat: { type: 'custom', formatter: v => v.toFixed(1) },
    });

    // ── Sync: price → RSI via simple logical range mirror ────────
    // Both charts now have the same number of bars (after RSI padding in
    // renderData), so logical index N in price chart == logical index N in RSI.
    // No offset needed. Just mirror the logical range 1:1.
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) rsiChart.timeScale().setVisibleLogicalRange(range);
    });

    // ── Resize: keep right-scale widths equal so x-axes stay pixel-aligned ──
    const syncScaleWidth = () => {
        // Read the actual rendered width of the price chart's right scale and
        // force the RSI chart's right scale to the same minimum width.
        // This ensures both chart drawing areas (total width minus scale) are equal.
        const w = chart.priceScale('right').width();
        if (w > 0) rsiChart.priceScale('right').applyOptions({ minimumWidth: w });
    };

    const ro = new ResizeObserver(() => {
        chart.applyOptions({ width: priceEl.clientWidth, height: priceEl.clientHeight });
        rsiChart.applyOptions({ width: rsiEl.clientWidth, height: rsiEl.clientHeight });
        syncScaleWidth();
        // Re-sync range after resize
        const range = chart.timeScale().getVisibleLogicalRange();
        if (range) rsiChart.timeScale().setVisibleLogicalRange(range);
    });
    ro.observe(priceEl);
    ro.observe(rsiEl);

    // ── Events ───────────────────────────────────────────────────
    document.getElementById('run-btn').addEventListener('click', runAnalysis);
    console.log('[dashboard] v9 init complete');
}

// -- Actions --
async function runAnalysis() {
    const symbol = document.getElementById('symbol').value;
    const interval = document.getElementById('interval').value;
    const strategy = document.getElementById('strategy').value;
    const start = document.getElementById('start-date').value;

    toggleLoading(true);

    try {
        const url = `/api/backtest?symbol=${symbol}&interval=${interval}&strategy=${strategy}&start=${start}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error(`API ${response.status}`);

        const data = await response.json();
        if (data.error) { alert(data.error); return; }
        renderData(data);
    } catch (err) {
        console.error('[dashboard]', err);
        alert('Failed to run analysis. Check server logs.');
    } finally {
        toggleLoading(false);
    }
}

function toTimestamp(isoStr) {
    return Math.floor(new Date(isoStr).getTime() / 1000);
}

function renderData(data) {
    // 1. Candlesticks
    const candles = data.candles.map(c => ({
        time: toTimestamp(c.time), open: c.open, high: c.high, low: c.low, close: c.close,
    }));
    candleSeries.setData(candles);

    // 2. Volume
    volumeSeries.setData(data.candles.map(c => ({
        time: toTimestamp(c.time),
        value: c.volume,
        color: c.close >= c.open ? 'rgba(14,203,129,0.3)' : 'rgba(246,70,93,0.3)',
    })));

    // 3. Trade markers
    if (data.trade_log && data.trade_log.length) {
        const markers = [];
        data.trade_log.forEach(t => {
            markers.push({
                time: toTimestamp(t.entry),
                position: t.side === 'long' ? 'belowBar' : 'aboveBar',
                color: t.side === 'long' ? '#0ecb81' : '#f6465d',
                shape: t.side === 'long' ? 'arrowUp' : 'arrowDown',
                text: `Entry ${t.side.toUpperCase()}`,
            });
            markers.push({
                time: toTimestamp(t.exit),
                position: t.side === 'long' ? 'aboveBar' : 'belowBar',
                color: '#848e9c', shape: 'circle',
                text: `Exit ${t.reason}`,
            });
        });
        markers.sort((a, b) => a.time - b.time);
        candleSeries.setMarkers(markers);
    }

    // 4. RSI pane – pad warmup bars with the first valid value so the RSI
    //    chart ends up with the same number of bars as the price chart.
    //    This makes bar widths identical → perfect left+right edge alignment.
    if (data.rsi_data && data.rsi_data.length) {
        // Build a lookup: timestamp → {rsi, ema9, wma45}
        const rsiLookup = new Map(
            data.rsi_data.map(d => [toTimestamp(d.time), d])
        );
        const first = data.rsi_data[0]; // first valid RSI point

        // Build padded arrays matching every candle timestamp
        const rsiPoints = [], emaPoints = [], wmaPoints = [];
        for (const c of candles) {
            const d = rsiLookup.get(c.time) || first; // warmup → first valid value
            rsiPoints.push({ time: c.time, value: d.rsi });
            emaPoints.push({ time: c.time, value: d.ema9 });
            wmaPoints.push({ time: c.time, value: d.wma45 });
        }

        rsiSeries.setData(rsiPoints);
        emaLine.setData(emaPoints);
        wmaLine.setData(wmaPoints);

        const warmupCount = candles.length - data.rsi_data.length;
        console.log(`[dashboard] RSI padded: ${warmupCount} warmup bars → both charts have ${candles.length} bars`);
    }

    // 5. Table + Metrics
    updateTable(data.trade_log || []);
    updateMetrics(data.metrics);

    // Fit price chart then mirror range to RSI
    chart.timeScale().fitContent();
    requestAnimationFrame(() => {
        // 1. Sync right price-scale width so both chart drawing areas are equal.
        //    Price labels (e.g. "69000.00") are wider than RSI labels ("43.9"),
        //    so without this the RSI chart's bar area is wider → misalignment.
        const w = chart.priceScale('right').width();
        if (w > 0) rsiChart.priceScale('right').applyOptions({ minimumWidth: w });

        // 2. Mirror time range (both charts now have identical bar counts AND
        //    identical drawing widths → perfect pixel alignment).
        const range = chart.timeScale().getVisibleLogicalRange();
        if (range) rsiChart.timeScale().setVisibleLogicalRange(range);
    });
}

function updateTable(trades) {
    const tbody = document.querySelector('#trades-table tbody');
    tbody.innerHTML = '';
    trades.forEach(t => {
        const row = document.createElement('tr');
        const pnlColor = t.pnl_pct > 0 ? 'positive' : 'negative';
        const sideClass = t.side === 'long' ? 'side-long' : 'side-short';
        row.innerHTML = `
            <td>${new Date(t.entry).toLocaleString()}</td>
            <td>${new Date(t.exit).toLocaleString()}</td>
            <td class="${sideClass}">${t.side.toUpperCase()}</td>
            <td>${t.entry_price.toLocaleString()}</td>
            <td>${t.exit_price.toLocaleString()}</td>
            <td class="value ${pnlColor}">${t.pnl_pct > 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%</td>
            <td style="color: #848e9c">${t.reason}</td>
        `;
        tbody.appendChild(row);
    });
}

function updateMetrics(m) {
    const roiEl = document.getElementById('metric-roi');
    roiEl.textContent = `${m.total_return_pct > 0 ? '+' : ''}${m.total_return_pct.toFixed(2)}%`;
    roiEl.className = `value ${m.total_return_pct > 0 ? 'positive' : 'negative'}`;
    document.getElementById('metric-winrate').textContent = `${m.win_rate_pct}%`;
    document.getElementById('metric-trades').textContent = m.trades;
    const ddEl = document.getElementById('metric-dd');
    ddEl.textContent = `${m.max_drawdown_pct.toFixed(2)}%`;
}

function toggleLoading(show) {
    const overlay = document.getElementById('loading-overlay');
    if (show) overlay.classList.remove('hidden');
    else overlay.classList.add('hidden');
}

// Start app
window.addEventListener('load', init);
