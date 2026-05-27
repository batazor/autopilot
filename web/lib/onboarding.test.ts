import { describe, expect, it } from "vitest";
import {
  checklistDismissed,
  markChecklistDismissed,
  markWizardSeen,
  wizardSeen,
} from "./onboarding";

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
