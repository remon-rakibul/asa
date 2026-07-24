import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import PlatformDashboardPage from "@/app/platform/page";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

vi.mock("@/lib/toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

const getMeMock = vi.fn();
const overviewMock = vi.fn();
const paymentsMock = vi.fn();
const markHospMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...mod,
    getMe: (...a: unknown[]) => getMeMock(...a),
    platformOverview: (...a: unknown[]) => overviewMock(...a),
    platformPayments: (...a: unknown[]) => paymentsMock(...a),
    platformMarkHospitalPaid: (...a: unknown[]) => markHospMock(...a),
  };
});

const OVERVIEW = {
  booking_fee_revenue: 1500, patient_sub_revenue: 990, paid_count: 60,
  refunds_pending: 0, subscribers_premium: 10, subscribers_trialing: 4,
  open_platform_escalations: 0,
  hospital_sub_revenue: 999, credit_topup_revenue: 2000, gross_revenue: 5489,
  credits_sold: 100, credits_consumed_booking: 25,
  usage_sms: 40, usage_voice_minutes: 12, usage_whatsapp: 8,
  estimated_channel_cost: 40, gateway_fees: 110, net_margin: 5339,
  outstanding_wallet_debt: 0, unused_wallet_credits: 75,
  hospitals: [{
    id: 10, name: "City Hospital", slug: "city", billing_status: "past_due",
    wallet_status: "ok", booking_fee: 30, subscription_status: "past_due",
    monthly_fee: 999, current_period_end: new Date().toISOString(),
    fee_revenue: 1500, paid_bookings: 50, dues: 999,
    wallet_balance: 75, credit_rate_bdt: 20, credit_revenue: 2000, credits_consumed: 25,
  }],
};

describe("PlatformDashboardPage", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    getMeMock.mockReset();
    overviewMock.mockReset();
    paymentsMock.mockReset();
    markHospMock.mockReset();
  });

  it("redirects non-platform-admins to the platform login", async () => {
    getMeMock.mockResolvedValue({ role: "hospital_admin" });
    render(<PlatformDashboardPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/platform-admin"));
  });

  it("renders revenue tiles and the hospital table for a platform admin", async () => {
    getMeMock.mockResolvedValue({ role: "platform_admin" });
    overviewMock.mockResolvedValue(OVERVIEW);
    paymentsMock.mockResolvedValue([]);
    render(<PlatformDashboardPage />);

    // Subscription-revenue tile is a unique amount; hospital row shows the name.
    expect(await screen.findByText("৳990")).toBeInTheDocument();
    expect(screen.getByText("City Hospital")).toBeInTheDocument();
    expect(screen.getByText("past_due")).toBeInTheDocument();
  });

  it("marks a hospital subscription paid", async () => {
    getMeMock.mockResolvedValue({ role: "platform_admin" });
    overviewMock.mockResolvedValue(OVERVIEW);
    paymentsMock.mockResolvedValue([]);
    markHospMock.mockResolvedValue({ ok: true });
    render(<PlatformDashboardPage />);

    fireEvent.click(await screen.findByRole("button", { name: /Paid/ }));
    await waitFor(() => expect(markHospMock).toHaveBeenCalledWith(10));
  });
});
