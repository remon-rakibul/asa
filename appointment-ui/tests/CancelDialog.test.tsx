import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the API client before importing the component
vi.mock("@/lib/api", () => ({
  cancelAppointment: vi.fn().mockResolvedValue({ ok: true }),
}));

import CancelDialog from "@/components/appointments/CancelDialog";
import { cancelAppointment } from "@/lib/api";

describe("CancelDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders Cancel button initially without dialog", () => {
    render(<CancelDialog appointmentId="abc-123" patientName="রাহেলা বেগম" />);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.queryByText("Cancel appointment")).not.toBeInTheDocument();
  });

  it("opens dialog when Cancel button is clicked", () => {
    render(<CancelDialog appointmentId="abc-123" patientName="রাহেলা বেগম" />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getAllByText("Cancel appointment").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/রাহেলা বেগম/)).toBeInTheDocument();
  });

  it("closes dialog when Keep it is clicked", () => {
    render(<CancelDialog appointmentId="abc-123" patientName="রাহেলা বেগম" />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep it" }));
    expect(screen.queryByText("Cancel appointment")).not.toBeInTheDocument();
  });

  it("calls cancelAppointment with the appointment id on confirm", async () => {
    render(<CancelDialog appointmentId="abc-123" patientName="রাহেলা বেগম" />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel appointment" }));
    await waitFor(() => {
      expect(cancelAppointment).toHaveBeenCalledWith("abc-123");
    });
  });
});
