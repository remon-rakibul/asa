import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import StatsRow from "@/components/dashboard/StatsRow";

const SAMPLE_STATS = {
  today_count: 5,
  week_count: 23,
  available_today: 8,
  cancellations_week: 2,
};

describe("StatsRow", () => {
  it("renders today's appointment count", () => {
    render(<StatsRow stats={SAMPLE_STATS} />);
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("Today's appointments")).toBeInTheDocument();
  });

  it("renders weekly appointment count", () => {
    render(<StatsRow stats={SAMPLE_STATS} />);
    expect(screen.getByText("23")).toBeInTheDocument();
    expect(screen.getByText("This week")).toBeInTheDocument();
  });

  it("renders available today count", () => {
    render(<StatsRow stats={SAMPLE_STATS} />);
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("Available today")).toBeInTheDocument();
  });

  it("renders cancellations count", () => {
    render(<StatsRow stats={SAMPLE_STATS} />);
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Cancellations (7d)")).toBeInTheDocument();
  });

  it("renders four stat cards", () => {
    const { container } = render(<StatsRow stats={SAMPLE_STATS} />);
    expect(container.querySelectorAll(".card").length).toBe(4);
  });
});
