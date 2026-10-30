import { describe, expect, it } from "vitest";
import { defaultDashboardState, readDashboardState, writeDashboardState } from "./dashboardState";

describe("dashboard URL state", () => {
  it("restores filters and selected interval from a shared URL", () => {
    const original = {
      deliveryDate: "2026-01-20",
      mode: "fixed_horizon" as const,
      cutoff: "07:30",
      horizon: "36",
      selectedInterval: "2026-01-20T13:00:00Z",
    };
    const search = writeDashboardState(original, "?campaign=review");
    expect(readDashboardState(search)).toEqual(original);
    expect(new URLSearchParams(search).get("campaign")).toBe("review");
  });

  it("falls back to defaults for invalid values and drops an interval from another date", () => {
    expect(readDashboardState("?delivery_date=2026-02-31&mode=maybe&horizon_hours=999&cutoff=25%3A00&interval=2026-01-14T13%3A00%3A00Z")).toEqual(defaultDashboardState);
  });

  it("stores both cutoff and horizon choices with the active rule", () => {
    const search = writeDashboardState({ ...defaultDashboardState, mode: "fixed_cutoff", selectedInterval: null }, "?horizon_hours=48");
    const params = new URLSearchParams(search);
    expect(params.get("cutoff")).toBe("07:30");
    expect(params.get("horizon_hours")).toBe("24");
  });
});
