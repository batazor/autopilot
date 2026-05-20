"use client";

import { useCallback, useRef, useState } from "react";
import { runWikiSync, type WikiFaqItem, type WikiSyncEvent } from "@/lib/api";

type JobState = {
  key: string;
  label: string;
  status: "running" | "ok" | "error";
  done: number;
  total: number;
  log: string[];
  summary?: string;
  elapsed?: number;
  exitCode?: number;
  error?: string;
};

export function WikiFaqSync({ items }: { items: WikiFaqItem[] }) {
  const [job, setJob] = useState<JobState | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const onEvent = useCallback((key: string, label: string, ev: WikiSyncEvent) => {
    setJob((prev) => {
      const base: JobState =
        prev?.key === key
          ? prev
          : {
              key,
              label,
              status: "running",
              done: 0,
              total: 0,
              log: [],
            };
      if (ev.type === "line") {
        const log = [...base.log, ev.text].slice(-200);
        return { ...base, log };
      }
      if (ev.type === "progress") {
        return { ...base, done: ev.done, total: ev.total };
      }
      if (ev.type === "done") {
        return {
          ...base,
          status: ev.exit_code === 0 ? "ok" : "error",
          done: ev.done,
          total: ev.total,
          summary: ev.summary,
          elapsed: ev.elapsed,
          exitCode: ev.exit_code,
        };
      }
      if (ev.type === "error") {
        return { ...base, status: "error", error: ev.message };
      }
      if (ev.type === "start" && ev.progress_total_hint) {
        return { ...base, total: ev.progress_total_hint };
      }
      return base;
    });
  }, []);

  const run = async (item: WikiFaqItem) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setJob({
      key: item.key,
      label: item.label,
      status: "running",
      done: 0,
      total: 0,
      log: [],
    });
    try {
      await runWikiSync(item.key, (ev) => onEvent(item.key, item.label, ev), ac.signal);
    } catch (e) {
      if (ac.signal.aborted) return;
      const message = e instanceof Error ? e.message : String(e);
      setJob((prev) =>
        prev?.key === item.key
          ? { ...prev, status: "error", error: message }
          : {
              key: item.key,
              label: item.label,
              status: "error",
              done: 0,
              total: 0,
              log: [],
              error: message,
            },
      );
    }
  };

  const half = Math.ceil(items.length / 2);
  const left = items.slice(0, half);
  const right = items.slice(half);
  const busy = job?.status === "running";

  return (
    <div className="wiki-faq-sync">
      <div className="wiki-faq-sync__buttons">
        <div className="wiki-faq-sync__col">
          {left.map((it) => (
            <button
              key={it.key}
              type="button"
              className="btn-secondary"
              style={{ width: "100%", marginBottom: 8 }}
              disabled={busy}
              onClick={() => run(it)}
            >
              {it.label}
            </button>
          ))}
        </div>
        <div className="wiki-faq-sync__col">
          {right.map((it) => (
            <button
              key={it.key}
              type="button"
              className="btn-secondary"
              style={{ width: "100%", marginBottom: 8 }}
              disabled={busy}
              onClick={() => run(it)}
            >
              {it.label}
            </button>
          ))}
        </div>
      </div>

      {job ? (
        <div
          className={`wiki-faq-sync__panel${job.status === "error" ? " wiki-faq-sync__panel--error" : ""}`}
        >
          <div className="wiki-faq-sync__header">
            <strong>{job.label}</strong>
            <span className="meta">
              {job.status === "running"
                ? job.total > 0
                  ? `${job.done}/${job.total}`
                  : "running…"
                : job.status === "ok"
                  ? `done · ${job.elapsed ?? "?"}s`
                  : `failed · exit ${job.exitCode ?? "?"}`}
            </span>
          </div>
          {job.total > 0 ? (
            <progress
              className="wiki-faq-sync__progress"
              max={job.total}
              value={job.done}
            />
          ) : (
            <progress className="wiki-faq-sync__progress" max={1} value={job.status === "running" ? 0 : 1} />
          )}
          {job.summary ? <p className="meta">{job.summary}</p> : null}
          {job.error ? <p className="error-banner">{job.error}</p> : null}
          {job.log.length > 0 ? (
            <pre className="wiki-faq-sync__log">{job.log.join("\n")}</pre>
          ) : null}
        </div>
      ) : null}

      <ul className="meta" style={{ marginTop: "1rem" }}>
        {items.map((it) => (
          <li key={it.key}>
            <code>
              uv run python {it.script}
              {it.args?.length ? ` ${it.args.join(" ")}` : ""}
            </code>
            {" — "}
            {it.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
