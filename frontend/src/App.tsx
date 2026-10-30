import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { readDashboardState, writeDashboardState } from "./dashboardState";
import { londonTime } from "./timeFormat";

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

type Interval = {
  delivery_start: string;
  cutoff: string;
  publish_time: string | null;
  forecast_mw: number | null;
  actual_mw: number | null;
  error_mw: number | null;
};

type Metrics = {
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

type Evaluation = {
  metrics: Metrics;
  intervals: Interval[];
};

type PeriodEvaluation = {
  decision_days: number;
  delivery_from: string;
  delivery_to_exclusive: string;
  cutoff_time_utc: string;
  metrics: Metrics;
  daily: Array<{ decision_date: string; delivery_date: string; cutoff: string; metrics: Metrics }>;
};

type LeadBucket = {
  label: string;
  lower_hours: number;
  upper_hours: number | null;
  sample_count: number;
  mae_mw: number | null;
  rmse_mw: number | null;
  bias_mw: number | null;
};

type RevisionRow = {
  delivery_start: string;
  actual_mw: number;
  decision_forecast_mw: number;
  decision_publish_time: string;
  decision_lead_hours: number;
  decision_error_mw: number;
  later_forecast_mw: number;
  later_publish_time: string;
  later_lead_hours: number;
  later_error_mw: number;
};

type ForecastAnalytics = {
  lead_time: {
    buckets: LeadBucket[];
    candidate_forecasts: number;
    observation_count: number;
    bucketed_observations: number;
    unbucketed_observations: number;
    excluded_post_delivery_count: number;
    excluded_incomplete_actual_count: number;
  };
  revision: {
    decision_time: { sample_count: number; mae_mw: number | null; rmse_mw: number | null; bias_mw: number | null };
    later_vintage: { sample_count: number; mae_mw: number | null; rmse_mw: number | null; bias_mw: number | null };
    mae_improvement_mw: number | null;
    rmse_improvement_mw: number | null;
    matched_intervals: number;
    intervals: RevisionRow[];
  };
  error_distribution: {
    p10_mw: number | null;
    p50_mw: number | null;
    p90_mw: number | null;
    sample_count: number;
    minimum_sample_count: number;
    observation_window: string;
    selection_rule: Mode;
    cutoff_time_utc: string | null;
    bands: Array<{ delivery_start: string; forecast_mw: number; p10_mw: number; p50_mw: number; p90_mw: number }>;
  };
};

type Metadata = {
  retrieved_at?: string;
  interpretation?: string;
  source?: string;
  query?: Record<string, string>;
  records?: { forecast_vintages?: number; forecasts_normalized?: number; actuals_normalized?: number; actuals_raw?: number; forecasts_raw?: number };
  coverage?: {
    forecast_publish_time_from?: string;
    forecast_publish_time_to?: string;
    actual_settlement_dates?: string[];
  };
  checksums_sha256?: Record<string, string>;
};

type Mode = "fixed_cutoff" | "fixed_horizon";

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

function formatNumber(value: number | null, digits = 0) {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value);
}

