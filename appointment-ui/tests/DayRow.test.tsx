import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import DayRow from "@/components/schedule/DayRow";
import type { ScheduleRow } from "@/types";

const MONDAY_ROW: ScheduleRow = {
  day_of_week: 0,
  start_time: "09:00",
  end_time: "17:00",
  slot_duration: 30,
  active: true,
};

describe("DayRow", () => {
  it("renders the weekday label", () => {
    const onChange = vi.fn();
    render(
      <table>
        <tbody>
          <DayRow row={MONDAY_ROW} onChange={onChange} />
        </tbody>
      </table>
    );
    expect(screen.getByText("Monday")).toBeInTheDocument();
  });

  it("shows slot preview for active row", () => {
    render(
      <table>
        <tbody>
          <DayRow row={MONDAY_ROW} onChange={vi.fn()} />
        </tbody>
      </table>
    );
    expect(screen.getByText("16 slots/day")).toBeInTheDocument();
  });

  it("shows dash when row is inactive", () => {
    render(
      <table>
        <tbody>
          <DayRow row={{ ...MONDAY_ROW, active: false }} onChange={vi.fn()} />
        </tbody>
      </table>
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("calls onChange with toggled active when switch is clicked", () => {
    const onChange = vi.fn();
    render(
      <table>
        <tbody>
          <DayRow row={MONDAY_ROW} onChange={onChange} />
        </tbody>
      </table>
    );
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith({ ...MONDAY_ROW, active: false });
  });

  it("time inputs are disabled when row is inactive", () => {
    render(
      <table>
        <tbody>
          <DayRow row={{ ...MONDAY_ROW, active: false }} onChange={vi.fn()} />
        </tbody>
      </table>
    );
    const inputs = screen.getAllByRole("combobox").concat(
      screen.queryAllByDisplayValue("09:00")
    );
    screen.getAllByDisplayValue("09:00").forEach((el) => {
      expect(el).toBeDisabled();
    });
  });
});
