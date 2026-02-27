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

    // ── RSI chart ────────────────────────────────────────────────
    const rsiEl = document.getElementById('rsi-chart');
    rsiChart = cc(rsiEl, {
        ...theme,
        width: rsiEl.clientWidth,
        height: rsiEl.clientHeight,
        rightPriceScale: {
            borderColor: '#2b2f36',
            scaleMargins: { top: 0.05, bottom: 0.05 },
        },
    });

    rsiSeries = rsiChart.addLineSeries({
        color: '#e0e0e0',
        lineWidth: 1.5,
        title: 'RSI',
        priceFormat: { type: 'custom', formatter: v => v.toFixed(1) },
    });

    emaLine = rsiChart.addLineSeries({
        color: '#00bcd4',
        lineWidth: 1.5,
        title: 'EMA 9',
        priceFormat: { type: 'custom', formatter: v => v.toFixed(1) },
    });

    wmaLine = rsiChart.addLineSeries({
        color: '#ff9800',
        lineWidth: 1.5,
        title: 'WMA 45',
        priceFormat: { type: 'custom', formatter: v => v.toFixed(1) },
    });

    // ── Sync time scales ─────────────────────────────────────────
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) rsiChart.timeScale().setVisibleLogicalRange(range);
    });
    rsiChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) chart.timeScale().setVisibleLogicalRange(range);
    });

    // ── Resize handling ──────────────────────────────────────────
    const ro = new ResizeObserver(() => {
        chart.applyOptions({ width: priceEl.clientWidth, height: priceEl.clientHeight });
        rsiChart.applyOptions({ width: rsiEl.clientWidth, height: rsiEl.clientHeight });
    });
    ro.observe(priceEl);
    ro.observe(rsiEl);

    // ── Events ───────────────────────────────────────────────────
    document.getElementById('run-btn').addEventListener('click', runAnalysis);
    console.log('[dashboard] init complete');
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
    const volumes = data.candles.map(c => ({
        time: toTimestamp(c.time),
        value: c.volume,
        color: c.close >= c.open ? 'rgba(14, 203, 129, 0.3)' : 'rgba(246, 70, 93, 0.3)',
    }));
    volumeSeries.setData(volumes);

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

    // 4. RSI pane
    if (data.rsi_data && data.rsi_data.length) {
        rsiSeries.setData(data.rsi_data.map(d => ({ time: toTimestamp(d.time), value: d.rsi })));
        emaLine.setData(data.rsi_data.map(d => ({ time: toTimestamp(d.time), value: d.ema9 })));
        wmaLine.setData(data.rsi_data.map(d => ({ time: toTimestamp(d.time), value: d.wma45 })));
    }

    // 5. Table + Metrics
    updateTable(data.trade_log || []);
    updateMetrics(data.metrics);

    chart.timeScale().fitContent();
    rsiChart.timeScale().fitContent();
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
