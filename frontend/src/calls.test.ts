import { describe, expect, it } from "vitest";
import { tickStep } from "./components/CallsChart";
import { dateRange, formatDuration, formatTalkTime, formatTalkTimeShort, isoDate } from "./format";

describe("call formatting", () => {
  it("formats durations", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(75)).toBe("1:15");
    expect(formatDuration(3725)).toBe("1:02:05");
    expect(formatTalkTime(59 * 60)).toBe("59 min");
    expect(formatTalkTime(12 * 3600 + 5 * 60)).toBe("12 h 5 min");
    expect(formatTalkTimeShort(45 * 60)).toBe("45 min");
    expect(formatTalkTimeShort(4 * 3600 + 53 * 60)).toBe("4.9 h");
    expect(formatTalkTimeShort(123 * 3600)).toBe("123 h");
  });

  it("builds local-midnight date ranges", () => {
    const now = new Date(2026, 8, 30, 15, 45); // 30 Sep, afternoon
    const today = dateRange("today", null, null, now);
    expect(today.start).toEqual(new Date(2026, 8, 30));
    expect(today.end).toEqual(new Date(2026, 9, 1));
    const week = dateRange("7d", null, null, now);
    expect(week.start).toEqual(new Date(2026, 8, 24));
    const custom = dateRange("custom", "2026-09-01", "2026-09-03", now);
    expect(custom.start).toEqual(new Date(2026, 8, 1));
    expect(custom.end).toEqual(new Date(2026, 8, 4));
    // A backwards custom range falls back to 7 days.
    expect(dateRange("custom", "2026-09-05", "2026-09-01", now).start).toEqual(week.start);
    expect(isoDate(new Date(2026, 0, 5))).toBe("2026-01-05");
  });

  it("picks clean chart tick steps", () => {
    expect(tickStep(3)).toBe(1);
    expect(tickStep(9)).toBe(5);
    expect(tickStep(37)).toBe(10);
    expect(tickStep(180)).toBe(50);
    expect(tickStep(1234)).toBe(500);
  });
});
