import { afterEach, describe, expect, it, vi } from "vitest";

import {
  checklistDismissed,
  fetchOnboardingState,
  markChecklistDismissed,
  markWizardSeen,
  wizardSeen,
} from "./onboarding";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("wizardSeen / markWizardSeen", () => {
  it("returns false when nothing is stored", () => {
    expect(wizardSeen()).toBe(false);
  });

  it("returns true after markWizardSeen()", () => {
    markWizardSeen();
    expect(wizardSeen()).toBe(true);
  });
});

describe("checklistDismissed / markChecklistDismissed", () => {
  it("returns false when nothing is stored", () => {
    expect(checklistDismissed()).toBe(false);
  });

  it("returns true after markChecklistDismissed()", () => {
    markChecklistDismissed();
    expect(checklistDismissed()).toBe(true);
  });

  it("is independent from the wizard flag", () => {
    markWizardSeen();
    expect(checklistDismissed()).toBe(false);
  });
});

describe("fetchOnboardingState", () => {
  it("uses FastAPI detail text in rejected requests", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "redis unavailable" }), {
          status: 503,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(fetchOnboardingState()).rejects.toThrow(
      "/api/onboarding/state: 503 — redis unavailable",
    );
  });

  it("replaces generic 500 bodies with an actionable message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("Internal Server Error", { status: 500 }),
      ),
    );

    await expect(fetchOnboardingState()).rejects.toThrow(
      "/api/onboarding/state: 500 — Onboarding API failed unexpectedly. Check the API logs and retry.",
    );
  });
});
