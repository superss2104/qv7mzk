import { useEffect, useMemo, useState } from "react";

type Interval = {
  delivery_start: string;
  cutoff: string;
  publish_time: string | null;
  forecast_mw: number | null;
  actual_mw: number | null;
  error_mw: number | null;
};

type Evaluation = {
  metrics: {
    expected_intervals: number;
    forecast_available: number;
    actual_available: number;
    matched_intervals: number;
    forecast_coverage: number;
    actual_coverage: number;
    matched_coverage: number;
    mae_mw: number | null;
    rmse_mw: number | null;
    bias_mw: number | null;
  };
  intervals: Interval[];
};

type Metadata = {
  retrieved_at?: string;
  interpretation?: string;
  records?: { forecast_vintages?: number; forecasts_normalized?: number; actuals_normalized?: number };
  coverage?: { forecast_publish_time_from?: string; forecast_publish_time_to?: string };
};

type Mode = "fixed_cutoff" | "fixed_horizon";

const defaultDate = "2026-01-15";
const defaultCutoff = "07:30";

function nextDate(date: string) {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10);
}

function previousDate(date: string) {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day - 1)).toISOString().slice(0, 10);
}

function utcIso(date: string, time = "00:00") {
  return `${date}T${time}:00Z`;
}

function londonTime(value: string | null | undefined, options: Intl.DateTimeFormatOptions = {}) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London",
    hour: "2-digit",
    minute: "2-digit",
    ...options,
  }).format(new Date(value));
}

function formatNumber(value: number | null, digits = 0) {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value);
}

function Chart({ intervals }: { intervals: Interval[] }) {
  const points = intervals.filter((row) => row.actual_mw !== null || row.forecast_mw !== null);
  if (!points.length) return <div className="chart-empty">No intervals to plot for this selection.</div>;

  const values = points.flatMap((row) => [row.actual_mw, row.forecast_mw].filter((value): value is number => value !== null));
  const minimum = Math.floor((Math.min(...values) - 500) / 1000) * 1000;
  const maximum = Math.ceil((Math.max(...values) + 500) / 1000) * 1000;
  const width = 920;
  const height = 296;
  const left = 68;
  const right = 18;
  const top = 16;
  const bottom = 36;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const x = (index: number) => left + (points.length <= 1 ? chartWidth / 2 : (index / (points.length - 1)) * chartWidth);
  const y = (value: number) => top + ((maximum - value) / (maximum - minimum || 1)) * chartHeight;
  const line = (field: "actual_mw" | "forecast_mw") => points
    .map((row, index) => row[field] === null ? null : `${x(index)},${y(row[field]!)}`)
    .filter((value): value is string => value !== null)
    .join(" ");
  const tickValues = Array.from({ length: 4 }, (_, index) => minimum + ((maximum - minimum) / 3) * index);
  const xLabels = [0, Math.round((points.length - 1) / 2), points.length - 1];

  return (
    <div className="chart-wrap">
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Hourly forecast and actual wind generation in megawatts">
        {tickValues.map((tick) => (
          <g key={tick}>
            <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="chart-grid" />
            <text x={left - 12} y={y(tick) + 4} textAnchor="end" className="chart-axis">{formatNumber(tick / 1000, 0)}k</text>
          </g>
        ))}
        <polyline points={line("forecast_mw")} fill="none" className="line-forecast" />
        <polyline points={line("actual_mw")} fill="none" className="line-actual" />
        {points.map((row, index) => row.forecast_mw === null ? null : (
          <circle key={`f-${row.delivery_start}`} cx={x(index)} cy={y(row.forecast_mw)} r="3" className="dot-forecast">
            <title>{`${londonTime(row.delivery_start)} · forecast ${formatNumber(row.forecast_mw)} MW`}</title>
          </circle>
        ))}
        {points.map((row, index) => row.actual_mw === null ? null : (
          <circle key={`a-${row.delivery_start}`} cx={x(index)} cy={y(row.actual_mw)} r="3" className="dot-actual">
            <title>{`${londonTime(row.delivery_start)} · actual ${formatNumber(row.actual_mw)} MW`}</title>
          </circle>
        ))}
        {xLabels.map((index) => (
          <text key={index} x={x(index)} y={height - 7} textAnchor="middle" className="chart-axis">
            {londonTime(points[index]?.delivery_start)}
          </text>
        ))}
      </svg>
      <div className="chart-legend">
        <span><i className="legend-line actual" /> Actual generation</span>
        <span><i className="legend-line forecast" /> Selected forecast</span>
        <span className="chart-unit">MW · delivery start in London time</span>
      </div>
    </div>
  );
}

