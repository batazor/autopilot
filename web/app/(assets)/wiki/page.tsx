"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { AppListbox, AppTabs } from "@/components/headless";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { WikiDetailPanel } from "@/components/wiki/WikiDetailPanel";
import { WikiFaqSync } from "@/components/wiki/WikiFaqSync";
import {
  fetchWikiDetail,
  fetchWikiEntries,
  fetchWikiFaq,
  fetchWikiGearDetail,
  fetchWikiGearList,
  fetchWikiScopes,
  wikiIconUrl,
} from "@/lib/api";
import type { WikiDetail, WikiEntrySummary, WikiScope } from "@/lib/wiki";

type EntityTab = "buildings" | "heroes" | "items" | "gear" | "faq";

const TABS: { key: EntityTab; label: string }[] = [
  { key: "buildings", label: "Buildings" },
  { key: "heroes", label: "Heroes" },
  { key: "gear", label: "Gear" },
  { key: "items", label: "Items" },
  { key: "faq", label: "FAQ" },
];

function WikiPageInner() {
  const params = useSearchParams();
  const [tab, setTab] = useState<EntityTab>(
    (params.get("section") as EntityTab) || "buildings",
  );
  const [scopes, setScopes] = useState<WikiScope[]>([]);
  const [scope, setScope] = useState("all");
  const [search, setSearch] = useState("");
  const [entries, setEntries] = useState<WikiEntrySummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<WikiDetail | Record<string, unknown> | null>(
    null,
  );
  const [gearList, setGearList] = useState<Array<{ id: string; title: string }>>([]);
  const [faq, setFaq] = useState<Awaited<ReturnType<typeof fetchWikiFaq>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchWikiScopes().then(setScopes).catch(() => {});
  }, []);

  const deepLinkId =
    params.get("id")?.trim() ||
    (tab === "buildings" ? params.get("building")?.trim() : null) ||
    (tab === "heroes" ? params.get("hero")?.trim() : null) ||
    null;

  useEffect(() => {
    if (!deepLinkId || tab === "gear" || tab === "faq") return;
    if (entries.some((e) => e.id === deepLinkId)) {
      setSelectedId(deepLinkId);
    }
  }, [deepLinkId, tab, entries]);

  const loadEntries = useCallback(async () => {
    if (tab === "gear" || tab === "faq") return;
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
    if (tab === "faq") {
      fetchWikiFaq().then(setFaq).catch((e: Error) => setError(e.message));
      return;
    }
    setGearList([]);
    setFaq(null);
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
    if (tab === "faq") return;
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

  return (
    <>
      <PageHeader title="Wiki reference" />
      <p className="meta">
        Buildings, heroes, items, gear — data from <code>db/</code> and{" "}
        <code>modules/*/wiki/</code>.
      </p>
      {error ? <div className="error-banner">{error}</div> : null}

      <AppTabs
        tabs={TABS}
        selectedKey={tab}
        onChange={(key) => onTab(key as EntityTab)}
        renderPanels={false}
        afterTabs={
          tab !== "gear" && tab !== "faq" ? (
            <AppListbox
              inline
              className="meta"
              label="Scope"
              value={scope}
              onChange={setScope}
              options={scopes.map((s) => ({ value: s.key, label: s.label }))}
              minWidth={160}
            />
          ) : null
        }
      />

      {tab === "faq" && faq ? (
        <section className="panel">
          {faq.sections.map((sec) => (
            <div key={sec.heading} className="wiki-section-block">
              <h2>{sec.heading}</h2>
              {sec.text ? <p>{sec.text}</p> : null}
              {sec.items?.length && sec.items[0].key ? (
                <WikiFaqSync items={sec.items} />
              ) : null}
            </div>
          ))}
        </section>
      ) : null}

      {tab !== "faq" ? (
        <div className="wiki-layout">
          <section className="panel">
            {tab !== "gear" ? (
              <>
                <input
                  type="search"
                  placeholder="Search…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  style={{ width: "100%", marginBottom: "0.75rem" }}
                />
                <div className="wiki-tiles">
                  {entries.map((e) => (
                    <button
                      key={e.id}
                      type="button"
                      className={`wiki-tile${selectedId === e.id ? " active" : ""}`}
                      onClick={() => setSelectedId(e.id)}
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
                <p className="meta">{entries.length} entries</p>
              </>
            ) : (
              <ul className="ref-list">
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
            <WikiDetailPanel detail={detail as WikiDetail | null} />
          </section>
        </div>
      ) : null}
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
