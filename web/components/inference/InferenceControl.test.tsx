import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InferenceControl } from "./InferenceControl";
import * as api from "@/lib/api";

// Controllable global-API-offline signal (the real hook reads connectivity from
// ApiStatusProvider's health query; here we drive it directly).
const apiOffline = vi.hoisted(() => ({ value: false }));

vi.mock("@/components/ApiStatusProvider", () => ({
  useApiOffline: () => apiOffline.value,
}));

vi.mock("@/lib/api", () => ({
  fetchInferenceStatus: vi.fn(),
  fetchInferenceLogs: vi.fn(),
  startInference: vi.fn(),
  stopInference: vi.fn(),
}));

const STATUS_ERROR =
  "/api/inference/status: 500 Internal Server Error — The API failed unexpectedly.";

function renderControl() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <InferenceControl />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiOffline.value = false;
  vi.mocked(api.fetchInferenceStatus).mockRejectedValue(new Error(STATUS_ERROR));
  vi.mocked(api.fetchInferenceLogs).mockResolvedValue({ lines: [] });
});

afterEach(() => vi.clearAllMocks());

describe("InferenceControl — API-offline error de-duplication", () => {
  it("shows the status-fetch error banner when the API is reachable", async () => {
    apiOffline.value = false;
    renderControl();
    await waitFor(() => {
      expect(screen.getByText(STATUS_ERROR)).toBeInTheDocument();
    });
  });

  it("suppresses the banner when the API is globally offline (one place is enough)", async () => {
    apiOffline.value = true;
    renderControl();
    // Let the same status query settle into its error state...
    await waitFor(() => {
      expect(api.fetchInferenceStatus).toHaveBeenCalled();
    });
    await act(async () => {
      await Promise.resolve();
    });
    // ...the global "API offline" indicator already covers it, so no banner here.
    expect(screen.queryByText(STATUS_ERROR)).not.toBeInTheDocument();
  });
});
