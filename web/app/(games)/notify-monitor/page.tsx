"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { FleetContextProvider } from "@/components/FleetContextProvider";
import { AppCombobox, AppTabs } from "@/components/headless";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { Icon, type IconName } from "@/components/ui/Icon";
import { PageLoading } from "@/components/ui/Spinner";
import { ApiError, fetchModuleScenarios } from "@/lib/api";
import type { SelectOption } from "@/components/AppSelect";
import { downloadCsv, type CsvColumn } from "@/lib/csv";
import {
  addNotifyPattern,
  addNotifyPlayer,
  deleteNotifyPattern,
  deleteNotifyPlayer,
  fetchNotifyEvents,
  fetchNotifyGames,
  fetchNotifyPatterns,
  fetchNotifyPlayers,
  fetchNotifySettings,
  fetchNotifyStatus,
  fetchNotifyUnrecognized,
  notifyPollNow,
  notifySetMonitor,
  promoteNotifyUnrecognized,
  reviewNotifyUnrecognized,
  setNotifyPlayerActive,
  testNotifyPattern,
  updateNotifyPattern,
  updateNotifySettings,
  type NotifyEvent,
  type NotifyGame,
  type NotifyPattern,
  type NotifyPlayer,
  type NotifyStatus,
  type NotifyUnrecognized,
  type PatternTestResult,
} from "@/lib/notify-api";

type TabKey = "dashboard" | "players" | "patterns" | "unrecognized" | "settings";

const errMsg = (e: unknown) =>
  e instanceof ApiError ? e.body || e.message : e instanceof Error ? e.message : String(e);

function GameTag({ game }: { game: string }) {
  return <span className="badge">{game}</span>;
}

function ExportCsvButton<T>({
  filename,
  rows,
  columns,
}: {
  filename: string;
  rows: T[];
  columns: CsvColumn<T>[];
}) {
  return (
    <button
      type="button"
      className="btn-secondary"
      disabled={rows.length === 0}
      onClick={() => downloadCsv(filename, rows, columns)}
      title={rows.length === 0 ? "Nothing to export" : `Export ${rows.length} row(s) to CSV`}
    >
      <Icon name="arrow-down" size="sm" />
      Export CSV
    </button>
  );
}

function NotifyEmpty({
  icon,
  title,
  hint,
}: {
  icon: IconName;
  title: string;
  hint?: string;
}) {
  return (
    <div className="nm-empty">
      <span className="nm-empty__icon" aria-hidden>
        <Icon name={icon} size="lg" />
      </span>
      <div className="nm-empty__title">{title}</div>
      {hint ? <div className="nm-empty__hint">{hint}</div> : null}
    </div>
  );
}

const PAGE_SIZE = 20;

/** Client-side pagination over an in-memory list. Clamps the current page when
 *  the underlying list shrinks (filter/refresh) so we never strand the user on
 *  an out-of-range page. */
function usePaged<T>(rows: T[], pageSize = PAGE_SIZE) {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  useEffect(() => {
    setPage((p) => Math.min(p, pageCount));
  }, [pageCount]);
  const current = Math.min(page, pageCount);
  const start = (current - 1) * pageSize;
  return {
    page: current,
    setPage,
    pageCount,
    pageRows: rows.slice(start, start + pageSize),
    total: rows.length,
    pageSize,
  };
}

function NotifyPager({
  page,
  pageCount,
  total,
  pageSize,
  onPage,
}: {
  page: number;
  pageCount: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
}) {
  if (pageCount <= 1) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  return (
    <div className="nm-pager">
      <span className="nm-pager__info">
        {from}–{to} of {total}
      </span>
      <div className="nm-pager__controls">
        <button
          type="button"
          className="nm-pager__btn"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label="Previous page"
        >
          <Icon name="chevron-left" size="sm" />
        </button>
        <span className="nm-pager__page">
          Page {page} / {pageCount}
        </span>
        <button
          type="button"
          className="nm-pager__btn"
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
          aria-label="Next page"
        >
          <Icon name="chevron-right" size="sm" />
        </button>
      </div>
    </div>
  );
}