function Chart({ intervals, bands = [] }: { intervals: Interval[]; bands?: ForecastAnalytics["error_distribution"]["bands"] }) {
  const points = intervals.filter((row) => row.actual_mw !== null || row.forecast_mw !== null);
  if (!points.length) return <div className="chart-empty">No intervals to plot for this selection.</div>;

  const bandByDelivery = new Map(bands.map((band) => [band.delivery_start, band]));
  const values = [
    ...points.flatMap((row) => [row.actual_mw, row.forecast_mw].filter((value): value is number => value !== null)),
    ...bands.flatMap((band) => [band.p10_mw, band.p90_mw]),
  ];
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
  const upperBand = points.flatMap((row, index) => {
    const band = bandByDelivery.get(row.delivery_start);
    return band ? [`${x(index)},${y(band.p90_mw)}`] : [];
  });
  const lowerBand = points.flatMap((row, index) => {
    const band = bandByDelivery.get(row.delivery_start);
    return band ? [`${x(index)},${y(band.p10_mw)}`] : [];
  }).reverse();
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
        {upperBand.length > 1 && <polygon points={[...upperBand, ...lowerBand].join(" ")} className="forecast-error-band" />}
        <polyline points={line("forecast_mw")} fill="none" className="line-forecast" />
        <polyline points={line("actual_mw")} fill="none" className="line-actual" />
        {points.map((row, index) => row.forecast_mw === null ? null : (
          <circle key={`f-${row.delivery_start}`} cx={x(index)} cy={y(row.forecast_mw)} r="3" className="dot-forecast">
            <title>{`${londonTime(row.delivery_start)} · forecast ${formatNumber(row.forecast_mw)} MW · published ${londonTime(row.publish_time)}`}</title>
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
        {upperBand.length > 1 && <span><i className="legend-band" /> Empirical P10–P90 error range</span>}
        <span className="chart-unit">MW · delivery start in London time</span>
      </div>
    </div>
  );
}

function LeadTimeChart({ buckets }: { buckets: LeadBucket[] }) {
  const [showRmse, setShowRmse] = useState(true);
  if (!buckets.length) return <div className="chart-empty">No matched forecasts were available in the lead-time buckets for this period.</div>;
  const width = 760;
  const height = 265;
  const left = 54;
  const right = 18;
  const top = 16;
  const bottom = 62;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const values = buckets.flatMap((bucket) => [bucket.mae_mw, showRmse ? bucket.rmse_mw : null].filter((value): value is number => value !== null));
  const maximum = Math.max(1, ...values) * 1.12;
  const x = (index: number) => left + (buckets.length <= 1 ? chartWidth / 2 : index * chartWidth / (buckets.length - 1));
  const y = (value: number) => top + chartHeight - value / maximum * chartHeight;
  const points = (key: "mae_mw" | "rmse_mw") => buckets.map((bucket, index) => bucket[key] === null ? null : `${x(index)},${y(bucket[key]!)}`).filter((point): point is string => point !== null).join(" ");
  const ticks = [0, maximum / 2, maximum];
  return (
    <div className="analytics-chart-wrap">
      <div className="analytics-chart-controls"><span>Error (MW)</span><label><input type="checkbox" checked={showRmse} onChange={(event) => setShowRmse(event.target.checked)} /> Show RMSE</label></div>
      <svg className="analytics-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Forecast MAE and RMSE by lead time">
        {ticks.map((tick) => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="chart-grid" /><text x={left - 8} y={y(tick) + 3} textAnchor="end" className="chart-axis">{formatNumber(tick)}</text></g>)}
        <polyline points={points("mae_mw")} fill="none" className="line-actual" />
        {showRmse && <polyline points={points("rmse_mw")} fill="none" className="line-forecast" />}
        {buckets.map((bucket, index) => <g key={bucket.label}>
          {bucket.mae_mw !== null && <circle cx={x(index)} cy={y(bucket.mae_mw)} r="4" className="dot-actual"><title>{`${bucket.label}: MAE ${formatNumber(bucket.mae_mw, 1)} MW · RMSE ${formatNumber(bucket.rmse_mw, 1)} MW · bias ${formatNumber(bucket.bias_mw, 1)} MW · n=${bucket.sample_count}`}</title></circle>}
          {showRmse && bucket.rmse_mw !== null && <circle cx={x(index)} cy={y(bucket.rmse_mw)} r="3" className="dot-forecast"><title>{`${bucket.label}: RMSE ${formatNumber(bucket.rmse_mw, 1)} MW · n=${bucket.sample_count}`}</title></circle>}
          <text x={x(index)} y={height - 34} textAnchor="middle" className="chart-axis">{bucket.label}</text>
          <text x={x(index)} y={height - 18} textAnchor="middle" className="bucket-count">n={bucket.sample_count}</text>
        </g>)}
      </svg>
      <div className="chart-legend"><span><i className="legend-line actual" /> MAE</span>{showRmse && <span><i className="legend-line forecast" /> RMSE</span>}<span className="chart-unit">Lead time = delivery start − publication time</span></div>
    </div>
  );
}

function RevisionChart({ rows }: { rows: RevisionRow[] }) {
  if (!rows.length) return <div className="chart-empty">No intervals had both a decision-time forecast and a later pre-delivery vintage.</div>;
  const width = 920;
  const height = 220;
  const left = 54;
  const right = 18;
  const top = 14;
  const bottom = 31;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const maximum = Math.max(1, ...rows.flatMap((row) => [Math.abs(row.decision_error_mw), Math.abs(row.later_error_mw)])) * 1.1;
  const x = (index: number) => left + (rows.length <= 1 ? chartWidth / 2 : index * chartWidth / (rows.length - 1));
  const y = (value: number) => top + chartHeight - Math.abs(value) / maximum * chartHeight;
  const line = (field: "decision_error_mw" | "later_error_mw") => rows.map((row, index) => `${x(index)},${y(row[field])}`).join(" ");
  return <div className="analytics-chart-wrap">
    <div className="revision-chart-label">Absolute error (MW)</div>
    <svg className="revision-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Absolute forecast error at decision time compared with later forecasts">
      {[0, maximum / 2, maximum].map((tick) => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="chart-grid" /><text x={left - 8} y={y(tick) + 3} textAnchor="end" className="chart-axis">{formatNumber(tick)}</text></g>)}
      <polyline points={line("decision_error_mw")} fill="none" className="line-actual" />
      <polyline points={line("later_error_mw")} fill="none" className="line-forecast" />
      {rows.map((row, index) => <g key={row.delivery_start}>
        <circle cx={x(index)} cy={y(row.decision_error_mw)} r="3" className="dot-actual"><title>{`${londonTime(row.delivery_start)} · decision error ${formatNumber(Math.abs(row.decision_error_mw))} MW · published ${londonTime(row.decision_publish_time)}`}</title></circle>
        <circle cx={x(index)} cy={y(row.later_error_mw)} r="3" className="dot-forecast"><title>{`${londonTime(row.delivery_start)} · later error ${formatNumber(Math.abs(row.later_error_mw))} MW · published ${londonTime(row.later_publish_time)}`}</title></circle>
      </g>)}
      {[0, Math.floor((rows.length - 1) / 2), rows.length - 1].map((index) => <text key={index} x={x(index)} y={height - 8} textAnchor="middle" className="chart-axis">{londonTime(rows[index].delivery_start)}</text>)}
    </svg>
    <div className="chart-legend"><span><i className="legend-line actual" /> Decision time</span><span><i className="legend-line forecast" /> Later vintage</span></div>
  </div>;
}

function App() {
  const [initialState] = useState(() => readDashboardState(window.location.search));
  const [deliveryDate, setDeliveryDate] = useState(initialState.deliveryDate);
  const [mode, setMode] = useState<Mode>(initialState.mode);
  const [cutoff, setCutoff] = useState(initialState.cutoff);
  const [horizon, setHorizon] = useState(initialState.horizon);
  const [selectedInterval, setSelectedInterval] = useState<string | null>(initialState.selectedInterval);

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

  const periodQuery = useMemo(() => new URLSearchParams({ cutoff_time_utc: cutoff }).toString(), [cutoff]);
  const evaluationQuery = useQuery({ queryKey: ["evaluation", query], queryFn: ({ signal }) => fetchJson<Evaluation>(`/api/evaluation?${query}`, signal) });
  const metadataQuery = useQuery({ queryKey: ["metadata"], queryFn: ({ signal }) => fetchJson<Metadata>("/api/metadata", signal), staleTime: 300_000 });
  const periodQueryResult = useQuery({ queryKey: ["january-evaluation", periodQuery], queryFn: ({ signal }) => fetchJson<PeriodEvaluation>(`/api/january-evaluation?${periodQuery}`, signal) });
  const analyticsQuery = useQuery({ queryKey: ["forecast-analytics", query], queryFn: ({ signal }) => fetchJson<ForecastAnalytics>(`/api/forecast-analytics?${query}`, signal) });
  const evaluation = evaluationQuery.data;
  const periodEvaluation = periodQueryResult.data;
  const analytics = analyticsQuery.data;
  const metadata = metadataQuery.data;
  const busy = evaluationQuery.isLoading;
  const periodBusy = periodQueryResult.isLoading;
  const analyticsBusy = analyticsQuery.isLoading;
  const error = evaluationQuery.error instanceof Error ? evaluationQuery.error.message : null;
  const periodError = periodQueryResult.error instanceof Error ? periodQueryResult.error.message : null;
  const analyticsError = analyticsQuery.error instanceof Error ? analyticsQuery.error.message : null;

  useEffect(() => {
    const state = { deliveryDate, mode, cutoff, horizon, selectedInterval };
    const search = writeDashboardState(state, window.location.search);
    const nextUrl = `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`;
    window.history.replaceState(null, "", nextUrl);
  }, [deliveryDate, mode, cutoff, horizon, selectedInterval]);

  useEffect(() => {
    const restore = () => {
      const state = readDashboardState(window.location.search);
      setDeliveryDate(state.deliveryDate);
      setMode(state.mode);
      setCutoff(state.cutoff);
      setHorizon(state.horizon);
      setSelectedInterval(state.selectedInterval);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  const metrics = evaluation?.metrics;
  const label = mode === "fixed_cutoff" ? `${previousDate(deliveryDate)} · ${cutoff} UTC cutoff` : `${horizon}h lead time`;
  const bias = metrics?.bias_mw;
  const biasPositive = (bias ?? 0) > 0;
  const biasNegative = (bias ?? 0) < 0;

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
              <label className="field"><span>Delivery date</span><input type="date" value={deliveryDate} onChange={(event) => { setDeliveryDate(event.target.value); setSelectedInterval(null); }} min="2026-01-02" max="2026-02-01" /></label>
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

          <section className="panel period-panel" aria-label="January day-ahead summary">
            <div className="panel-header">
              <div><h3>January day-ahead summary</h3><p>31 decision dates · delivery dates Jan 2–Feb 1 · previous-day cutoff at {periodEvaluation?.cutoff_time_utc ?? cutoff} UTC</p></div>
              <span className="period-chip">FULL PERIOD</span>
            </div>
            <div className="period-stats">
              <div><span>Mean absolute error</span><strong>{periodBusy ? "—" : formatNumber(periodEvaluation?.metrics.mae_mw ?? null)} <small>MW</small></strong></div>
              <div><span>Root mean square error</span><strong>{periodBusy ? "—" : formatNumber(periodEvaluation?.metrics.rmse_mw ?? null)} <small>MW</small></strong></div>
              <div><span>Mean signed error</span><strong>{periodBusy || periodEvaluation?.metrics.bias_mw == null ? "—" : `${periodEvaluation.metrics.bias_mw >= 0 ? "+" : "−"}${formatNumber(Math.abs(periodEvaluation.metrics.bias_mw))}`} <small>MW</small></strong></div>
              <div><span>Matched coverage</span><strong>{periodBusy || !periodEvaluation ? "—" : `${formatNumber(periodEvaluation.metrics.matched_coverage * 100, 1)}%`} <small>{periodBusy || !periodEvaluation ? "of hours" : `${periodEvaluation.metrics.matched_intervals}/${periodEvaluation.metrics.expected_intervals} hours`}</small></strong></div>
            </div>
            <div className="period-note">
              Expected {periodEvaluation?.metrics.expected_intervals ?? "—"} hours · forecasts available {periodEvaluation?.metrics.forecast_available ?? "—"} · actuals available {periodEvaluation?.metrics.actual_available ?? "—"} · matched {periodEvaluation?.metrics.matched_intervals ?? "—"}.
              Matched coverage requires both an eligible forecast and a complete actual pair; it does not count all forecast vintages. Select a delivery date below to inspect its 24 hourly intervals.
            </div>
            {periodError && <div className="period-note" role="alert">{periodError}</div>}
          </section>

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
              <div className="metric-note">
                {busy ? "Calculating signed error…" : biasPositive ? "Systematic wind overforecasting — potential shortfall exposure." : biasNegative ? "Systematic wind underforecasting — potential surplus exposure." : "No average signed bias in the matched hours."}
                <span> Financial effects also depend on demand, procurement positions, and imbalance prices.</span>
              </div>
              <div className={`metric-accent ${biasPositive ? "accent-amber" : "accent-rose"}`} />
            </article>
            <article className="metric-card">
              <div className="metric-top"><span>Matched coverage</span><span className="metric-glyph">◉</span></div>
              <div className="metric-value">{busy ? "—" : `${formatNumber((metrics?.matched_coverage ?? 0) * 100)}%`} <small>of hours</small></div>
              <div className="metric-note">Expected {metrics?.expected_intervals ?? 24} · actuals {metrics?.actual_available ?? 0} · eligible forecasts {metrics?.forecast_available ?? 0} · matched {metrics?.matched_intervals ?? 0}</div>
              <div className="coverage-caption">Coverage checks one selected forecast and actual per hour; it does not count every available vintage.</div>
              <div className="metric-accent accent-green" />
            </article>
          </section>

          <div className="content-grid">
            <section className="panel chart-panel">
              <div className="panel-header"><div><h3>Actual vs selected forecast</h3><p>Hourly wind generation · {deliveryDate}</p></div><span className="unit-chip">MW</span></div>
              {busy ? <div className="chart-loading"><span className="spinner" /> Loading interval data…</div> : <Chart intervals={evaluation?.intervals ?? []} bands={analytics?.error_distribution.bands} />}
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
              <div className="exposure-note"><span className="exposure-icon">↗</span><p><strong>Error = forecast − actual.</strong> Positive means wind was overforecast and may leave a buyer short if they relied on it; negative means underforecasting and may leave excess purchases; zero is an exact forecast. Which direction costs more depends on demand, procurement positions, and imbalance prices.</p></div>
            </aside>
          </div>

          <section className="panel interval-panel">
            <div className="panel-header"><div><h3>Hourly interval detail</h3><p>Select a row to save that delivery interval in the URL. Publication timestamps are retained.</p></div><span className="table-count">{evaluation?.intervals.length ?? 0} INTERVALS</span></div>
            <div className="table-scroll"><table><thead><tr><th>Delivery start <small>London</small></th><th>Forecast <small>MW</small></th><th>Published <small>London</small></th><th>Actual <small>MW</small></th><th>Error <small>MW</small></th></tr></thead>
              <tbody>{(evaluation?.intervals ?? []).map((row) => (
                <tr key={row.delivery_start} className={selectedInterval === row.delivery_start ? "selected-interval" : undefined} onClick={() => setSelectedInterval(selectedInterval === row.delivery_start ? null : row.delivery_start)} aria-selected={selectedInterval === row.delivery_start} tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedInterval(selectedInterval === row.delivery_start ? null : row.delivery_start); } }}>
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

          <section className="panel analytics-panel" aria-label="Forecast accuracy by lead time">
            <div className="panel-header"><div><h3>Forecast Accuracy by Lead Time</h3><p>Selected delivery period · only forecast vintages published by delivery are scored</p></div><span className="table-count">{analytics?.lead_time.observation_count ?? 0} OBSERVATIONS</span></div>
            {analyticsError && <div className="analytics-message" role="alert">{analyticsError}</div>}
            {analyticsBusy ? <div className="chart-loading"><span className="spinner" /> Calculating lead-time accuracy…</div> : <LeadTimeChart buckets={analytics?.lead_time.buckets ?? []} />}
            <div className="revision-method-note">Eligible observations: {analytics?.lead_time.observation_count ?? 0} · bucketed: {analytics?.lead_time.bucketed_observations ?? 0} · unbucketed: {analytics?.lead_time.unbucketed_observations ?? 0} · excluded after delivery: {analytics?.lead_time.excluded_post_delivery_count ?? 0} · excluded for incomplete actuals: {analytics?.lead_time.excluded_incomplete_actual_count ?? 0}. Each eligible vintage is counted separately for its delivery interval.</div>
          </section>

          <section className="panel analytics-panel" aria-label="Forecast revision impact">
            <div className="panel-header"><div><h3>Forecast Revision Impact</h3><p>Retrospective comparison on the same intervals; later vintages are published after the decision cutoff and by delivery.</p></div><span className="table-count">{analytics?.revision.matched_intervals ?? 0} MATCHED</span></div>
            {analyticsBusy ? <div className="chart-loading"><span className="spinner" /> Comparing forecast vintages…</div> : analyticsError ? <div className="analytics-message" role="alert">{analyticsError}</div> : analytics?.revision.matched_intervals ? <>
              <div className="revision-kpis">
                <article><span>MAE · decision vs later</span><strong>{formatNumber(analytics.revision.decision_time.mae_mw)} <i>→</i> {formatNumber(analytics.revision.later_vintage.mae_mw)} <small>MW</small></strong><div>Improvement: {formatNumber(analytics.revision.mae_improvement_mw, 1)} MW · positive means later was more accurate</div></article>
                <article><span>RMSE · decision vs later</span><strong>{formatNumber(analytics.revision.decision_time.rmse_mw)} <i>→</i> {formatNumber(analytics.revision.later_vintage.rmse_mw)} <small>MW</small></strong><div>Improvement: {formatNumber(analytics.revision.rmse_improvement_mw, 1)} MW · positive means later was more accurate</div></article>
              </div>
              <div className="revision-method-note">A retrospective forecast-revision comparison; it does not represent financial savings. The same matched intervals are used for both forecasts.</div>
              <RevisionChart rows={analytics.revision.intervals} />
              <div className="table-scroll"><table><thead><tr><th>Delivery <small>London</small></th><th>Actual <small>MW</small></th><th>Decision forecast <small>MW</small></th><th>Published <small>London</small></th><th>Lead</th><th>Decision error</th><th>Later forecast <small>MW</small></th><th>Published <small>London</small></th><th>Lead</th><th>Later error</th></tr></thead>
                <tbody>{analytics.revision.intervals.map((row) => <tr key={row.delivery_start}>
                  <td className="time-cell">{londonTime(row.delivery_start)}</td><td>{formatNumber(row.actual_mw)}</td><td>{formatNumber(row.decision_forecast_mw)}</td><td>{londonTime(row.decision_publish_time)}</td><td>{formatNumber(row.decision_lead_hours, 1)} h</td><td>{formatNumber(row.decision_error_mw, 1)}</td><td>{formatNumber(row.later_forecast_mw)}</td><td>{londonTime(row.later_publish_time)}</td><td>{formatNumber(row.later_lead_hours, 1)} h</td><td>{formatNumber(row.later_error_mw, 1)}</td>
                </tr>)}</tbody>
              </table></div>
              <div className="revision-method-note">Scroll the table horizontally to inspect the later forecast, publication time, lead, and error columns.</div>
            </> : <div className="chart-empty">No intervals had both a decision-time forecast and a later pre-delivery vintage.</div>}
          </section>

          <section className="panel analytics-panel" aria-label="Historical forecast error distribution">
            <div className="panel-header"><div><h3>Historical Forecast Error Distribution</h3><p>{analytics?.error_distribution.observation_window ?? "January 2026 decision dates"} · {mode === "fixed_cutoff" ? "previous-day cutoff" : `${horizon}-hour lead-time rule`}</p></div><span className="table-count">n={analytics?.error_distribution.sample_count ?? 0}</span></div>
            {analyticsBusy ? <div className="chart-loading"><span className="spinner" /> Calculating empirical error range…</div> : analyticsError ? <div className="analytics-message" role="alert">{analyticsError}</div> : analytics && analytics.error_distribution.sample_count >= analytics.error_distribution.minimum_sample_count ? <>
              <div className="distribution-stats">
                <article><span>P10 error</span><strong>{formatNumber(analytics.error_distribution.p10_mw, 1)} <small>MW</small></strong></article>
                <article><span>P50 error</span><strong>{formatNumber(analytics.error_distribution.p50_mw, 1)} <small>MW</small></strong></article>
                <article><span>P90 error</span><strong>{formatNumber(analytics.error_distribution.p90_mw, 1)} <small>MW</small></strong></article>
              </div>
              <div className="revision-method-note">The shaded chart range adds these signed-error quantiles to the selected forecast. This is an empirical historical range, not a guaranteed prediction interval. Minimum sample required: {analytics.error_distribution.minimum_sample_count} matched observations.</div>
            </> : <div className="chart-empty">Quantiles are withheld: {analytics?.error_distribution.sample_count ?? 0} matched observations; at least {analytics?.error_distribution.minimum_sample_count ?? 30} are required.</div>}
          </section>

          <section className="panel methodology-panel" id="methodology">
            <details>
              <summary>Methodology &amp; Data Quality</summary>
              <div className="methodology-content">
                {metadataQuery.error instanceof Error && <div className="analytics-message" role="alert">Snapshot metadata could not be loaded: {metadataQuery.error.message}</div>}
                <div><h4>Sources and snapshot</h4><p>Actual wind generation comes from Elexon <a href="https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream" target="_blank" rel="noreferrer">FUELHH</a> filtered to WIND; hourly forecast vintages come from <a href="https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream" target="_blank" rel="noreferrer">WINDFOR</a>. {metadata?.interpretation ?? "January refers to publication and decision dates; delivery starts can extend into February."}</p><p>Retrieved {metadata?.retrieved_at ? new Date(metadata.retrieved_at).toLocaleString("en-GB", { timeZone: "Europe/London", dateStyle: "medium", timeStyle: "short" }) + " Europe/London" : "snapshot timestamp unavailable"} · {metadata?.records?.forecasts_normalized ?? "—"} forecast rows across {metadata?.records?.forecast_vintages ?? "—"} vintages · {metadata?.records?.actuals_normalized ?? "—"} actual rows.</p>{metadata?.coverage?.actual_settlement_dates?.length ? <p>Actual settlement-date coverage: {metadata.coverage.actual_settlement_dates[0]} through {metadata.coverage.actual_settlement_dates[metadata.coverage.actual_settlement_dates.length - 1]}. Adjacent boundary dates support January’s delivery window; the January day-ahead summary evaluates only its defined delivery dates.</p> : null}</div>
                <div><h4>Selection and time handling</h4><p>All saved timestamps are UTC. The dashboard displays them in Europe/London. Fixed cutoff selects the latest publication at or before the configured previous-day UTC cutoff. Fixed horizon selects the latest publication at or before delivery start minus the chosen horizon. Later publications never replace a historical decision forecast.</p></div>
                <div><h4>Alignment and missing values</h4><p>Each hourly forecast is compared with the arithmetic mean of its two equal-duration half-hour FUELHH observations. Incomplete pairs remain missing and do not enter matched metrics. Missing forecasts and actuals are reported separately; no values are filled with zero.</p></div>
                <div><h4>Metrics and hindsight comparison</h4><p>Error is forecast minus actual: positive is overforecasting and negative is underforecasting. Financial impact cannot be inferred without demand, procurement positions, and imbalance prices. The revision comparison uses the same intervals and the latest forecast published after decision time but no later than delivery; it describes retrospective accuracy, not savings.</p></div>
                <div><h4>Snapshot checksums</h4><ul>{Object.entries(metadata?.checksums_sha256 ?? {}).map(([file, checksum]) => <li key={file}><code>{file}</code> · <code>{checksum}</code></li>)}</ul></div>
              </div>
            </details>
          </section>

          <footer className="page-footer"><div><strong>Method note</strong> · {metadata?.interpretation ?? "Forecasts are selected by publication timestamp; no forecast published after the configured decision time is used."}</div><div>Snapshot retrieved {metadata?.retrieved_at ? new Date(metadata.retrieved_at).toLocaleString("en-GB", { timeZone: "Europe/London" }) : "—"}</div></footer>
        </div>
      </main>
    </div>
  );
}

export default App;
