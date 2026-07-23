import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LangProvider } from "@/lib/i18n";
import FloatingAssistant from "@/components/portal/FloatingAssistant";

let mockAccount: object | null = null;
let mockPathname = "/portal";

vi.mock("@/lib/patientAuth", () => ({
  usePatientAuth: () => ({ account: mockAccount }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

// The panel/voice internals are exercised elsewhere — keep this test on the
// widget's visibility rules.
vi.mock("@/components/portal/ChatPanel", () => ({ default: () => <div data-testid="chat-panel" /> }));
vi.mock("@/components/portal/VoiceCall", () => ({ default: () => <div data-testid="voice-call" /> }));

function renderWidget() {
  return render(
    <LangProvider>
      <FloatingAssistant />
    </LangProvider>,
  );
}

describe("FloatingAssistant", () => {
  beforeEach(() => {
    mockAccount = { id: 7, name: "Kodu", phone: "01711000000" };
    mockPathname = "/portal";
  });

  it("shows chat and voice bubbles for a logged-in patient", () => {
    renderWidget();
    expect(screen.getByRole("button", { name: /চ্যাট করুন|Chat with/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ভয়েস কল|voice call/ })).toBeInTheDocument();
  });

  it("renders nothing when logged out (login/signup pages)", () => {
    mockAccount = null;
    const { container } = renderWidget();
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing on /portal/book (chat already full-screen)", () => {
    mockPathname = "/portal/book";
    const { container } = renderWidget();
    expect(container.innerHTML).toBe("");
  });

  it("stays visible on doctor pages", () => {
    mockPathname = "/portal/doctor/5";
    renderWidget();
    expect(screen.getByRole("button", { name: /চ্যাট করুন|Chat with/ })).toBeInTheDocument();
  });
});
