import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ModulesPage from "./page";
import { FeedbackProvider } from "@/components/feedback";
import * as api from "@/lib/api";
import type { ModuleRow } from "@/lib/config-pages";

function mod(overrides: Partial<ModuleRow>): ModuleRow {
  return {
    id: "heroes",
    storage_key: "wos/heroes",
    title: "Heroes",
    description: "Promote and recruit heroes",
    wiki: false,
    core: true,
    rel_path: "games/wos/core/heroes",
    scenarios_dir: "games/wos/core/heroes/scenarios",
    has_analyze: true,
    scenario_count: 0,
    enabled_on: 0,
    enabled_off: 0,
    scenarios: [],
    ...overrides,
  };
}

function renderPage() {
  return render(
    <FeedbackProvider>
      <ModulesPage />
    </FeedbackProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ModulesPage data states", () => {
  it("renders modules and a loaded count on success", async () => {
    vi.spyOn(api, "fetchModules").mockResolvedValue([
      mod({ id: "heroes", title: "Heroes" }),
    ]);
    vi.spyOn(api, "fetchWikiScopes").mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("Heroes")).toBeInTheDocument();
    expect(screen.getByText("1 loaded")).toBeInTheDocument();
    // The misleading "filter" empty-state must never show when rows exist.
    expect(
      screen.queryByText(/No modules match this filter/),
    ).not.toBeInTheDocument();
  });

  it("surfaces a load error instead of the misleading empty-filter message", async () => {
    vi.spyOn(api, "fetchModules").mockRejectedValue(new Error("boom"));
    vi.spyOn(api, "fetchWikiScopes").mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.getByText(/Couldn.t load modules/)).toBeInTheDocument();
    expect(
      screen.queryByText(/No modules match this filter/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/No modules found/)).not.toBeInTheDocument();
  });

  it("shows an empty state (not an error) when the API returns no modules", async () => {
    vi.spyOn(api, "fetchModules").mockResolvedValue([]);
    vi.spyOn(api, "fetchWikiScopes").mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("No modules found")).toBeInTheDocument();
    expect(
      screen.queryByText(/No modules match this filter/),
    ).not.toBeInTheDocument();
  });
});
