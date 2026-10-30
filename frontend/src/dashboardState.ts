export type DashboardMode = "fixed_cutoff" | "fixed_horizon";

export type DashboardState = {
  deliveryDate: string;
  mode: DashboardMode;
  cutoff: string;
  horizon: string;
  selectedInterval: string | null;
};

export const defaultDashboardState: DashboardState = {
  deliveryDate: "2026-01-15",
  mode: "fixed_cutoff",
  cutoff: "07:30",
  horizon: "24",
  selectedInterval: null,
};

const validDates = new Set<string>();
for (let date = new Date("2026-01-02T00:00:00Z"); date <= new Date("2026-02-01T00:00:00Z"); date.setUTCDate(date.getUTCDate() + 1)) {
  validDates.add(date.toISOString().slice(0, 10));
}

function isTime(value: string | null): value is string {
  return value !== null && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
}

function isTimestamp(value: string | null): value is string {
  return value !== null && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
}

export function readDashboardState(search: string): DashboardState {
  const params = new URLSearchParams(search);
  const deliveryDate = params.get("delivery_date");
  const mode = params.get("mode");
  const date = deliveryDate && validDates.has(deliveryDate) ? deliveryDate : defaultDashboardState.deliveryDate;
  const selected = params.get("interval");
  return {
    deliveryDate: date,
    mode: mode === "fixed_horizon" ? "fixed_horizon" : "fixed_cutoff",
    cutoff: isTime(params.get("cutoff")) ? params.get("cutoff")! : defaultDashboardState.cutoff,
    horizon: ["12", "24", "36", "48"].includes(params.get("horizon_hours") ?? "") ? params.get("horizon_hours")! : defaultDashboardState.horizon,
    selectedInterval: isTimestamp(selected) && selected.startsWith(`${date}T`) ? selected : null,
  };
}

export function writeDashboardState(state: DashboardState, existingSearch = ""): string {
  const params = new URLSearchParams(existingSearch);
  for (const key of ["delivery_date", "mode", "cutoff", "horizon_hours", "interval"]) params.delete(key);
  params.set("delivery_date", state.deliveryDate);
  params.set("mode", state.mode);
  params.set("cutoff", state.cutoff);
  params.set("horizon_hours", state.horizon);
  if (state.selectedInterval?.startsWith(`${state.deliveryDate}T`)) params.set("interval", state.selectedInterval);
  return params.toString();
}
