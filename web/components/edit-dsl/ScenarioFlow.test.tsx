import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { ScenarioFlow } from "./ScenarioFlow";
import type { EditorMeta } from "./StepCard";
import * as api from "@/lib/api";
import type { ScenarioDocument } from "@/lib/edit-dsl/dsl";
import type { InstanceDetail, LabelingDocument } from "@/lib/types";

vi.mock("@/components/ThemeProvider", () => ({
  useTheme: () => ({ theme: "dark", setTheme: () => {}, toggleTheme: () => {} }),
}));

// React Flow needs layout APIs happy-dom doesn't implement
// (https://reactflow.dev/learn/advanced-use/testing).
beforeAll(() => {
  if (typeof window.ResizeObserver === "undefined") {
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }
  if (typeof globalThis.DOMMatrixReadOnly === "undefined") {
    globalThis.DOMMatrixReadOnly = class {
      m22 = 1;
    } as unknown as typeof DOMMatrixReadOnly;
  }
  Object.defineProperties(window.HTMLElement.prototype, {
    offsetHeight: { get: () => 60, configurable: true },
    offsetWidth: { get: () => 260, configurable: true },
  });
  (
    window.SVGElement.prototype as unknown as { getBBox: () => DOMRect }
  ).getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
});

const META: EditorMeta = {
  regions: ["workers", "popup_close"],
  region_refs: { workers: "games/wos/core/x/references/main_city.png" },
  region_screens: { workers: "main_city" },
  region_red_dot: ["workers"],
  fsm_nodes: ["main_city"],
  exec_names: ["scan"],
  scenario_keys: ["other_one"],
};

function sampleDoc(): ScenarioDocument {
  return {
    name: "Test scenario",
    node: "main_city",
    enabled: true,
    steps: [
      { click: "workers" },
      { while_match: "popup_close", max: 2, steps: [{ wait: "500ms" }] },
      { push_scenario: "other_one" },
      { click: "ghost_region" },
    ],
  };
}

const REL = "games/wos/core/x/scenarios/test_scenario.yaml";

function nodeEl(container: HTMLElement, id: string): HTMLElement {
  const el = container.querySelector(`.react-flow__node[data-id="${id}"]`);
  expect(el, `node ${id}`).toBeTruthy();
  return el as HTMLElement;
}

const EMPTY_QUEUE = {
  pending: [],
  running: [],
  history: [],
  pending_count: 0,
  history_count: 0,
} as unknown as Awaited<ReturnType<typeof api.fetchQueue>>;

const NO_CALLERS = { callers: [], cron: "", notify_events: [] };

function arrange(
  doc = sampleDoc(),
  callers: Awaited<ReturnType<typeof api.fetchEditDslCallers>> = NO_CALLERS,
) {
  vi.spyOn(api, "fetchInstances").mockResolvedValue([]);
  vi.spyOn(api, "fetchQueue").mockResolvedValue(EMPTY_QUEUE);
  vi.spyOn(api, "fetchEditDslCallers").mockResolvedValue(callers);
  vi.spyOn(api, "fetchEditScenarioNameCollisions").mockResolvedValue([]);
  vi.spyOn(api, "fetchLabelingDocument").mockResolvedValue({
    ref: META.region_refs!.workers,
    regions: [
      {
        name: "workers",
        bbox: { x: 10, y: 20, width: 30, height: 5, rotation: 0 },
      },
    ],
  } as unknown as LabelingDocument);
  const onChange = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={client}>
      <ScenarioFlow
        doc={doc}
        rel={REL}
        meta={META}
        collisions={[]}
        onCollisionsChange={() => {}}
        onChange={onChange}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onChange };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ScenarioFlow canvas", () => {
  it("renders the start node, one node per step, and container chrome", () => {
    const { container } = arrange();
    expect(within(nodeEl(container, "start")).getByText("Test scenario")).toBeInTheDocument();
    expect(within(nodeEl(container, "s:0")).getByText("click")).toBeInTheDocument();
    const wm = nodeEl(container, "s:1");
    expect(within(wm).getByText("while_match")).toBeInTheDocument();
    expect(within(wm).getByText("max 2")).toBeInTheDocument();
    expect(nodeEl(container, "s:1/0")).toHaveTextContent("wait");
    expect(nodeEl(container, "s:3")).toBeTruthy();
  });

  it("flags unknown regions with a ⚠ badge, valid nodes stay clean", () => {
    const { container } = arrange();
    expect(nodeEl(container, "s:3").textContent).toContain("⚠");
    expect(nodeEl(container, "s:0").textContent).not.toContain("⚠");
  });

  it("links push_scenario nodes to the target scenario", () => {
    const { container } = arrange();
    const link = nodeEl(container, "s:2").querySelector("a[href]");
    expect(link).toBeTruthy();
    expect(link!.getAttribute("href")).toBe("/edit-dsl?scenario=other_one");
  });
});

