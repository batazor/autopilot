"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AppListbox, AppTabs } from "@/components/headless";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { WikiDetailPanel } from "@/components/wiki/WikiDetailPanel";
import {
  fetchWikiDetail,
  fetchWikiEntries,
  fetchWikiGearDetail,
  fetchWikiGearList,
  wikiIconUrl,
} from "@/lib/api";
import type { WikiDetail, WikiEntrySummary } from "@/lib/wiki";

// Buildings moved to the /trees graph (table + click-to-detail); the wiki keeps
// the remaining reference entity types.
type EntityTab = "heroes" | "items" | "gear";

const TABS: { key: EntityTab; label: string }[] = [
  { key: "heroes", label: "Heroes" },
  { key: "gear", label: "Gear" },
  { key: "items", label: "Items" },
];

// Troop classes. Each generation is a trio of one Infantry / Lancer / Marksman.
type UnitClass = "infantry" | "lancer" | "marksman";
const CLASS_LABEL: Record<UnitClass, string> = {
  infantry: "Infantry",
  lancer: "Lancer",
  marksman: "Marksman",
};
const CLASS_FILTERS: { value: string; label: string }[] = [
  { value: "all", label: "All classes" },
  { value: "infantry", label: "Infantry" },
  { value: "lancer", label: "Lancer" },
  { value: "marksman", label: "Marksman" },
];

// Heroes are grouped by release generation in the list. Generations sort
// ascending (Gen 1 first); non-generation heroes (Epic/Rare, generation: null)
// fall into a trailing "Non-generation" bucket.
type GenGroup = { key: string; short: string; label: string; items: WikiEntrySummary[] };

function groupByGeneration(list: WikiEntrySummary[]): GenGroup[] {
  const byGen = new Map<number, WikiEntrySummary[]>();
  const other: WikiEntrySummary[] = [];
  for (const e of list) {
    if (typeof e.generation === "number") {
      const bucket = byGen.get(e.generation) ?? [];
      bucket.push(e);
      byGen.set(e.generation, bucket);
    } else {
      other.push(e);
    }
  }
  const groups: GenGroup[] = [...byGen.keys()]
    .sort((a, b) => a - b)
    .map((g) => ({
      key: `gen-${g}`,
      short: `G${g}`,
      label: `Generation ${g}`,
      items: byGen.get(g)!,
    }));
  if (other.length) {
    groups.push({ key: "other", short: "NG", label: "Non-generation", items: other });
  }
  return groups;
}

// Game selector. Wiki content is Whiteout Survival only for now; other games
// are listed as "(soon)" and can't be selected until their wiki data lands.
const WIKI_GAMES: { value: string; label: string; available: boolean }[] = [
  { value: "wos", label: "Whiteout Survival", available: true },
  { value: "kingshot", label: "Kingshot (soon)", available: false },
];

