"use client";

import { useCallback, useEffect, useState } from "react";
import { AppTabs } from "@/components/headless";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { ApiError } from "@/lib/api";
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
      className="btn"
      disabled={rows.length === 0}
      onClick={() => downloadCsv(filename, rows, columns)}
      title={rows.length === 0 ? "Nothing to export" : `Export ${rows.length} row(s) to CSV`}
    >
      Export CSV
    </button>
  );
}

export default function NotifyMonitorPage() {
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

  const toggleMonitor = async () => {
    if (!status) return;
    try {
      await notifySetMonitor(status.monitor.running ? "stop" : "start");
      refreshStatus();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  return (
    <>
      <FleetPageHeader title="Notify monitor">
        <div className="toolbar-actions">
          <button type="button" className="btn" onClick={pollNow}>
            Poll now
          </button>
          <button type="button" className="btn" onClick={toggleMonitor}>
            {status?.monitor.running ? "Stop monitor" : "Start monitor"}
          </button>
        </div>
      </FleetPageHeader>

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
    </>
  );
}

// --- Dashboard --------------------------------------------------------------

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

  const m = status?.monitor;
  const c = status?.counts;
  const cards: [string, string | number][] = [
    ["Monitor", m ? (m.running ? "running" : "stopped") : "—"],
    ["Redis", m ? (m.redis.connected ? "connected" : "down") : "—"],
    ["Active players", c ? `${c.active_players} / ${c.players}` : "—"],
    ["Patterns", c?.patterns ?? "—"],
    ["Events", c?.events ?? "—"],
    ["Unrecognized", c?.unrecognized ?? "—"],
    ["Published", m?.redis.published_count ?? "—"],
  ];

  return (
    <section className="panel">
      <div className="stat-grid">
        {cards.map(([label, value]) => (
          <div key={label} className="stat-card">
            <div className="stat-card__value">{value}</div>
            <div className="stat-card__label">{label}</div>
          </div>
        ))}
      </div>
      <p className="muted" style={{ margin: "8px 0" }}>
        {m?.last_poll_human ? `Last poll ${m.last_poll_human}` : "No poll yet"}
        {status?.adb_devices.length ? ` · ${status.adb_devices.length} device(s): ${status.adb_devices.join(", ")}` : " · no devices"}
        {m?.last_error ? ` · error: ${m.last_error}` : ""}
      </p>

      <div className="toolbar-actions" style={{ margin: "12px 0" }}>
        <select value={game} onChange={(e) => setGame(e.target.value)}>
          <option value="">All games</option>
          {games.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button type="button" className="btn" onClick={load}>
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
        <span className="muted">{events.length} event(s)</span>
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
                <td colSpan={5} className="muted" style={{ textAlign: "center", padding: 24 }}>
                  no events yet
                </td>
              </tr>
            ) : (
              events.map((ev) => (
                <tr key={ev.id}>
                  <td className="mono">{ev.timestamp}</td>
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
      <div className="toolbar-actions" style={{ marginBottom: 12 }}>
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
        <span className="muted">Players are also auto-discovered from recognized events.</span>
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
            {players.map((p) => (
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
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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

      <div className="panel-subtle" style={{ padding: 12, marginBottom: 12 }}>
        <strong>Add pattern</strong>
        <div className="toolbar-actions" style={{ marginTop: 8, flexWrap: "wrap" }}>
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
          <input
            placeholder="scenario to push (optional)"
            className="mono"
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
          />
          <button type="button" className="btn btn-primary" onClick={add}>
            Add
          </button>
        </div>
      </div>

      <div className="panel-subtle" style={{ padding: 12, marginBottom: 12 }}>
        <strong>Test a pattern</strong>
        <div className="toolbar-actions" style={{ marginTop: 8, flexWrap: "wrap" }}>
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
          <button type="button" className="btn" onClick={runTest}>
            Test
          </button>
          {testResult ? (
            <span className="mono">
              {!testResult.ok
                ? `error: ${testResult.error}`
                : testResult.matched
                  ? `✓ match: "${testResult.match}"${
                      testResult.groups && Object.keys(testResult.groups).length
                        ? ` groups=${JSON.stringify(testResult.groups)}`
                        : ""
                    }`
                  : "✗ no match"}
            </span>
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
        <button type="button" className="btn" onClick={load}>
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
            {patterns.map((p) => (
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
                  <input
                    defaultValue={p.scenario}
                    className="mono"
                    placeholder="—"
                    style={{ width: 170 }}
                    onBlur={(e) => e.target.value !== p.scenario && edit(p, "scenario", e.target.value)}
                  />
                </td>
                <td>
                  <input type="checkbox" checked={!!p.active} onChange={(e) => edit(p, "active", e.target.checked)} />
                </td>
                <td>
                  <button type="button" className="btn btn-danger" onClick={() => remove(p)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
        <label>
          <input
            type="checkbox"
            checked={includeReviewed}
            onChange={(e) => setIncludeReviewed(e.target.checked)}
          />{" "}
          show reviewed
        </label>
        <button type="button" className="btn" onClick={load}>
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
                <td colSpan={5} className="muted" style={{ textAlign: "center", padding: 24 }}>
                  nothing unrecognized
                </td>
              </tr>
            ) : (
              rows.map((u) => (
                <tr key={u.id}>
                  <td className="mono">{u.timestamp}</td>
                  <td>
                    <GameTag game={u.game} />
                  </td>
                  <td className="muted">{u.raw_text}</td>
                  <td>{u.reviewed ? "✓" : "—"}</td>
                  <td>
                    <div className="toolbar-actions">
                      {!u.reviewed ? (
                        <button type="button" className="btn" onClick={() => review(u.id)}>
                          Mark reviewed
                        </button>
                      ) : null}
                      <button type="button" className="btn btn-primary" onClick={() => promote(u)}>
                        Promote →
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
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
    <section className="panel" style={{ maxWidth: 520 }}>
      <ErrorBanner message={error} />
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
      <label style={{ display: "block", marginTop: 12 }}>
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> monitor enabled
      </label>
      <button type="button" className="btn btn-primary" style={{ marginTop: 16 }} onClick={save}>
        Save settings
      </button>
    </section>
  );
}