describe("ScenarioFlow inspector", () => {
  it("shows the header form until a node is picked", () => {
    arrange();
    expect(screen.getByText(/Click a node to edit it here/)).toBeInTheDocument();
    expect(screen.getByText("cond (root guard, optional)")).toBeInTheDocument();
  });

  it("opens step fields on node click and frames the region on its screen", async () => {
    const { container } = arrange();
    fireEvent.click(nodeEl(container, "s:0"));
    expect(screen.getByText("region (click)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move up" })).toBeDisabled();
    // Reference screenshot with the labeling bbox framed.
    expect(screen.getByText("on screen")).toBeInTheDocument();
    const frame = await waitFor(() => {
      const el = container.querySelector(".pointer-events-none.absolute");
      expect(el, "bbox frame").toBeTruthy();
      return el as HTMLElement;
    });
    expect(frame.style.left).toBe("10%");
    expect(frame.style.width).toBe("30%");
  });

  it("shows the flat note for container inner steps", async () => {
    const { container } = arrange();
    fireEvent.click(nodeEl(container, "s:1"));
    expect(screen.getByText(/Inner steps \(1\) are shown as nodes on the canvas/)).toBeInTheDocument();
  });
});

describe("ScenarioFlow structural edits", () => {
  it("appends a step at the root when nothing is selected", async () => {
    const user = userEvent.setup();
    const { onChange } = arrange();
    await user.click(screen.getByRole("button", { name: "Add to end" }));
    const next = onChange.mock.calls[0][0] as ScenarioDocument;
    expect(next.steps).toHaveLength(5);
    expect(next.steps![4]).toEqual({ click: "" });
  });

  it("inserts after the selected step and inside a selected container", async () => {
    const user = userEvent.setup();
    const { container, onChange } = arrange();
    fireEvent.click(nodeEl(container, "s:0"));
    await user.click(screen.getByRole("button", { name: "Add after" }));
    let next = onChange.mock.calls.at(-1)![0] as ScenarioDocument;
    expect(next.steps).toHaveLength(5);
    expect(next.steps![1]).toEqual({ click: "" });

    fireEvent.click(nodeEl(container, "s:1"));
    await user.click(screen.getByRole("button", { name: "Add inside" }));
    next = onChange.mock.calls.at(-1)![0] as ScenarioDocument;
    expect(next.steps).toHaveLength(4);
    expect((next.steps![1] as { steps: unknown[] }).steps).toHaveLength(2);
  });

  it("moves, duplicates and removes the selected step", async () => {
    const user = userEvent.setup();
    const { container, onChange } = arrange();
    fireEvent.click(nodeEl(container, "s:0"));

    await user.click(screen.getByRole("button", { name: "Move down" }));
    let next = onChange.mock.calls.at(-1)![0] as ScenarioDocument;
    expect(next.steps![1]).toEqual({ click: "workers" });

    await user.click(screen.getByRole("button", { name: "Duplicate step" }));
    next = onChange.mock.calls.at(-1)![0] as ScenarioDocument;
    expect(next.steps).toHaveLength(5);

    await user.click(screen.getByRole("button", { name: "Remove step" }));
    next = onChange.mock.calls.at(-1)![0] as ScenarioDocument;
    expect(next.steps).toHaveLength(3);
    // Selection cleared → back to the header form.
    expect(screen.getByText(/Click a node to edit it here/)).toBeInTheDocument();
  });

  it("offers break only inside loop-like containers", async () => {
    const user = userEvent.setup();
    const { container } = arrange();
    // Root-level selection: no break in the add menu.
    fireEvent.click(nodeEl(container, "s:0"));
    await user.click(screen.getByRole("button", { name: /Step type/ }));
    expect(screen.queryByRole("option", { name: "break" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    // Child of while_match: break available.
    fireEvent.click(nodeEl(container, "s:1/0"));
    await user.click(screen.getByRole("button", { name: /Step type/ }));
    expect(screen.getByRole("option", { name: "break" })).toBeInTheDocument();
  });
});

describe("ScenarioFlow callers", () => {
  it("lists push_scenario/cron/notify references with deep links", async () => {
    arrange(sampleDoc(), {
      callers: [
        { rel: "games/wos/core/a/scenarios/caller_one.yaml", stem: "caller_one", step: "3/0" },
      ],
      cron: "*/5 * * * *",
      notify_events: ["intel_lighthouse"],
    });
    await waitFor(() => {
      expect(screen.getByText("Called by")).toBeInTheDocument();
    });
    const link = screen.getByRole("link", { name: "caller_one" });
    expect(link.getAttribute("href")).toBe(
      "/edit-dsl?scenario=games%2Fwos%2Fcore%2Fa%2Fscenarios%2Fcaller_one.yaml",
    );
    expect(screen.getByText("step 3/0")).toBeInTheDocument();
    expect(screen.getByText("*/5 * * * *")).toBeInTheDocument();
    expect(screen.getByText("intel_lighthouse")).toBeInTheDocument();
  });

  it("says so when nothing references the scenario", async () => {
    arrange();
    await waitFor(() => {
      expect(screen.getByText(/Nothing references this scenario/)).toBeInTheDocument();
    });
  });
});

describe("ScenarioFlow region test", () => {
  it("probes the region on the live frame and shows the score", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "fetchInstances").mockResolvedValue(["bs1"]);
    vi.spyOn(api, "fetchInstanceDetail").mockResolvedValue({
      instance_id: "bs1",
      state: {},
    } as unknown as Awaited<ReturnType<typeof api.fetchInstanceDetail>>);
    vi.spyOn(api, "fetchQueue").mockResolvedValue(EMPTY_QUEUE);
    vi.spyOn(api, "fetchEditDslCallers").mockResolvedValue(NO_CALLERS);
    vi.spyOn(api, "fetchEditScenarioNameCollisions").mockResolvedValue([]);
    vi.spyOn(api, "fetchLabelingDocument").mockResolvedValue({
      regions: [],
    } as unknown as Awaited<ReturnType<typeof api.fetchLabelingDocument>>);
    const probe = vi.spyOn(api, "fetchAreaRegionProbe").mockResolvedValue({
      result: { matched: true, score: 0.913, threshold: 0.85 },
    } as unknown as Awaited<ReturnType<typeof api.fetchAreaRegionProbe>>);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={client}>
        <ScenarioFlow
          doc={sampleDoc()}
          rel={REL}
          meta={META}
          collisions={[]}
          onCollisionsChange={() => {}}
          onChange={() => {}}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(nodeEl(container, "s:0"));
    const btn = await screen.findByRole("button", { name: "Test on bs1" });
    await user.click(btn);
    await waitFor(() => {
      expect(screen.getByText("matched")).toBeInTheDocument();
    });
    expect(screen.getByText(/score 0\.913/)).toBeInTheDocument();
    expect(probe).toHaveBeenCalledWith("bs1", { region: "workers" });
  });
});

describe("ScenarioFlow last-run trace", () => {
  it("colors nodes from the latest queue-history trace and toggles off", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "fetchInstances").mockResolvedValue([]);
    vi.spyOn(api, "fetchEditScenarioNameCollisions").mockResolvedValue([]);
    vi.spyOn(api, "fetchQueue").mockResolvedValue({
      ...(EMPTY_QUEUE as object),
      history: [
        {
          task_id: "t1",
          scenario: "Test scenario",
          scenario_key: "test_scenario",
          instance_id: "bs1",
          finished_at: Math.floor(Date.now() / 1000) - 60,
          success: false,
          steps_trace: [
            { i: "0", status: "ok", duration_ms: 80 },
            { i: "1.0.0", status: "failed", reason: "match_region_not_found" },
          ],
        },
      ],
    } as unknown as Awaited<ReturnType<typeof api.fetchQueue>>);
    vi.spyOn(api, "fetchEditDslCallers").mockResolvedValue(NO_CALLERS);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={client}>
        <ScenarioFlow
          doc={sampleDoc()}
          rel={REL}
          meta={META}
          collisions={[]}
          onCollisionsChange={() => {}}
          onChange={() => {}}
        />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText(/last run/)).toBeInTheDocument();
    });
    const badgeStyle = (id: string) =>
      (nodeEl(container, id).querySelector("span[style]") as HTMLElement).getAttribute(
        "style",
      ) ?? "";
    expect(badgeStyle("s:0")).toContain("#22c55e");
    expect(badgeStyle("s:1/0")).toContain("#ef4444");
    // Toggle hides the coloring but keeps the banner.
    await user.click(screen.getByRole("button", { name: "hide" }));
    expect(badgeStyle("s:0")).not.toContain("#22c55e");
    expect(screen.getByRole("button", { name: "show" })).toBeInTheDocument();
  });
});

describe("ScenarioFlow live trace", () => {
  it("highlights the running step and shows the device banner", async () => {
    vi.spyOn(api, "fetchQueue").mockResolvedValue(EMPTY_QUEUE);
    vi.spyOn(api, "fetchEditDslCallers").mockResolvedValue(NO_CALLERS);
    vi.spyOn(api, "fetchInstances").mockResolvedValue(["bs1"]);
    vi.spyOn(api, "fetchInstanceDetail").mockResolvedValue({
      instance_id: "bs1",
      state: {
        current_scenario: "test_scenario",
        last_active_scenario_step: "1",
        last_active_scenario_iter: "",
      },
    } as unknown as InstanceDetail);
    vi.spyOn(api, "fetchEditScenarioNameCollisions").mockResolvedValue([]);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { container } = render(
      <QueryClientProvider client={client}>
        <ScenarioFlow
          doc={sampleDoc()}
          rel={REL}
          meta={META}
          collisions={[]}
          onCollisionsChange={() => {}}
          onChange={() => {}}
        />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("bs1")).toBeInTheDocument();
    });
    expect(screen.getByText(/step 2/)).toBeInTheDocument();
    expect(nodeEl(container, "s:1").textContent).toContain("▶");
  });
});
