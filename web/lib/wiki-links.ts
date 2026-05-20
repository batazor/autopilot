/** Deep links into the Next.js wiki page (mirrors Streamlit wiki_db query keys). */
export function wikiBuildingHref(buildingId: string): string {
  return `/wiki?section=buildings&id=${encodeURIComponent(buildingId)}`;
}

export function wikiHeroHref(heroId: string): string {
  return `/wiki?section=heroes&id=${encodeURIComponent(heroId)}`;
}
