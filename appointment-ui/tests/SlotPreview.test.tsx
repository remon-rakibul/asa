import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import SlotPreview, { slotCount } from "@/components/schedule/SlotPreview";

describe("slotCount", () => {
  it("returns 16 for 09:00–17:00 with 30-min slots", () => {
    expect(slotCount("09:00", "17:00", 30)).toBe(16);
  });

  it("returns 0 when start equals end", () => {
    expect(slotCount("09:00", "09:00", 30)).toBe(0);
  });

  it("returns 0 when end is before start", () => {
    expect(slotCount("17:00", "09:00", 30)).toBe(0);
  });

  it("floors partial slots", () => {
    expect(slotCount("09:00", "09:45", 30)).toBe(1);
  });

  it("handles 15-min slots", () => {
    expect(slotCount("09:00", "10:00", 15)).toBe(4);
  });
});

describe("SlotPreview component", () => {
  it("shows slot count when active", () => {
    render(
      <SlotPreview startTime="09:00" endTime="17:00" slotDuration={30} active={true} />
    );
    expect(screen.getByText("16 slots/day")).toBeInTheDocument();
  });

  it("shows dash when inactive", () => {
    render(
      <SlotPreview startTime="09:00" endTime="17:00" slotDuration={30} active={false} />
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("uses singular 'slot' for count of 1", () => {
    render(
      <SlotPreview startTime="09:00" endTime="09:30" slotDuration={30} active={true} />
    );
    expect(screen.getByText("1 slot/day")).toBeInTheDocument();
  });
});
