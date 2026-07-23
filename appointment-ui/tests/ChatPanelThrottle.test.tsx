import { render, screen, waitFor } from "@testing-library/react";
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
vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...mod,
    getChatHistory: (...args: unknown[]) => historyMock(...args),
    getPatientToken: () => "tok",
  };
});

vi.mock("@/components/portal/VoiceCall", () => ({ default: () => null }));

function renderPanel(variant: "page" | "popup") {
  return render(
    <LangProvider>
      <ChatPanel variant={variant} />
    </LangProvider>,
  );
}

describe("ChatPanel greeting throttle", () => {
  beforeEach(() => {
    historyMock.mockReset();
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    // jsdom has no scrollIntoView; the auto-scroll effect calls it per render.
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("popup renders existing history WITHOUT a welcome-back LLM turn", async () => {
    historyMock.mockResolvedValue([
      { role: "user", text: "হাই" },
      { role: "assistant", text: "আসসালামু আলাইকুম!" },
    ]);
    renderPanel("popup");
    expect(await screen.findByText("আসসালামু আলাইকুম!")).toBeInTheDocument();
    // Each popup open must NOT burn a CPU LLM turn just to greet again.
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("page variant still fires the welcome-back turn on history", async () => {
    historyMock.mockResolvedValue([
      { role: "user", text: "হাই" },
      { role: "assistant", text: "আসসালামু আলাইকুম!" },
    ]);
    renderPanel("page");
    await screen.findByText("আসসালামু আলাইকুম!");
    await waitFor(() => expect(global.fetch).toHaveBeenCalledOnce());
  });

  it("popup with an empty history starts the greeting turn", async () => {
    historyMock.mockResolvedValue([]);
    renderPanel("popup");
    await waitFor(() => expect(global.fetch).toHaveBeenCalledOnce());
  });
});
