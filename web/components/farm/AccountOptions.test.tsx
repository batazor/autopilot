import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountOptions } from "./AccountOptions";

const ARENA = {
  key: "planner.arena_exclude_own_alliance",
  label: "Skip own alliance in Arena",
  description: "Don't attack players from your own alliance.",
  type: "bool",
  group: "Arena",
  choices: [],
  value: false,
};

const OPTIONS_URL = "/api/farm/accounts/moss/characters/222/options";

function jsonResponse(body: unknown, { ok = true, status = 200 } = {}) {
  return Promise.resolve({ ok, status, json: async () => body } as Response);
}

describe("AccountOptions", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("fetches and renders the character's options", async () => {
    const fetchMock = vi.fn(() => jsonResponse({ options: [ARENA] }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AccountOptions username="moss" fid="222" />);

    expect(await screen.findByText("Skip own alliance in Arena")).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: "Skip own alliance in Arena" }),
    ).toHaveAttribute("aria-checked", "false");
    expect(fetchMock).toHaveBeenCalledWith(OPTIONS_URL);
  });

  it("posts the new value and reflects it optimistically on toggle", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((_url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? jsonResponse({ value: true })
        : jsonResponse({ options: [ARENA] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AccountOptions username="moss" fid="222" />);
    const sw = await screen.findByRole("switch", {
      name: "Skip own alliance in Arena",
    });
    await user.click(sw);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        OPTIONS_URL,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ key: ARENA.key, value: true }),
        }),
      ),
    );
    expect(sw).toHaveAttribute("aria-checked", "true");
  });

  it("surfaces a load error", async () => {
    const fetchMock = vi.fn(() => jsonResponse({}, { ok: false, status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AccountOptions username="moss" fid="222" />);

    expect(await screen.findByText(/Options unavailable/)).toBeInTheDocument();
  });

  it("renders nothing when the registry is empty", async () => {
    const fetchMock = vi.fn(() => jsonResponse({ options: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<AccountOptions username="moss" fid="222" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("retries a failed load", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => jsonResponse({}, { ok: false, status: 500 }))
      .mockImplementationOnce(() => jsonResponse({ options: [ARENA] }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AccountOptions username="moss" fid="222" />);
    await screen.findByRole("alert"); // load failed, announced
    await user.click(screen.getByRole("button", { name: /Retry/ }));

    expect(await screen.findByText("Skip own alliance in Arena")).toBeInTheDocument();
  });

  it("announces a save failure and rolls back", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((_url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? jsonResponse({}, { ok: false, status: 500 })
        : jsonResponse({ options: [ARENA] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AccountOptions username="moss" fid="222" />);
    const sw = await screen.findByRole("switch", {
      name: "Skip own alliance in Arena",
    });
    await user.click(sw);

    expect(await screen.findByRole("alert")).toHaveTextContent(/Couldn't save/);
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false")); // rolled back
  });
});
