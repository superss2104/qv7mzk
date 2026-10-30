import { describe, expect, it } from "vitest";
import { londonTime } from "./timeFormat";

describe("London timestamp display", () => {
  it("converts UTC timestamps using the winter offset", () => {
    expect(londonTime("2026-01-15T09:00:00Z")).toBe("09:00");
  });

  it("converts UTC timestamps using the summer offset", () => {
    expect(londonTime("2026-07-15T09:00:00Z")).toBe("10:00");
  });

  it("formats a missing timestamp explicitly", () => {
    expect(londonTime(null)).toBe("—");
  });
});
