import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-confetti", () => ({
  default: (props: { className?: string }) => (
    <canvas data-testid="onboarding-confetti" className={props.className} />
  ),
}));

import { OnboardingChecklist } from "./OnboardingChecklist";
import * as onboarding from "@/lib/onboarding";

const emptyState: onboarding.OnboardingState = {
  device_added_at: null,
  bot_started_at: null,
  first_scenario_at: null,
  first_approval_at: null,
  first_ocr_at: null,
  approvals_disabled_at: null,
};

function stub(state: Partial<onboarding.OnboardingState>) {
  vi.spyOn(onboarding, "fetchOnboardingState").mockResolvedValue({
    ...emptyState,
    ...state,
  });
}

async function flush() {
  // Allow the initial fetch promise + setState to settle.
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OnboardingChecklist", () => {
  it("renders nothing when checklist has been dismissed", () => {
    window.localStorage.setItem("wos:onboarding:checklistDismissed", "1");
    stub({});
    const { container } = render(<OnboardingChecklist />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows all six items with bullets when state is empty", async () => {
    stub({});
    render(<OnboardingChecklist />);
    await flush();
    expect(screen.getByText(/First steps \(0\/6\)/)).toBeInTheDocument();
    expect(screen.getByText("Add device")).toBeInTheDocument();
    expect(screen.getByText("Start bot")).toBeInTheDocument();
    expect(screen.getByText("Wait for first scenario")).toBeInTheDocument();
    expect(screen.getByText("Approve first click")).toBeInTheDocument();
    expect(screen.getByText("View first OCR result")).toBeInTheDocument();
    expect(screen.getByText("Disable approvals")).toBeInTheDocument();
  });

  it("marks completed items with strike-through styling", async () => {
    stub({
      device_added_at: "2026-01-01T00:00:00Z",
      bot_started_at: "2026-01-01T00:01:00Z",
    });
    render(<OnboardingChecklist />);
    await flush();
    expect(screen.getByText(/First steps \(2\/6\)/)).toBeInTheDocument();
    const deviceItem = screen.getByText("Add device").closest("li");
    expect(deviceItem).toHaveClass("is-done");
    const scenarioItem = screen
      .getByText("Wait for first scenario")
      .closest("li");
    expect(scenarioItem).not.toHaveClass("is-done");
  });

  it("shows the completed widget with confetti when all six milestones finish", async () => {
    stub({
      device_added_at: "t",
      bot_started_at: "t",
      first_scenario_at: "t",
      first_approval_at: "t",
      first_ocr_at: "t",
      approvals_disabled_at: "t",
    });
    render(<OnboardingChecklist />);
    await flush();
    expect(screen.getByText("First steps complete")).toBeInTheDocument();
    expect(screen.getByText("Ready for regular runs")).toBeInTheDocument();
    expect(screen.getByTestId("onboarding-confetti")).toBeInTheDocument();
  });

  it("does not replay confetti after completion was already celebrated", async () => {
    onboarding.markChecklistCelebrated();
    stub({
      device_added_at: "t",
      bot_started_at: "t",
      first_scenario_at: "t",
      first_approval_at: "t",
      first_ocr_at: "t",
      approvals_disabled_at: "t",
    });
    render(<OnboardingChecklist />);
    await flush();
    expect(screen.getByText("First steps complete")).toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-confetti")).not.toBeInTheDocument();
  });

  it("hides after the user clicks Dismiss and persists the flag", async () => {
    stub({});
    render(<OnboardingChecklist />);
    await flush();
    const dismiss = screen.getByLabelText("Dismiss checklist");
    await userEvent.click(dismiss);
    expect(window.localStorage.getItem("wos:onboarding:checklistDismissed")).toBe(
      "1",
    );
    expect(screen.queryByText(/First steps/)).not.toBeInTheDocument();
  });
});