function NotifyMonitorPageContent() {
  const { showSuccess } = useFeedback();
  const [tab, setTab] = useState<TabKey>("dashboard");
  const [games, setGames] = useState<NotifyGame[]>([]);
  const [status, setStatus] = useState<NotifyStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await fetchNotifyStatus());
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    fetchNotifyGames().then(setGames).catch((e) => setError(errMsg(e)));
    refreshStatus();
    const id = setInterval(refreshStatus, 5000);
    return () => clearInterval(id);
  }, [refreshStatus]);

  const pollNow = async () => {
    try {
      const r = await notifyPollNow();
      showSuccess(`Polled: ${JSON.stringify(r.summary)}`);
      refreshStatus();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const running = !!status?.monitor.running;
  const toggleMonitor = async () => {
    if (!status) return;
    try {
      await notifySetMonitor(running ? "stop" : "start");
      refreshStatus();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  return (
    <div className="nm-page">
      <FleetPageHeader
        title="Notify monitor"
        titleBadge={
          status ? (
            <span className={`status-pill ${running ? "pill-live" : "pill-offline"}`}>
              <span className="status-pill__dot" aria-hidden />
              {running ? "Running" : "Stopped"}
            </span>
          ) : null
        }
      >
        Watches Android notifications per player and turns them into bot events.
      </FleetPageHeader>

      <div className="toolbar-actions" style={{ marginBottom: 14 }}>
        <button type="button" className="btn-secondary" onClick={pollNow}>
          <Icon name="refresh" size="sm" />
          Poll now
        </button>
        <button
          type="button"
          className={running ? "btn-danger" : "btn-primary"}
          onClick={toggleMonitor}
        >
          <Icon name={running ? "stop" : "play"} size="sm" />
          {running ? "Stop monitor" : "Start monitor"}
        </button>
      </div>

      <ErrorBanner message={error} onRetry={refreshStatus} />

      <AppTabs
        selectedKey={tab}
        onChange={(k) => setTab(k as TabKey)}
        tabs={[
          {
            key: "dashboard",
            label: "Dashboard",
            panel: <DashboardTab status={status} games={games} />,
          },
          { key: "players", label: "Players", panel: <PlayersTab games={games} onChange={refreshStatus} /> },
          { key: "patterns", label: "Patterns", panel: <PatternsTab games={games} onChange={refreshStatus} /> },
          {
            key: "unrecognized",
            label: "Unrecognized",
            panel: <UnrecognizedTab onChange={refreshStatus} />,
          },
          { key: "settings", label: "Settings", panel: <SettingsTab onChange={refreshStatus} /> },
        ]}
      />
    </div>
  );
}

export default function NotifyMonitorPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <FleetContextProvider>
        <NotifyMonitorPageContent />
      </FleetContextProvider>
    </Suspense>
  );
}

// --- Dashboard --------------------------------------------------------------

function StatusCell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="nm-statusbar__group">
      <span className="nm-statusbar__label">{label}</span>
      <span className="nm-statusbar__value">{children}</span>
    </div>
  );
}

type MetricTone = "neutral" | "ok" | "warn" | "danger" | "accent";

function MetricCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  tone?: MetricTone;
}) {
  return (
    <div className={`stat-card${tone !== "neutral" ? ` stat-card--${tone}` : ""}`}>
      <span className="stat-card__accent" aria-hidden />
      <div className="stat-card__value">{value}</div>
      <div className="stat-card__label">{label}</div>
    </div>
  );
}

