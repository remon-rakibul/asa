import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { LangProvider } from "@/lib/i18n";
import BookingSheet from "@/components/portal/BookingSheet";
import { ApiError } from "@/lib/api";

vi.mock("@/lib/patientAuth", () => ({
  usePatientAuth: () => ({
    account: { id: 7, name: "Kodu", phone: "01711000000", email: "k@a.com", created_at: "" },
  }),
}));

const bookMock = vi.fn();
const getPaymentMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...mod,
    portalBookAppointment: (...args: unknown[]) => bookMock(...args),
    portalGetPayment: (...args: unknown[]) => getPaymentMock(...args),
    portalDoctorDetail: vi.fn(),
  };
});

const DOC = {
  id: 5, clinic_id: 2, name: "Dr. Rahim", specialty: "Cardiology",
  degrees: "MBBS", description: "", has_photo: false,
  fee_new: 800, fee_followup: 500,
  department_name: "Cardiology", hospital_id: 1, hospital_name: "City Hospital",
  avg_rating: 4.5, review_count: 12, next_slot: null,
  slots: [
    { datetime: "2026-07-13T09:00:00", label: "সোমবার সকাল ৯টা" },
    { datetime: "2026-07-13T09:30:00", label: "সোমবার সকাল সাড়ে ৯টা" },
  ],
};

function renderSheet(slot = DOC.slots[0]) {
  return render(
    <LangProvider>
      <BookingSheet doc={DOC} slot={slot} onClose={() => {}} onBookWithAI={() => {}} />
    </LangProvider>,
  );
}

// No beforeEach clearing bookMock: vitest 4 then mis-attributes the 409
// test's Promise.reject implementation as an unhandled rejection. Each test
// sets its own implementation, and the only call-count assertion runs before
// any other test invokes the mock.
describe("BookingSheet", () => {
  it("prefills name and phone from the account", () => {
    renderSheet();
    expect(screen.getByDisplayValue("Kodu")).toBeInTheDocument();
    expect(screen.getByDisplayValue("01711000000")).toBeInTheDocument();
  });

  it("books with the tapped slot and shows the serial number", async () => {
    bookMock.mockResolvedValue({ id: "apt-1", serial_number: 3, slot_label: DOC.slots[0].label });
    renderSheet();
    fireEvent.change(screen.getByPlaceholderText("—"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: /বুকিং নিশ্চিত করুন|Confirm booking/ }));

    await waitFor(() => expect(bookMock).toHaveBeenCalledOnce());
    expect(bookMock).toHaveBeenCalledWith({
      clinic_id: 2,
      doctor_id: 5,
      slot_datetime: "2026-07-13T09:00:00",
      slot_label: "সোমবার সকাল ৯টা",
      patient_name: "Kodu",
      patient_age: 30,
      patient_mobile: "01711000000",
    });
    expect(await screen.findByText(/#3/)).toBeInTheDocument();
  });

  it("requires a valid age before enabling confirm", () => {
    renderSheet();
    const btn = screen.getByRole("button", { name: /বুকিং নিশ্চিত করুন|Confirm booking/ });
    expect(btn).toBeDisabled();
  });

  it("shows the pay card when a booking fee holds the slot", async () => {
    getPaymentMock.mockResolvedValue({
      id: "pay-9", status: "initiated", amount: 30, currency: "BDT",
      appointment_id: "apt-2", appointment_status: "pending_payment",
    });
    const openSpy = vi.fn();
    vi.stubGlobal("open", openSpy);
    bookMock.mockResolvedValue({
      id: "apt-2", serial_number: null, slot_label: DOC.slots[0].label,
      status: "pending_payment",
      payment: {
        payment_id: "pay-9", amount: 30, currency: "BDT",
        pay_url: "https://gw.example/pay/abc",
        expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
      },
    });
    renderSheet();
    fireEvent.change(screen.getByPlaceholderText("—"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: /বুকিং নিশ্চিত করুন|Confirm booking/ }));

    // Pay card appears, the gateway tab is opened, and a pay link is offered.
    expect(await screen.findByText(/বুকিং ফি পরিশোধ করুন|Pay the booking fee/)).toBeInTheDocument();
    expect(openSpy).toHaveBeenCalledWith("https://gw.example/pay/abc", "_blank", "noopener");
    const link = screen.getByRole("link", { name: /এখন পরিশোধ করুন|Pay now/ });
    expect(link).toHaveAttribute("href", "https://gw.example/pay/abc");
    vi.unstubAllGlobals();
  });

  it("shows the slot-taken recovery on 409", async () => {
    bookMock.mockImplementation(() => Promise.reject(new ApiError(409, "slot_taken")));
    renderSheet();
    fireEvent.change(screen.getByPlaceholderText("—"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: /বুকিং নিশ্চিত করুন|Confirm booking/ }));
    expect(await screen.findByText(/অন্য কেউ নিয়ে নিয়েছেন|just taken/)).toBeInTheDocument();
  });
});
