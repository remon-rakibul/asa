import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import StatusBadge from "@/components/ui/StatusBadge";

describe("StatusBadge", () => {
  it("renders 'Confirmed' with badge-success class for confirmed status", () => {
    render(<StatusBadge status="confirmed" />);
    const badge = screen.getByText("Confirmed");
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain("badge-success");
  });

  it("renders 'Cancelled' with badge-danger class for cancelled status", () => {
    render(<StatusBadge status="cancelled" />);
    const badge = screen.getByText("Cancelled");
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain("badge-danger");
  });

  it("does not render badge-danger class for confirmed status", () => {
    render(<StatusBadge status="confirmed" />);
    expect(screen.getByText("Confirmed").className).not.toContain("badge-danger");
  });

  it("does not render badge-success class for cancelled status", () => {
    render(<StatusBadge status="cancelled" />);
    expect(screen.getByText("Cancelled").className).not.toContain("badge-success");
  });
});
