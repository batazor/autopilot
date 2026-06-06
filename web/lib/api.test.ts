import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createQueueTask,
  discardLabelingCapture,
  fetchLabelingScreenIds,
  fetchRegionOcr,
  fetchScreenDetect,
  importLabelingPng,
  labelingImageUrl,
  saveDreamscapeScene,
  setActiveGame,
  uploadDreamscapeSceneImage,
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

  it("encodes regions as a comma-separated query for region-ocr", async () => {
    const fetchMock = mockFetchJson({ rows: [] });

    await fetchRegionOcr("bs1", ["dreamscape_memory.1", "dreamscape_memory.2"], {
      threshold: 0.7,
    });

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("/api/instances/bs1/region-ocr");
    expect(url).toContain("regions=dreamscape_memory.1%2Cdreamscape_memory.2");
    expect(url).toContain("threshold=0.7");
  });

  it("calls the lightweight screen-detect endpoint", async () => {
    const fetchMock = mockFetchJson({ detected_screen: "dreamscape_memory" });

    await fetchScreenDetect("bs1");

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toBe("/api/instances/bs1/screen-detect");
  });

  it("passes abort_running when queueing a Dreamscape restart", async () => {
    const fetchMock = mockFetchJson({
      ok: true,
      task_id: "queue:restart",
      queue_key: "wos:queue:bs1",
    });

    await createQueueTask({
      scenario_key: "dreamscape_memory",
      instance_id: "bs1",
      scheduled_at: 123,
      priority: 90000,
      replace_existing: true,
      abort_running: true,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/queue/enqueue");
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({
      scenario_key: "dreamscape_memory",
      instance_id: "bs1",
      replace_existing: true,
      abort_running: true,
    });
  });
});

describe("dreamscape scene API", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setActiveGame("");
  });

  it("posts the image as multipart to the scene image endpoint", async () => {
    const fetchMock = mockFetchJson({
      ok: true,
      source_image: "games/wos/events/dreamscape_memory/references/maps/garden.png",
    });

    const res = await uploadDreamscapeSceneImage(
      "garden",
      new File(["png"], "garden.png", { type: "image/png" }),
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/dreamscape/scenes/garden/image");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBeInstanceOf(File);
    expect(res.source_image).toContain("references/maps/garden.png");
  });

  it("posts the scene title + markers as JSON to the save endpoint", async () => {
    const fetchMock = mockFetchJson({
      ok: true,
      slug: "garden",
      point_count: 2,
      active: "garden",
    });

    const res = await saveDreamscapeScene("garden", {
      title: "Garden",
      source_image: "games/wos/events/dreamscape_memory/references/maps/garden.png",
      scene_rect: null,
      points: [
        { n: 1, name: "Book", xPct: 10, yPct: 20 },
        { n: 2, name: "Wolf", xPct: 30, yPct: 40 },
      ],
      activate: true,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/dreamscape/scenes/garden");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body));
    expect(body.title).toBe("Garden");
    expect(body.activate).toBe(true);
    expect(body.points).toHaveLength(2);
    expect(body.points[0]).toEqual({ n: 1, name: "Book", xPct: 10, yPct: 20 });
    expect(res.active).toBe("garden");
  });
});
