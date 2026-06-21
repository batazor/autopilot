import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  PendingSchedulePill,
  PriorityBadge,
  queuePriorityBand,
} from "./QueueVisuals";
import type { QueuePendingRow } from "@/lib/types";

function pendingRow(overrides: Partial<QueuePendingRow> = {}): QueuePendingRow {
  return {
    task_id: "t1",
    scheduled: "overdue · 205h 44m ago",
    scheduled_at: 0,
    overdue: true,
    player_id: "401227964",
    instance_id: "bs2",
    scenario: "Claim",
    scenario_key: "claim",
    region: "—",
    priority: 80_000,
    cooperative: false,
    ...overrides,
  };
}

describe("queuePriorityBand", () => {
  it("maps deferring scenarios (small values) to low", () => {
    expect(queuePriorityBand(15)).toBe("low");
    expect(queuePriorityBand(49_999)).toBe("low");
  });

  it("maps the default band to normal", () => {
    expect(queuePriorityBand(50_000)).toBe("normal");
    expect(queuePriorityBand(80_000)).toBe("normal");
  });

  it("maps above-default to high", () => {
    expect(queuePriorityBand(90_000)).toBe("high");
    expect(queuePriorityBand(120_000)).toBe("high");
  });
});

describe("PriorityBadge", () => {
  it("shows the band label and keeps the raw number in the tooltip", () => {
    render(<PriorityBadge priority={80_000} />);
    const badge = screen.getByText("normal");
    expect(badge).toHaveAttribute("title", "Priority 80,000");
    expect(badge.className).toContain("queue-priority--normal");
  });
});

describe("PendingSchedulePill", () => {
  it("shows Overdue for an overdue task on a healthy instance", () => {
    render(<PendingSchedulePill row={pendingRow()} />);
    expect(screen.getByText("Overdue")).toBeInTheDocument();
  });

  it("shows Blocked with the reason in the tooltip when the device is offline", () => {
    render(
      <PendingSchedulePill
        row={pendingRow({
          blocked: true,
          blocked_reason: "device offline (ADB)",
        })}
      />,
    );
    const pill = screen.getByText("Blocked");
    expect(pill.closest(".status-pill")).toHaveAttribute(
      "title",
      "device offline (ADB) · overdue · 205h 44m ago",
    );
  });
});