function DashboardTab({ status, games }: { status: NotifyStatus | null; games: NotifyGame[] }) {
  const [events, setEvents] = useState<NotifyEvent[]>([]);
  const [game, setGame] = useState("");

  const load = useCallback(() => {
    fetchNotifyEvents(game || undefined)
      .then(setEvents)
      .catch(() => undefined);
  }, [game]);

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  const { page, setPage, pageCount, pageRows, total, pageSize } = usePaged(events);

  const m = status?.monitor;
  const c = status?.counts;
  const running = !!m?.running;
  const redisOk = !!m?.redis.connected;
  const deviceCount = status?.adb_devices.length ?? 0;

  return (
    <section className="panel">
      <div className="nm-statusbar">
        <StatusCell label="Monitor">
          <span
            className={`nm-dot ${m && running ? "nm-dot--on" : "nm-dot--off"}`}
            aria-hidden
          />
          {m ? (running ? "Running" : "Stopped") : "—"}
        </StatusCell>
        <span className="nm-statusbar__divider" aria-hidden />
        <StatusCell label="Redis">
          <span
            className={`nm-dot ${m ? (redisOk ? "nm-dot--on" : "nm-dot--err") : "nm-dot--off"}`}
            aria-hidden
          />
          {m ? (redisOk ? "Connected" : "Down") : "—"}
        </StatusCell>
        <span className="nm-statusbar__divider" aria-hidden />
        <StatusCell label="Last poll">{m?.last_poll_human || "No poll yet"}</StatusCell>
        <span className="nm-statusbar__divider" aria-hidden />
        <StatusCell label="Devices">
          {deviceCount ? (
            <span title={status?.adb_devices.join(", ")}>{deviceCount} connected</span>
          ) : (
            <span className="muted">none</span>
          )}
        </StatusCell>
        {m?.last_error ? (
          <div className="nm-statusbar__error">
            <Icon name="warning" size="sm" />
            <span className="mono">{m.last_error}</span>
          </div>
        ) : null}
      </div>

      <div className="stat-grid">
        <MetricCard
          label="Active players"
          value={c ? `${c.active_players} / ${c.players}` : "—"}
          tone="accent"
        />
        <MetricCard label="Patterns" value={c?.patterns ?? "—"} />
        <MetricCard label="Events" value={c?.events ?? "—"} tone="ok" />
        <MetricCard
          label="Unrecognized"
          value={c?.unrecognized ?? "—"}
          tone={c && c.unrecognized > 0 ? "warn" : "neutral"}
        />
        <MetricCard label="Published" value={m?.redis.published_count ?? "—"} />
      </div>

      <div className="toolbar-actions" style={{ margin: "16px 0 12px" }}>
        <select value={game} onChange={(e) => setGame(e.target.value)}>
          <option value="">All games</option>
          {games.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button type="button" className="btn-secondary" onClick={load}>
          <Icon name="refresh" size="sm" />
          Refresh
        </button>
        <ExportCsvButton
          filename="notify-events.csv"
          rows={events}
          columns={[
            { header: "id", value: (e) => e.id },
            { header: "timestamp", value: (e) => e.timestamp },
            { header: "game", value: (e) => e.game },
            { header: "player", value: (e) => e.player },
            { header: "event_type", value: (e) => e.event_type },
            { header: "raw_text", value: (e) => e.raw_text },
          ]}
        />
        <span className="nm-count">{events.length} event(s)</span>
      </div>

      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Game</th>
              <th>Player</th>
              <th>Event</th>
              <th>Raw text</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <NotifyEmpty
                    icon="inbox-empty"
                    title="No events yet"
                    hint="Recognized notifications will appear here as they arrive."
                  />
                </td>
              </tr>
            ) : (
              pageRows.map((ev) => (
                <tr key={ev.id}>
                  <td className="mono muted">{ev.timestamp}</td>
                  <td>
                    <GameTag game={ev.game} />
                  </td>
                  <td>{ev.player}</td>
                  <td>
                    <span className="badge">{ev.event_type}</span>
                  </td>
                  <td className="muted">{ev.raw_text}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <NotifyPager
        page={page}
        pageCount={pageCount}
        total={total}
        pageSize={pageSize}
        onPage={setPage}
      />
    </section>
  );
}

// --- Players ----------------------------------------------------------------

function PlayersTab({ games, onChange }: { games: NotifyGame[]; onChange: () => void }) {
  const { showSuccess } = useFeedback();
  const [players, setPlayers] = useState<NotifyPlayer[]>([]);
  const [nickname, setNickname] = useState("");
  const [game, setGame] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchNotifyPlayers().then(setPlayers).catch((e) => setError(errMsg(e)));
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (games.length && !game) setGame(games[0].id);
  }, [games, game]);

  const { page, setPage, pageCount, pageRows, total, pageSize } = usePaged(players);

  const add = async () => {
    if (!nickname.trim()) return;
    try {
      await addNotifyPlayer(nickname.trim(), game);
      setNickname("");
      showSuccess("Player added");
      load();
      onChange();
    } catch (e) {
      setError(errMsg(e));
    }
  };
  const toggle = async (p: NotifyPlayer) => {
    await setNotifyPlayerActive(p.id, !p.active).catch((e) => setError(errMsg(e)));
    load();
    onChange();
  };
  const remove = async (p: NotifyPlayer) => {
    if (!confirm(`Delete player "${p.nickname}"?`)) return;
    await deleteNotifyPlayer(p.id).catch((e) => setError(errMsg(e)));
    load();
    onChange();
  };

  return (
    <section className="panel">
      <ErrorBanner message={error} />
      <div className="nm-card">
        <div className="nm-card__title">
          <span className="nm-card__title-icon" aria-hidden>
            <Icon name="plus" size="sm" />
          </span>
          Add player
        </div>
        <div className="toolbar-actions">
          <input placeholder="nickname" value={nickname} onChange={(e) => setNickname(e.target.value)} />
          <select value={game} onChange={(e) => setGame(e.target.value)}>
            {games.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn-primary" onClick={add}>
            Add player
          </button>
          <ExportCsvButton
            filename="notify-players.csv"
            rows={players}
            columns={[
              { header: "id", value: (p) => p.id },
              { header: "nickname", value: (p) => p.nickname },
              { header: "game", value: (p) => p.game },
              { header: "active", value: (p) => (p.active ? "yes" : "no") },
              { header: "created_at", value: (p) => p.created_at },
            ]}
          />
        </div>
        <p className="muted nm-card__note">Players are also auto-discovered from recognized events.</p>
      </div>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Nickname</th>
              <th>Game</th>
              <th>Active</th>
              <th>Added</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {players.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <NotifyEmpty
                    icon="list-empty"
                    title="No players yet"
                    hint="Add one above, or let recognized events discover them automatically."
                  />
                </td>
              </tr>
            ) : (
              pageRows.map((p) => (
                <tr key={p.id}>
                  <td>{p.nickname}</td>
                  <td>
                    <GameTag game={p.game} />
                  </td>
                  <td>
                    <input type="checkbox" checked={!!p.active} onChange={() => toggle(p)} />
                  </td>
                  <td className="mono muted">{p.created_at}</td>
                  <td>
                    <button type="button" className="btn btn-danger" onClick={() => remove(p)}>
                      <Icon name="trash" size="sm" />
                      Delete
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <NotifyPager
        page={page}
        pageCount={pageCount}
        total={total}
        pageSize={pageSize}
        onPage={setPage}
      />
    </section>
  );
}

// --- Patterns ---------------------------------------------------------------

function PatternsTab({ games, onChange }: { games: NotifyGame[]; onChange: () => void }) {
  const { showSuccess } = useFeedback();
  const [patterns, setPatterns] = useState<NotifyPattern[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  // add form
  const [game, setGame] = useState("");
  const [eventType, setEventType] = useState("");
  const [regex, setRegex] = useState("");
  const [desc, setDesc] = useState("");
  const [scenario, setScenario] = useState("");
  // tester
  const [testRegex, setTestRegex] = useState("");
  const [testText, setTestText] = useState("");
  const [testResult, setTestResult] = useState<PatternTestResult | null>(null);

  // Available DSL scenarios for the searchable scenario picker. The pushed
  // value is the scenario key (YAML filename, no ext) — exactly ScenarioRow.key.
  const [scenarioOptions, setScenarioOptions] = useState<SelectOption[]>([]);

  const load = useCallback(() => {
    fetchNotifyPatterns(filter || undefined)
      .then(setPatterns)
      .catch((e) => setError(errMsg(e)));
  }, [filter]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (games.length && !game) setGame(games[0].id);
  }, [games, game]);
  useEffect(() => {
    fetchModuleScenarios()
      .then((rows) => {
        const opts = rows
          .map((r) => ({
            value: r.key,
            label: r.name && r.name !== r.key ? `${r.key} — ${r.name}` : r.key,
          }))
          .sort((a, b) => a.value.localeCompare(b.value));
        setScenarioOptions([{ value: "", label: "— none —" }, ...opts]);
      })
      .catch(() => setScenarioOptions([{ value: "", label: "— none —" }]));
  }, []);

  const { page, setPage, pageCount, pageRows, total, pageSize } = usePaged(patterns);

  const add = async () => {
    if (!eventType.trim() || !regex) {
      setError("event_type and regex are required");
      return;
    }
    try {
      await addNotifyPattern({
        game,
        event_type: eventType.trim(),
        pattern_regex: regex,
        description: desc.trim(),
        scenario: scenario.trim(),
      });
      setEventType("");
      setRegex("");
      setDesc("");
      setScenario("");
      showSuccess("Pattern added");
      load();
      onChange();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const edit = async (
    p: NotifyPattern,
    field: "event_type" | "pattern_regex" | "description" | "scenario" | "active",
    value: string | boolean,
  ) => {
    try {
      await updateNotifyPattern(p.id, { [field]: value });
      load();
      onChange();
    } catch (e) {
      setError(errMsg(e));
      load();
    }
  };

  const remove = async (p: NotifyPattern) => {
    if (!confirm(`Delete pattern "${p.event_type}"?`)) return;
    await deleteNotifyPattern(p.id).catch((e) => setError(errMsg(e)));
    load();
    onChange();
  };

  const runTest = async () => {
    setTestResult(await testNotifyPattern(testRegex, testText).catch((e) => ({ ok: false, error: errMsg(e) })));
  };

  return (
    <section className="panel">
      <ErrorBanner message={error} />

      <div className="nm-card">
        <div className="nm-card__title">
          <span className="nm-card__title-icon" aria-hidden>
            <Icon name="plus" size="sm" />
          </span>
          Add pattern
        </div>
        <div className="toolbar-actions">
          <select value={game} onChange={(e) => setGame(e.target.value)}>
            {games.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
          <input placeholder="event_type" value={eventType} onChange={(e) => setEventType(e.target.value)} />
          <input
            placeholder="regex (case-insensitive)"
            className="mono"
            style={{ minWidth: 260 }}
            value={regex}
            onChange={(e) => setRegex(e.target.value)}
          />
          <input placeholder="description" value={desc} onChange={(e) => setDesc(e.target.value)} />
          <AppCombobox
            value={scenario}
            onChange={setScenario}
            options={scenarioOptions}
            placeholder="scenario to push (optional)"
            loading={scenarioOptions.length === 0}
            minWidth={240}
          />
          <button type="button" className="btn btn-primary" onClick={add}>
            Add
          </button>
        </div>
      </div>

      <div className="nm-card">
        <div className="nm-card__title">
          <span className="nm-card__title-icon" aria-hidden>
            <Icon name="search" size="sm" />
          </span>
          Test a pattern
        </div>
        <div className="toolbar-actions">
          <input
            placeholder="regex"
            className="mono"
            style={{ minWidth: 260 }}
            value={testRegex}
            onChange={(e) => setTestRegex(e.target.value)}
          />
          <input
            placeholder="sample notification text"
            style={{ minWidth: 320 }}
            value={testText}
            onChange={(e) => setTestText(e.target.value)}
          />
          <button type="button" className="btn-secondary" onClick={runTest}>
            Test
          </button>
          {testResult ? (
            !testResult.ok ? (
              <span className="nm-test-result nm-test-result--err">
                <Icon name="warning" size="sm" />
                error: {testResult.error}
              </span>
            ) : testResult.matched ? (
              <span className="nm-test-result nm-test-result--ok">
                <Icon name="check" size="sm" />
                match: &quot;{testResult.match}&quot;
                {testResult.groups && Object.keys(testResult.groups).length
                  ? ` groups=${JSON.stringify(testResult.groups)}`
                  : ""}
              </span>
            ) : (
              <span className="nm-test-result nm-test-result--no">
                <Icon name="close" size="sm" />
                no match
              </span>
            )
          ) : null}
        </div>
      </div>

      <div className="toolbar-actions" style={{ marginBottom: 12 }}>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All games</option>
          {games.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button type="button" className="btn-secondary" onClick={load}>
          <Icon name="refresh" size="sm" />
          Refresh
        </button>
        <ExportCsvButton
          filename="notify-patterns.csv"
          rows={patterns}
          columns={[
            { header: "id", value: (p) => p.id },
            { header: "game", value: (p) => p.game },
            { header: "event_type", value: (p) => p.event_type },
            { header: "pattern_regex", value: (p) => p.pattern_regex },
            { header: "description", value: (p) => p.description },
            { header: "scenario", value: (p) => p.scenario },
            { header: "active", value: (p) => (p.active ? "yes" : "no") },
          ]}
        />
        <span className="nm-count">{patterns.length} pattern(s)</span>
      </div>

      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Game</th>
              <th>Event type</th>
              <th>Regex</th>
              <th>Description</th>
              <th>Scenario</th>
              <th>Active</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {patterns.length === 0 ? (
              <tr>
                <td colSpan={7}>
                  <NotifyEmpty
                    icon="list-empty"
                    title="No patterns"
                    hint="Add a pattern above, or promote one from the Unrecognized tab."
                  />
                </td>
              </tr>
            ) : (
              pageRows.map((p) => (
                <tr key={p.id}>
                  <td>
                    <GameTag game={p.game} />
                  </td>
                  <td>
                    <input
                      defaultValue={p.event_type}
                      style={{ width: 160 }}
                      onBlur={(e) => e.target.value !== p.event_type && edit(p, "event_type", e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      defaultValue={p.pattern_regex}
                      className="mono"
                      style={{ width: 260 }}
                      onBlur={(e) => e.target.value !== p.pattern_regex && edit(p, "pattern_regex", e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      defaultValue={p.description}
                      style={{ width: 180 }}
                      onBlur={(e) => e.target.value !== p.description && edit(p, "description", e.target.value)}
                    />
                  </td>
                  <td>
                    <AppCombobox
                      value={p.scenario}
                      onChange={(v) => v !== p.scenario && edit(p, "scenario", v)}
                      options={scenarioOptions}
                      placeholder="—"
                      loading={scenarioOptions.length === 0}
                      minWidth={180}
                    />
                  </td>
                  <td>
                    <input type="checkbox" checked={!!p.active} onChange={(e) => edit(p, "active", e.target.checked)} />
                  </td>
                  <td>
                    <button type="button" className="btn btn-danger" onClick={() => remove(p)}>
                      <Icon name="trash" size="sm" />
                      Delete
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <NotifyPager
        page={page}
        pageCount={pageCount}
        total={total}
        pageSize={pageSize}
        onPage={setPage}
      />
    </section>
  );
}

// --- Unrecognized -----------------------------------------------------------

function UnrecognizedTab({ onChange }: { onChange: () => void }) {
  const { showSuccess } = useFeedback();
  const [rows, setRows] = useState<NotifyUnrecognized[]>([]);
  const [includeReviewed, setIncludeReviewed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchNotifyUnrecognized(includeReviewed)
      .then(setRows)
      .catch((e) => setError(errMsg(e)));
  }, [includeReviewed]);
  useEffect(() => {
    load();
  }, [load]);

  const { page, setPage, pageCount, pageRows, total, pageSize } = usePaged(rows);

  const review = async (id: number) => {
    await reviewNotifyUnrecognized(id).catch((e) => setError(errMsg(e)));
    load();
    onChange();
  };

  const promote = async (u: NotifyUnrecognized) => {
    const event_type = prompt("event_type for this notification:", "");
    if (!event_type) return;
    const suggested = u.raw_text.slice(0, 40).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const pattern_regex = prompt("regex to match this event (case-insensitive):", suggested);
    if (!pattern_regex) return;
    try {
      await promoteNotifyUnrecognized(u.id, { event_type, pattern_regex, description: "promoted from unrecognized" });
      showSuccess("Pattern created");
      load();
      onChange();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  return (
    <section className="panel">
      <ErrorBanner message={error} />
      <div className="toolbar-actions" style={{ marginBottom: 12 }}>
        <label className="nm-check">
          <input
            type="checkbox"
            checked={includeReviewed}
            onChange={(e) => setIncludeReviewed(e.target.checked)}
          />
          show reviewed
        </label>
        <button type="button" className="btn-secondary" onClick={load}>
          <Icon name="refresh" size="sm" />
          Refresh
        </button>
        <ExportCsvButton
          filename="notify-unrecognized.csv"
          rows={rows}
          columns={[
            { header: "id", value: (u) => u.id },
            { header: "timestamp", value: (u) => u.timestamp },
            { header: "game", value: (u) => u.game },
            { header: "raw_text", value: (u) => u.raw_text },
            { header: "reviewed", value: (u) => (u.reviewed ? "yes" : "no") },
          ]}
        />
        <span className="nm-count">{rows.length} row(s)</span>
      </div>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Game</th>
              <th>Raw text</th>
              <th>Reviewed</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <NotifyEmpty
                    icon="check"
                    title="Nothing unrecognized"
                    hint="Every captured notification matched a known pattern."
                  />
                </td>
              </tr>
            ) : (
              pageRows.map((u) => (
                <tr key={u.id}>
                  <td className="mono muted">{u.timestamp}</td>
                  <td>
                    <GameTag game={u.game} />
                  </td>
                  <td className="muted">{u.raw_text}</td>
                  <td>{u.reviewed ? <Icon name="check" size="sm" /> : "—"}</td>
                  <td>
                    <div className="toolbar-actions">
                      {!u.reviewed ? (
                        <button type="button" className="btn-secondary" onClick={() => review(u.id)}>
                          Mark reviewed
                        </button>
                      ) : null}
                      <button type="button" className="btn btn-primary" onClick={() => promote(u)}>
                        Promote
                        <Icon name="chevron-right" size="sm" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <NotifyPager
        page={page}
        pageCount={pageCount}
        total={total}
        pageSize={pageSize}
        onPage={setPage}
      />
    </section>
  );
}

// --- Settings ---------------------------------------------------------------

function SettingsTab({ onChange }: { onChange: () => void }) {
  const { showSuccess } = useFeedback();
  const [interval, setIntervalValue] = useState(10);
  const [serial, setSerial] = useState("");
  const [adbPath, setAdbPath] = useState("adb");
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchNotifySettings()
      .then((s) => {
        setIntervalValue(Number(s.poll_interval ?? 10));
        setSerial(s.adb_serial ?? "");
        setAdbPath(s.adb_path ?? "adb");
        setEnabled(s.monitor_enabled === "1");
      })
      .catch((e) => setError(errMsg(e)));
  }, []);

  const save = async () => {
    try {
      await updateNotifySettings({
        poll_interval: interval,
        adb_serial: serial,
        adb_path: adbPath,
        monitor_enabled: enabled,
      });
      showSuccess("Settings saved");
      onChange();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  return (
    <section className="panel">
      <ErrorBanner message={error} />
      <div className="nm-card" style={{ maxWidth: 520 }}>
        <div className="nm-card__title">
          <span className="nm-card__title-icon" aria-hidden>
            <Icon name="config" size="sm" />
          </span>
          Monitor settings
        </div>
        <div className="form-field">
          <label htmlFor="nm-interval">Polling interval (seconds)</label>
          <input
            id="nm-interval"
            type="number"
            min={1}
            value={interval}
            onChange={(e) => setIntervalValue(Number(e.target.value))}
          />
        </div>
        <div className="form-field" style={{ marginTop: 12 }}>
          <label htmlFor="nm-serial">ADB serial (blank = default device)</label>
          <input id="nm-serial" placeholder="emulator-5554" value={serial} onChange={(e) => setSerial(e.target.value)} />
        </div>
        <div className="form-field" style={{ marginTop: 12 }}>
          <label htmlFor="nm-adb">ADB path</label>
          <input id="nm-adb" value={adbPath} onChange={(e) => setAdbPath(e.target.value)} />
        </div>
        <label className="nm-check" style={{ marginTop: 14 }}>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          monitor enabled
        </label>
        <div style={{ marginTop: 18 }}>
          <button type="button" className="btn btn-primary" onClick={save}>
            Save settings
          </button>
        </div>
      </div>
    </section>
  );
}