function WikiPageInner() {
  const params = useSearchParams();
  const router = useRouter();
  const sectionParam = params.get("section");
  const [tab, setTab] = useState<EntityTab>(
    TABS.some((t) => t.key === sectionParam)
      ? (sectionParam as EntityTab)
      : "heroes",
  );

  // Buildings now live on the /trees graph — redirect old deep links there.
  useEffect(() => {
    if (sectionParam === "buildings") {
      router.replace("/trees?game=wos&tab=buildings");
    }
  }, [sectionParam, router]);
  const [game, setGame] = useState("wos");
  const [search, setSearch] = useState("");
  const [entries, setEntries] = useState<WikiEntrySummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<WikiDetail | Record<string, unknown> | null>(
    null,
  );
  const [gearList, setGearList] = useState<Array<{ id: string; title: string }>>([]);
  const [error, setError] = useState<string | null>(null);
  const [classFilter, setClassFilter] = useState("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // Heroes filtered by troop class (client-side; class lives on the summary).
  const heroEntries = useMemo(
    () =>
      classFilter === "all"
        ? entries
        : entries.filter((e) => e.unit_class === classFilter),
    [entries, classFilter],
  );
  const groups = useMemo(() => groupByGeneration(heroEntries), [heroEntries]);

  const toggleGroup = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const jumpToGroup = (key: string) =>
    document
      .getElementById(`wiki-group-${key}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });

  const deepLinkId =
    params.get("id")?.trim() ||
    (tab === "heroes" ? params.get("hero")?.trim() : null) ||
    null;

  useEffect(() => {
    if (!deepLinkId || tab === "gear") return;
    if (entries.some((e) => e.id === deepLinkId)) {
      setSelectedId(deepLinkId);
    }
  }, [deepLinkId, tab, entries]);

  const loadEntries = useCallback(async () => {
    if (tab === "gear") return;
    try {
      const data = await fetchWikiEntries(tab, "all", search);
      setEntries(data.entries);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [tab, search]);

  useEffect(() => {
    loadEntries();
  }, [loadEntries]);

  useEffect(() => {
    if (tab === "gear") {
      fetchWikiGearList()
        .then((g) => setGearList(g.entries))
        .catch((e: Error) => setError(e.message));
      return;
    }
    setGearList([]);
  }, [tab]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    if (tab === "gear") {
      fetchWikiGearDetail(selectedId)
        .then((g) => setDetail({ entity: "gear", summary: { id: g.id, name: g.id, source: "core", wiki_url: "", has_icon: false, yaml_path: "" }, body: g.body }))
        .catch((e: Error) => setError(e.message));
      return;
    }
    fetchWikiDetail(tab, selectedId)
      .then(setDetail)
      .catch((e: Error) => setError(e.message));
  }, [selectedId, tab]);

  const onTab = (key: EntityTab) => {
    setTab(key);
    setSelectedId(null);
    setDetail(null);
    const url = new URL(window.location.href);
    url.searchParams.set("section", key);
    window.history.replaceState(null, "", url.pathname + url.search);
  };

  const renderTile = (e: WikiEntrySummary) => (
    <button
      key={e.id}
      type="button"
      className={`wiki-tile${selectedId === e.id ? " active" : ""}`}
      onClick={() => setSelectedId(e.id)}
      title={e.name || e.id}
    >
      {e.paid_only ? (
        <span className="wiki-tile__paid" title="Only available as a paid (premium) hero">
          💎 Paid
        </span>
      ) : null}
      {e.has_icon ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={wikiIconUrl(tab, e.id)}
          alt=""
          className="wiki-tile__img"
          width={48}
          height={48}
          loading="lazy"
          decoding="async"
        />
      ) : (
        <div className="wiki-tile__placeholder">?</div>
      )}
      <span className="wiki-tile__name">{e.name || e.id}</span>
      {e.unit_class ? (
        <span className={`wiki-tile__class wiki-tile__class--${e.unit_class}`}>
          {CLASS_LABEL[e.unit_class]}
        </span>
      ) : null}
      {e.source !== "core" ? (
        <span className="wiki-tile__module">{e.source}</span>
      ) : null}
    </button>
  );

  const showSearch = tab !== "gear";
  const listCount =
    tab === "gear"
      ? gearList.length
      : tab === "heroes"
        ? heroEntries.length
        : entries.length;
  const countNoun =
    tab === "gear" ? "gear sets" : tab === "heroes" ? "heroes" : "entries";

  return (
    <>
      <PageHeader title="Wiki reference">
        Heroes, gear and items — reference data from <code>db/</code> and{" "}
        <code>modules/*/wiki/</code>. Buildings moved to the{" "}
        <a className="underline" href="/trees?game=wos&tab=buildings">
          Game trees
        </a>{" "}
        graph.
      </PageHeader>

      {error ? <div className="error-banner">{error}</div> : null}

      <AppTabs
        variant="section"
        tabs={TABS}
        selectedKey={tab}
        onChange={(key) => onTab(key as EntityTab)}
        renderPanels={false}
      />

      <div className="wiki-filterbar">
        <div className="wiki-filterbar__field">
          <AppListbox
            fullWidth
            label="Game"
            value={game}
            onChange={(v) => {
              // Only switch to a game whose wiki data has shipped.
              if (WIKI_GAMES.find((g) => g.value === v)?.available) setGame(v);
            }}
            options={WIKI_GAMES.map((g) => ({ value: g.value, label: g.label }))}
            maxWidth={220}
          />
        </div>

        {tab === "heroes" ? (
          <div className="wiki-filterbar__field">
            <AppListbox
              fullWidth
              label="Class"
              value={classFilter}
              onChange={setClassFilter}
              options={CLASS_FILTERS}
              maxWidth={200}
            />
          </div>
        ) : null}

        {showSearch ? (
          <label className="wiki-search">
            <span>Search</span>
            <div className="wiki-search__field">
              <input
                type="search"
                placeholder="Name or id…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="wiki-search__input"
              />
              {search ? (
                <button
                  type="button"
                  className="wiki-search__clear"
                  aria-label="Clear search"
                  onClick={() => setSearch("")}
                >
                  ✕
                </button>
              ) : null}
            </div>
          </label>
        ) : null}

        <div className="wiki-filterbar__count">
          <strong>{listCount}</strong> {countNoun}
        </div>
      </div>

      <div className="wiki-layout">
        <section className="panel wiki-list-panel">
          {tab === "heroes" ? (
            heroEntries.length === 0 ? (
              <div className="wiki-empty wiki-empty--list">
                <span className="wiki-empty__icon">🔍</span>
                <p>No heroes match the current filters.</p>
              </div>
            ) : (
              <>
                <nav className="wiki-gen-nav" aria-label="Jump to generation">
                  {groups.map((grp) => (
                    <button
                      key={grp.key}
                      type="button"
                      className="wiki-gen-nav__btn"
                      onClick={() => jumpToGroup(grp.key)}
                      title={grp.label}
                    >
                      {grp.short}
                    </button>
                  ))}
                </nav>
                <div className="wiki-gen-groups">
                  {groups.map((grp) => {
                    const isCollapsed = collapsed.has(grp.key);
                    return (
                      <div
                        key={grp.key}
                        id={`wiki-group-${grp.key}`}
                        className="wiki-gen-group"
                      >
                        <button
                          type="button"
                          className="wiki-gen-group__head"
                          onClick={() => toggleGroup(grp.key)}
                          aria-expanded={!isCollapsed}
                        >
                          <span
                            className={`wiki-gen-group__chevron${isCollapsed ? " is-collapsed" : ""}`}
                            aria-hidden
                          >
                            ▾
                          </span>
                          <h3 className="wiki-gen-group__title">{grp.label}</h3>
                          <span className="wiki-count-chip">{grp.items.length}</span>
                        </button>
                        {!isCollapsed ? (
                          <div className="wiki-tiles">{grp.items.map(renderTile)}</div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </>
            )
          ) : tab === "items" ? (
            entries.length === 0 ? (
              <div className="wiki-empty wiki-empty--list">
                <span className="wiki-empty__icon">🔍</span>
                <p>No entries match the current filters.</p>
              </div>
            ) : (
              <div className="wiki-tiles">{entries.map(renderTile)}</div>
            )
          ) : gearList.length === 0 ? (
            <div className="wiki-empty wiki-empty--list">
              <span className="wiki-empty__icon">🛡️</span>
              <p>No gear sets available.</p>
            </div>
          ) : (
            <ul className="ref-list wiki-gear-list">
              {gearList.map((g) => (
                <li key={g.id}>
                  <button
                    type="button"
                    className={selectedId === g.id ? "active" : undefined}
                    onClick={() => setSelectedId(g.id)}
                  >
                    {g.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel wiki-detail-panel">
          {detail ? (
            <WikiDetailPanel detail={detail as WikiDetail | null} />
          ) : (
            <div className="wiki-empty">
              <span className="wiki-empty__icon">📖</span>
              <p>Select an entry to see its details.</p>
            </div>
          )}
        </section>
      </div>
    </>
  );
}

export default function WikiPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <WikiPageInner />
    </Suspense>
  );
}
