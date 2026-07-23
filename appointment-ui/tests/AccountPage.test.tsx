import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LangProvider } from "@/lib/i18n";
import AccountPage from "@/app/portal/account/page";

const meMock = vi.fn();
const subscribeMock = vi.fn();
const pvStartMock = vi.fn();
const pvConfirmMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...mod,
    getPatientMe: (...a: unknown[]) => meMock(...a),
    portalSubscribe: (...a: unknown[]) => subscribeMock(...a),
    portalPhoneVerifyStart: (...a: unknown[]) => pvStartMock(...a),
    portalPhoneVerifyConfirm: (...a: unknown[]) => pvConfirmMock(...a),
  };
});

vi.mock("@/lib/toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

function baseMe(over: Record<string, unknown> = {}) {
  return {
    id: 7, email: "k@a.com", name: "Kodu", phone: "01711000000", created_at: "",
    plan: "free", tier: "free", trial_ends_at: null, premium_until: null,
    agent_bookings_used: 2, agent_bookings_cap: 3, subscription_fee: 99,
    phone_verified: false, ...over,
  };
}

function renderPage() {
  return render(
    <LangProvider>
      <AccountPage />
    </LangProvider>,
  );
}

describe("AccountPage", () => {
  beforeEach(() => {
    meMock.mockReset();
    subscribeMock.mockReset();
  });

  it("shows the free-tier usage line and an upgrade CTA", async () => {
    meMock.mockResolvedValue(baseMe());
    renderPage();
    expect(await screen.findByText(/2\/3/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /আপগ্রেড|Upgrade to Premium/ })).toBeInTheDocument();
  });

  it("opens the gateway tab when checkout returns a pay_url", async () => {
    meMock.mockResolvedValue(baseMe());
    subscribeMock.mockResolvedValue({
      tier: "free", premium_until: null,
      payment: { payment_id: "pay-s", amount: 99, currency: "BDT",
                 pay_url: "https://gw.example/sub", expires_at: null },
    });
    const openSpy = vi.fn();
    vi.stubGlobal("open", openSpy);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /আপগ্রেড|Upgrade to Premium/ }));
    await waitFor(() =>
      expect(openSpy).toHaveBeenCalledWith("https://gw.example/sub", "_blank", "noopener,noreferrer"),
    );
    vi.unstubAllGlobals();
  });

  it("shows a premium plan with a renew button", async () => {
    meMock.mockResolvedValue(baseMe({
      plan: "premium", tier: "premium", agent_bookings_cap: -1,
      premium_until: new Date(Date.now() + 30 * 86_400_000).toISOString(),
    }));
    renderPage();
    expect(await screen.findByRole("button", { name: /রিনিউ|Renew Premium/ })).toBeInTheDocument();
  });

  it("verifies the phone: send code then confirm, and reloads /me", async () => {
    meMock.mockResolvedValue(baseMe());
    pvStartMock.mockResolvedValue({ ok: true });
    pvConfirmMock.mockResolvedValue({ ok: true, phone: "01711000000", phone_verified: true });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /কোড পাঠান|Send code/ }));
    await waitFor(() => expect(pvStartMock).toHaveBeenCalledWith("01711000000"));

    meMock.mockResolvedValue(baseMe({ phone_verified: true }));
    const codeInput = await screen.findByLabelText(/৬ সংখ্যার কোড|6-digit code/);
    fireEvent.change(codeInput, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: /যাচাই করুন|^Verify$/ }));
    await waitFor(() => expect(pvConfirmMock).toHaveBeenCalledWith("123456"));
    expect(await screen.findByTestId("pv-verified")).toBeInTheDocument();
  });

  it("shows the verified badge instead of the form when already verified", async () => {
    meMock.mockResolvedValue(baseMe({ phone_verified: true }));
    renderPage();
    expect(await screen.findByTestId("pv-verified")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /কোড পাঠান|Send code/ })).toBeNull();
  });
});
