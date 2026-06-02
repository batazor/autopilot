// AUTO-GENERATED from wostools.net Dreamscape Memory guide.
// Scene catalog: each scene's base screenshot + item-location guide variants.
// Regenerate via scripts/fetch_dreamscape.py if the event rotation changes.

export type DreamscapeRect = {
  left: number;
  top: number;
  right: number;
  bottom: number;
};

export type DreamscapeScene = {
  slug: string;
  title: string;
  /** Base scene screenshot (clean). */
  src: string;
  width: number;
  height: number;
  /** Item-location guide images (markers drawn on the scene). */
  images: string[];
  /** Normalized playable-area rectangle within the image, when known. */
  sceneRect: DreamscapeRect | null;
  /** Active = current event rotation; archived scenes still 1:1 reusable. */
  active: boolean;
};

export const DREAMSCAPE_SCENES: DreamscapeScene[] = [
  {
    slug: "garden",
    title: "Garden",
    src: "/dreamscape/garden.webp",
    width: 523,
    height: 930,
    images: ["/dreamscape/garden.webp"],
    sceneRect: { left: 0.034, top: 0.086, right: 0.965, bottom: 0.86 },
    active: false,
  },
  {
    slug: "mine",
    title: "Mine",
    src: "/dreamscape/mine.webp",
    width: 522,
    height: 930,
    images: ["/dreamscape/mine.webp"],
    sceneRect: { left: 0.034, top: 0.086, right: 0.965, bottom: 0.86 },
    active: false,
  },
  {
    slug: "hospital",
    title: "Hospital",
    src: "/dreamscape/hospital.webp",
    width: 522,
    height: 930,
    images: ["/dreamscape/hospital.webp"],
    sceneRect: { left: 0.034, top: 0.086, right: 0.965, bottom: 0.86 },
    active: false,
  },
  {
    slug: "city-hall",
    title: "City Hall",
    src: "/dreamscape/city-hall.webp",
    width: 522,
    height: 930,
    images: ["/dreamscape/city-hall.webp"],
    sceneRect: { left: 0.034, top: 0.086, right: 0.965, bottom: 0.86 },
    active: false,
  },
  {
    slug: "farmhouse",
    title: "Farmhouse",
    src: "/dreamscape/farmhouse.webp",
    width: 522,
    height: 930,
    images: ["/dreamscape/farmhouse.webp"],
    sceneRect: { left: 0.034, top: 0.086, right: 0.965, bottom: 0.86 },
    active: false,
  },
  {
    slug: "hangar",
    title: "Hangar",
    src: "/dreamscape/hangar.webp",
    width: 522,
    height: 930,
    images: ["/dreamscape/hangar.webp"],
    sceneRect: { left: 0.034, top: 0.086, right: 0.965, bottom: 0.86 },
    active: false,
  },
  {
    slug: "kitchen",
    title: "Kitchen",
    src: "/dreamscape/kitchen.webp",
    width: 320,
    height: 570,
    images: ["/dreamscape/kitchen.webp"],
    sceneRect: { left: 0.034, top: 0.086, right: 0.965, bottom: 0.86 },
    active: false,
  },
  {
    slug: "court",
    title: "Court",
    src: "/dreamscape/court.webp",
    width: 501,
    height: 1080,
    images: ["/dreamscape/court.webp"],
    sceneRect: { left: 0.02, top: 0.079, right: 0.978, bottom: 0.819 },
    active: false,
  },
  {
    slug: "arena",
    title: "Arena",
    src: "/dreamscape/arena.webp",
    width: 802,
    height: 1306,
    images: ["/dreamscape/arena.webp", "/dreamscape/arena-2.webp", "/dreamscape/arena-3.webp", "/dreamscape/arena-4.webp", "/dreamscape/arena-5.webp", "/dreamscape/arena-6.webp", "/dreamscape/arena-7.webp", "/dreamscape/arena-8.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "ballroom",
    title: "Ballroom",
    src: "/dreamscape/ballroom.webp",
    width: 798,
    height: 1308,
    images: ["/dreamscape/ballroom.webp", "/dreamscape/ballroom-2.webp", "/dreamscape/ballroom-3.webp", "/dreamscape/ballroom-4.webp", "/dreamscape/ballroom-5.webp", "/dreamscape/ballroom-6.webp", "/dreamscape/ballroom-7.webp", "/dreamscape/ballroom-8.webp", "/dreamscape/ballroom-9.webp", "/dreamscape/ballroom-10.webp", "/dreamscape/ballroom-11.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "workshop",
    title: "Workshop",
    src: "/dreamscape/workshop.webp",
    width: 725,
    height: 1191,
    images: ["/dreamscape/workshop.webp", "/dreamscape/workshop-2.webp", "/dreamscape/workshop-3.webp", "/dreamscape/workshop-4.webp", "/dreamscape/workshop-5.webp", "/dreamscape/workshop-6.webp", "/dreamscape/workshop-7.webp", "/dreamscape/workshop-8.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "square",
    title: "Square",
    src: "/dreamscape/square.webp",
    width: 798,
    height: 1305,
    images: ["/dreamscape/square.webp", "/dreamscape/square-2.webp", "/dreamscape/square-3.webp", "/dreamscape/square-4.webp", "/dreamscape/square-5.webp", "/dreamscape/square-6.webp", "/dreamscape/square-7.webp", "/dreamscape/square-8.webp", "/dreamscape/square-9.webp", "/dreamscape/square-10.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "forge",
    title: "Forge",
    src: "/dreamscape/forge.webp",
    width: 795,
    height: 1305,
    images: ["/dreamscape/forge.webp", "/dreamscape/forge-2.webp", "/dreamscape/forge-3.webp", "/dreamscape/forge-4.webp", "/dreamscape/forge-5.webp", "/dreamscape/forge-6.webp", "/dreamscape/forge-7.webp", "/dreamscape/forge-8.webp", "/dreamscape/forge-9.webp", "/dreamscape/forge-10.webp", "/dreamscape/forge-11.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "stable",
    title: "Stable",
    src: "/dreamscape/stable.webp",
    width: 803,
    height: 1306,
    images: ["/dreamscape/stable.webp", "/dreamscape/stable-2.webp", "/dreamscape/stable-3.webp", "/dreamscape/stable-4.webp", "/dreamscape/stable-5.webp", "/dreamscape/stable-6.webp", "/dreamscape/stable-7.webp", "/dreamscape/stable-8.webp", "/dreamscape/stable-9.webp", "/dreamscape/stable-10.webp", "/dreamscape/stable-11.webp", "/dreamscape/stable-12.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "market",
    title: "Market",
    src: "/dreamscape/market.webp",
    width: 848,
    height: 1377,
    images: ["/dreamscape/market.webp", "/dreamscape/market-2.webp", "/dreamscape/market-3.webp", "/dreamscape/market-4.webp", "/dreamscape/market-5.webp", "/dreamscape/market-6.webp", "/dreamscape/market-7.webp", "/dreamscape/market-8.webp", "/dreamscape/market-9.webp", "/dreamscape/market-10.webp", "/dreamscape/market-11.webp", "/dreamscape/market-12.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "outpost",
    title: "Outpost",
    src: "/dreamscape/outpost.webp",
    width: 848,
    height: 1373,
    images: ["/dreamscape/outpost.webp", "/dreamscape/outpost-2.webp", "/dreamscape/outpost-3.webp", "/dreamscape/outpost-4.webp", "/dreamscape/outpost-5.webp", "/dreamscape/outpost-6.webp", "/dreamscape/outpost-7.webp", "/dreamscape/outpost-8.webp", "/dreamscape/outpost-9.webp", "/dreamscape/outpost-10.webp", "/dreamscape/outpost-11.webp", "/dreamscape/outpost-12.webp", "/dreamscape/outpost-13.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "office",
    title: "Office",
    src: "/dreamscape/office.webp",
    width: 849,
    height: 1375,
    images: ["/dreamscape/office.webp", "/dreamscape/office-2.webp", "/dreamscape/office-3.webp", "/dreamscape/office-4.webp", "/dreamscape/office-5.webp", "/dreamscape/office-6.webp", "/dreamscape/office-7.webp", "/dreamscape/office-8.webp", "/dreamscape/office-9.webp", "/dreamscape/office-10.webp", "/dreamscape/office-11.webp", "/dreamscape/office-12.webp", "/dreamscape/office-13.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "barracks",
    title: "Barracks",
    src: "/dreamscape/barracks.webp",
    width: 916,
    height: 1480,
    images: ["/dreamscape/barracks.webp", "/dreamscape/barracks-2.webp", "/dreamscape/barracks-3.webp", "/dreamscape/barracks-4.webp"],
    sceneRect: null,
    active: true,
  },
  {
    slug: "obelisk-square",
    title: "Obelisk Square",
    src: "/dreamscape/obelisk-square.webp",
    width: 804,
    height: 1306,
    images: ["/dreamscape/obelisk-square.webp", "/dreamscape/obelisk-square-2.webp", "/dreamscape/obelisk-square-3.webp"],
    sceneRect: null,
    active: true,
  },
];

export const DREAMSCAPE_ACTIVE = DREAMSCAPE_SCENES.filter((s) => s.active);
export const DREAMSCAPE_ARCHIVE = DREAMSCAPE_SCENES.filter((s) => !s.active);

export function dreamscapeScene(slug: string): DreamscapeScene | undefined {
  return DREAMSCAPE_SCENES.find((s) => s.slug === slug);
}
