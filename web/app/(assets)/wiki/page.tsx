"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { AppListbox, AppTabs } from "@/components/headless";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { WikiDetailPanel } from "@/components/wiki/WikiDetailPanel";
import {
  fetchWikiDetail,
  fetchWikiEntries,
  fetchWikiGearDetail,
  fetchWikiGearList,
  fetchWikiScopes,
  wikiIconUrl,
} from "@/lib/api";
import type { WikiDetail, WikiEntrySummary, WikiScope } from "@/lib/wiki";

// Buildings moved to the /trees graph (table + click-to-detail); the wiki keeps
// the remaining reference entity types.
type EntityTab = "heroes" | "items" | "gear";

const TABS: { key: EntityTab; label: string }[] = [
  { key: "heroes", label: "Heroes" },
  { key: "gear", label: "Gear" },
  { key: "items", label: "Items" },
];

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
  const [scopes, setScopes] = useState<WikiScope[]>([]);
  const [scope, setScope] = useState("all");
  const [search, setSearch] = useState("");
  const [entries, setEntries] = useState<WikiEntrySummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<WikiDetail | Record<string, unknown> | null>(
    null,
  );
  const [gearList, setGearList] = useState<Array<{ id: string; title: string }>>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchWikiScopes().then(setScopes).catch(() => {});
  }, []);

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
      const data = await fetchWikiEntries(tab, scope, search);
      setEntries(data.entries);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [tab, scope, search]);

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
    fetchWikiDetail(tab, selectedId, scope)
      .then(setDetail)
      .catch((e: Error) => setError(e.message));
  }, [selectedId, tab, scope]);

  const onTab = (key: EntityTab) => {
    setTab(key);
    setSelectedId(null);
    setDetail(null);
    const url = new URL(window.location.href);
    url.searchParams.set("section", key);
    window.history.replaceState(null, "", url.pathname + url.search);
  };

  const showSearch = tab !== "gear";
  const listCount = tab === "gear" ? gearList.length : entries.length;
  const countNoun = tab === "gear" ? "gear sets" : "entries";
  const activeTabLabel = TABS.find((t) => t.key === tab)?.label ?? "";

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

        {tab !== "gear" ? (
          <div className="wiki-filterbar__field">
            <AppListbox
              fullWidth
              label="Scope"
              value={scope}
              onChange={setScope}
              options={scopes.map((s) => ({ value: s.key, label: s.label }))}
              maxWidth={220}
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
          <div className="wiki-list-panel__head">
            <h2>{activeTabLabel}</h2>
            <span className="wiki-count-chip">{listCount}</span>
          </div>
          {tab !== "gear" ? (
            entries.length === 0 ? (
              <div className="wiki-empty wiki-empty--list">
                <span className="wiki-empty__icon">🔍</span>
                <p>No entries match the current filters.</p>
              </div>
            ) : (
              <div className="wiki-tiles">
                {entries.map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    className={`wiki-tile${selectedId === e.id ? " active" : ""}`}
                    onClick={() => setSelectedId(e.id)}
                    title={e.name || e.id}
                  >
                    {e.has_icon ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={wikiIconUrl(tab, e.id)}
                        alt=""
                        className="wiki-tile__img"
                      />
                    ) : (
                      <div className="wiki-tile__placeholder">?</div>
                    )}
                    <span className="wiki-tile__name">{e.name || e.id}</span>
                    {e.source !== "core" ? (
                      <span className="wiki-tile__module">{e.source}</span>
                    ) : null}
                  </button>
                ))}
              </div>
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
