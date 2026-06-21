import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  FleetContextProvider,
  useFleet,
} from "@/components/FleetContextProvider";
import * as api from "@/lib/api";
import { saveFleetInstanceId } from "@/lib/fleet-prefs";

const nav = vi.hoisted(() => ({
  replace: vi.fn(),
  search: "",
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/approvals",
  useRouter: () => ({ replace: nav.replace }),
  useSearchParams: () => new URLSearchParams(nav.search),
}));

vi.mock("@/lib/api", () => ({
  fetchInstanceGames: vi.fn(),
  fetchInstances: vi.fn(),
  fetchPlayers: vi.fn(),
  setActiveGame: vi.fn(),
}));

function CurrentInstanceProbe() {
  const { instanceId } = useFleet();
  return <output aria-label="current instance">{instanceId}</output>;
}

beforeEach(() => {
  nav.replace.mockReset();
  nav.search = "";
  vi.mocked(api.fetchInstanceGames).mockResolvedValue({});
  vi.mocked(api.fetchInstances).mockResolvedValue(["bs1", "bs2"]);
  vi.mocked(api.fetchPlayers).mockResolvedValue([]);
  vi.mocked(api.setActiveGame).mockClear();
});

describe("FleetContextProvider", () => {
  it("updates when Bot control changes the selected device in the same tab", async () => {
    saveFleetInstanceId("bs1");

    render(
      <FleetContextProvider>
        <CurrentInstanceProbe />
      </FleetContextProvider>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("current instance")).toHaveTextContent("bs1");
    });

    act(() => {
      saveFleetInstanceId("bs2");
    });

    await waitFor(() => {
      expect(screen.getByLabelText("current instance")).toHaveTextContent("bs2");
    });
  });
});
