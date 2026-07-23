import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LangProvider } from "@/lib/i18n";
import ChatPanel from "@/components/portal/ChatPanel";

vi.mock("@/lib/patientAuth", () => ({
  usePatientAuth: () => ({
    account: { id: 7, name: "Kodu", phone: "01711000000", email: "k@a.com", created_at: "" },
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const historyMock = vi.fn();
const getPaymentMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...mod,
    getChatHistory: (...args: unknown[]) => historyMock(...args),
    portalGetPayment: (...args: unknown[]) => getPaymentMock(...args),
    getPatientToken: () => "tok",
  };
});

vi.mock("@/components/portal/VoiceCall", () => ({ default: () => null }));

/** Build a fetch Response whose body streams the given SSE events. */
function sseResponse(events: Record<string, unknown>[]): Response {
  const body = events.map((e) => `data: ${JSON.stringify(e)}\n`).join("");
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return { ok: true, status: 200, body: stream } as unknown as Response;
}

function renderPanel() {
  return render(
    <LangProvider>
      <ChatPanel variant="page" />
    </LangProvider>,
  );
}

describe("ChatPanel payment card", () => {
  beforeEach(() => {
    historyMock.mockReset();
    getPaymentMock.mockReset();
    // Poll stays pending so the card is not swapped out mid-assertion.
    getPaymentMock.mockResolvedValue({
      id: "pay-1", status: "initiated", amount: 30, currency: "BDT",
      appointment_id: "apt-1", appointment_status: "pending_payment",
    });
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("renders a deterministic pay card from a `payment` stream event", async () => {
    historyMock.mockResolvedValue([]); // new session → greeting turn fires the stream
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          { type: "token", text: "আপনার স্লট ধরে রাখা হয়েছে।" },
          {
            type: "payment",
            appointment_id: "apt-1",
            payment_id: "pay-1",
            amount: 30,
            currency: "BDT",
            pay_url: "https://gw.example/pay/xyz",
            expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
          },
          { type: "end", done: true },
        ]),
      ),
    );

    renderPanel();

    expect(await screen.findByText(/বুকিং ফি পরিশোধ করুন|Pay the booking fee/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /এখন পরিশোধ করুন|Pay now/ });
    expect(link).toHaveAttribute("href", "https://gw.example/pay/xyz");
    vi.unstubAllGlobals();
  });

  it("renders an upgrade card from an `upgrade` stream event", async () => {
    historyMock.mockResolvedValue([]);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          { type: "token", text: "এই মাসের ফ্রি সীমা শেষ।" },
          { type: "upgrade", feature: "chat_bookings", used: 3, cap: 3 },
          { type: "end", done: true },
        ]),
      ),
    );

    renderPanel();

    expect(await screen.findByText(/ফ্রি এআই বুকিং শেষ|used your free AI bookings/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /প্রিমিয়ামে আপগ্রেড|Upgrade to Premium/ });
    expect(link).toHaveAttribute("href", "/portal/account");
    vi.unstubAllGlobals();
  });
});
