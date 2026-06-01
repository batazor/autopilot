import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  discardLabelingCapture,
  fetchLabelingScreenIds,
  importLabelingPng,
  labelingImageUrl,
  setActiveGame,
} from "./api";

function mockFetchJson(body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

describe("labeling API game scope", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setActiveGame("");
  });

  it("adds the active game to labeling image URLs", () => {
    setActiveGame("kingshot");

    const url = labelingImageUrl(
      "games/kingshot/core/main_city/references/main_city.png",
      7,
    );

    expect(url).toContain("game=kingshot");
    expect(url).toContain("n=7");
  });

  it("adds the active game to screen-id fetches", async () => {
    const fetchMock = mockFetchJson({ screen_ids: ["main_city"] });
    setActiveGame("kingshot");

    await fetchLabelingScreenIds("kingshot:core/main_city", "main_city");

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("scope=kingshot%3Acore%2Fmain_city");
    expect(url).toContain("game=kingshot");
    expect(url).toContain("current=main_city");
  });

  it("adds the active game to capture discard requests", async () => {
    const fetchMock = mockFetchJson({ ok: true });
    setActiveGame("kingshot");

    await discardLabelingCapture(
      "games/kingshot/core/main_city/references/temporal/bs1_shot.png",
      "kingshot:core/main_city",
    );

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("scope=kingshot%3Acore%2Fmain_city");
    expect(url).toContain("game=kingshot");
    expect(url).toContain("ref=games%2Fkingshot%2Fcore%2Fmain_city");
  });

  it("adds the active game to dropped PNG imports", async () => {
    const fetchMock = mockFetchJson({ ok: true, ref: "games/kingshot/x.png" });
    setActiveGame("kingshot");

    await importLabelingPng(
      "bs1",
      "kingshot:core/main_city",
      new File(["png"], "shot.png", { type: "image/png" }),
    );

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("scope=kingshot%3Acore%2Fmain_city");
    expect(url).toContain("game=kingshot");
  });
});