function App() {
  const [deliveryDate, setDeliveryDate] = useState(defaultDate);
  const [mode, setMode] = useState<Mode>("fixed_cutoff");
  const [cutoff, setCutoff] = useState(defaultCutoff);
  const [horizon, setHorizon] = useState("24");
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo(() => {
    const params = new URLSearchParams({
      delivery_from: utcIso(deliveryDate),
      delivery_to: utcIso(nextDate(deliveryDate)),
      mode,
    });
    if (mode === "fixed_cutoff") params.set("cutoff", utcIso(previousDate(deliveryDate), cutoff));
    else params.set("horizon_hours", horizon);
    return params.toString();
  }, [deliveryDate, mode, cutoff, horizon]);

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError(null);
    Promise.all([
      fetch(`/api/evaluation?${query}`, { signal: controller.signal }).then(async (response) => {
        if (!response.ok) throw new Error((await response.json()).detail ?? "Evaluation request failed");
        return response.json() as Promise<Evaluation>;
      }),
      fetch("/api/metadata", { signal: controller.signal }).then(async (response) => {
        if (!response.ok) throw new Error("Could not load snapshot metadata");
        return response.json() as Promise<Metadata>;
      }),
    ])
      .then(([nextEvaluation, nextMetadata]) => {
        setEvaluation(nextEvaluation);
        setMetadata(nextMetadata);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "Could not load the evaluation.");
      })
      .finally(() => setBusy(false));
    return () => controller.abort();
  }, [query]);

  const metrics = evaluation?.metrics;
  const label = mode === "fixed_cutoff" ? `${previousDate(deliveryDate)} · ${cutoff} UTC cutoff` : `${horizon}h lead time`;
  const biasPositive = (metrics?.bias_mw ?? 0) >= 0;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-symbol"><span /><span /><span /></div>
          <div><strong>gridbricks</strong><small>ENERGY INTELLIGENCE</small></div>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          <a className="nav-item active" href="#overview"><span className="nav-icon">◫</span> Forecast review</a>
        </nav>
        <div className="sidebar-spacer" />
        <div className="side-status"><span className="status-dot" /><div><strong>Snapshot online</strong><small>Historical data loaded</small></div></div>
        <div className="sidebar-footer">GRIDBRICKS · INTERNSHIP CHALLENGE<br />BMRS / ELEXON DATA</div>
      </aside>

      <main className="main-content" id="overview">
        <header className="topbar">
          <div className="breadcrumb">Analytics <span>/</span> Wind forecast review</div>
          <div className="topbar-right"><span className="live-indicator"><i /> SNAPSHOT</span><span className="avatar">GB</span></div>
        </header>

        <div className="page-wrap">
          <div className="page-heading">
            <div>
              <div className="eyebrow"><span className="eyebrow-mark" /> GENERATION FORECASTS</div>
              <h1>Wind forecast review</h1>
              <p className="page-subtitle">Compare the forecast available at decision time with realized wind generation.</p>
            </div>
            <div className="snapshot-pill"><span className="status-dot" /> Historical snapshot <b>·</b> Jan 2026</div>
          </div>

          <section className="control-panel" aria-label="Evaluation controls">
            <div className="control-title"><span className="control-icon">⚙</span><div><strong>Evaluation setup</strong><small>Choose the delivery date and information available to the buyer.</small></div></div>
            <div className="control-fields">
              <label className="field"><span>Delivery date</span><input type="date" value={deliveryDate} onChange={(event) => setDeliveryDate(event.target.value)} min="2026-01-01" max="2026-02-02" /></label>
              <label className="field"><span>Selection rule</span><select value={mode} onChange={(event) => setMode(event.target.value as Mode)}><option value="fixed_cutoff">Fixed decision cutoff</option><option value="fixed_horizon">Fixed lead time</option></select></label>
              {mode === "fixed_cutoff" ? (
                <label className="field"><span>Decision cutoff <em>UTC · previous day</em></span><input type="time" value={cutoff} onChange={(event) => setCutoff(event.target.value)} /></label>
              ) : (
                <label className="field"><span>Minimum lead time</span><select value={horizon} onChange={(event) => setHorizon(event.target.value)}><option value="12">12 hours</option><option value="24">24 hours</option><option value="36">36 hours</option><option value="48">48 hours</option></select></label>
              )}
              <div className="control-result"><small>ACTIVE RULE</small><strong>{label}</strong></div>
            </div>
          </section>

          {error && <div className="error-banner" role="alert"><strong>Couldn’t load this evaluation.</strong> {error} <span>Check that the API is running on port 8000.</span></div>}

          <div className="section-heading">
            <div><div className="section-kicker">DAY VIEW <span>·</span> {deliveryDate}</div><h2>Forecast performance</h2></div>
            <span className="diagnostic-badge"><i /> SINGLE-DAY DIAGNOSTIC</span>
          </div>

          <section className="metric-grid" aria-label="Forecast performance metrics">
            <article className="metric-card">
              <div className="metric-top"><span>Mean absolute error</span><span className="metric-glyph">↕</span></div>
              <div className="metric-value">{busy ? "—" : formatNumber(metrics?.mae_mw ?? null)} <small>MW</small></div>
              <div className="metric-note">Average absolute miss across matched hours</div>
              <div className="metric-accent accent-blue" />
            </article>
            <article className="metric-card">
              <div className="metric-top"><span>Root mean square error</span><span className="metric-glyph">⌁</span></div>
              <div className="metric-value">{busy ? "—" : formatNumber(metrics?.rmse_mw ?? null)} <small>MW</small></div>
              <div className="metric-note">Penalizes larger forecast misses</div>
              <div className="metric-accent accent-teal" />
            </article>
            <article className="metric-card">
              <div className="metric-top"><span>Mean signed error</span><span className="metric-glyph">±</span></div>
              <div className={`metric-value ${biasPositive ? "positive" : "negative"}`}>{busy ? "—" : `${biasPositive ? "+" : "−"}${formatNumber(Math.abs(metrics?.bias_mw ?? 0))}`} <small>MW</small></div>
              <div className="metric-note">{biasPositive ? "Positive means overforecasting" : "Negative means underforecasting"}</div>
              <div className={`metric-accent ${biasPositive ? "accent-amber" : "accent-rose"}`} />
            </article>
            <article className="metric-card">
              <div className="metric-top"><span>Matched coverage</span><span className="metric-glyph">◉</span></div>
              <div className="metric-value">{busy ? "—" : `${formatNumber((metrics?.matched_coverage ?? 0) * 100)}%`} <small>of hours</small></div>
              <div className="metric-note">{metrics?.matched_intervals ?? 0} of {metrics?.expected_intervals ?? 24} delivery hours scored</div>
              <div className="metric-accent accent-green" />
            </article>
          </section>

          <div className="content-grid">
            <section className="panel chart-panel">
              <div className="panel-header"><div><h3>Actual vs selected forecast</h3><p>Hourly wind generation · {deliveryDate}</p></div><span className="unit-chip">MW</span></div>
              {busy ? <div className="chart-loading"><span className="spinner" /> Loading interval data…</div> : <Chart intervals={evaluation?.intervals ?? []} />}
              <div className="chart-footnote"><span className="info-mark">i</span> Actuals are the mean of two half-hour FUELHH records; incomplete hours are left unmatched.</div>
            </section>

            <aside className="panel method-panel" id="method">
              <div className="panel-header"><div><h3>Decision context</h3><p>What was available at the time</p></div><span className="context-icon">◷</span></div>
              <div className="context-main"><span>SELECTED VINTAGE</span><strong>{evaluation?.intervals.find((row) => row.publish_time)?.publish_time ? londonTime(evaluation.intervals.find((row) => row.publish_time)?.publish_time, { day: "2-digit", month: "short" }) : "—"}</strong><small>{evaluation?.intervals.find((row) => row.publish_time)?.publish_time ? `${londonTime(evaluation.intervals.find((row) => row.publish_time)?.publish_time)} London time` : "No eligible vintage"}</small></div>
              <div className="context-list">
                <div><span>Decision rule</span><strong>{mode === "fixed_cutoff" ? "Fixed cutoff" : "Fixed lead time"}</strong></div>
                <div><span>Decision cutoff / lead time</span><strong>{mode === "fixed_cutoff" ? `${previousDate(deliveryDate)} ${cutoff} UTC` : `${horizon} hours before each hour`}</strong></div>
                <div><span>Matched intervals</span><strong>{metrics?.matched_intervals ?? 0} / {metrics?.expected_intervals ?? 24}</strong></div>
                <div><span>Delivery date</span><strong>{deliveryDate}</strong></div>
              </div>
              <div className="exposure-note"><span className="exposure-icon">↗</span><p><strong>Error direction matters.</strong> The cost of over- or underforecasting depends on imbalance prices and the buyer’s position.</p></div>
            </aside>
          </div>

          <section className="panel interval-panel">
            <div className="panel-header"><div><h3>Hourly interval detail</h3><p>Publication timestamp is retained for each selected forecast.</p></div><span className="table-count">{evaluation?.intervals.length ?? 0} INTERVALS</span></div>
            <div className="table-scroll"><table><thead><tr><th>Delivery start <small>London</small></th><th>Forecast <small>MW</small></th><th>Published <small>London</small></th><th>Actual <small>MW</small></th><th>Error <small>MW</small></th></tr></thead>
              <tbody>{(evaluation?.intervals ?? []).map((row) => (
                <tr key={row.delivery_start}>
                  <td className="time-cell">{londonTime(row.delivery_start)}</td>
                  <td>{formatNumber(row.forecast_mw)}</td>
                  <td>{row.publish_time ? <span className="published-cell"><i /> {londonTime(row.publish_time)}</span> : <span className="missing">No eligible vintage</span>}</td>
                  <td>{formatNumber(row.actual_mw)}</td>
                  <td className={row.error_mw === null ? "missing" : row.error_mw > 0 ? "error-positive" : "error-negative"}>{row.error_mw === null ? "—" : `${row.error_mw > 0 ? "+" : "−"}${formatNumber(Math.abs(row.error_mw))}`}</td>
                </tr>
              ))}</tbody>
            </table></div>
            <div className="table-bottom">Showing all {evaluation?.intervals.length ?? 0} delivery intervals <span>·</span> All timestamps stored in UTC</div>
          </section>

          <footer className="page-footer"><div><strong>Method note</strong> · {metadata?.interpretation ?? "Forecasts are selected by publication timestamp; no forecast published after the configured decision time is used."}</div><div>Snapshot retrieved {metadata?.retrieved_at ? new Date(metadata.retrieved_at).toLocaleString("en-GB", { timeZone: "Europe/London" }) : "—"}</div></footer>
        </div>
      </main>
    </div>
  );
}

export default App;
