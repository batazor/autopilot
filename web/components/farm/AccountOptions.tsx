"use client";

import { useCallback, useEffect, useState } from "react";
import { Spinner, Toggle } from "@/components/ui";

/**
 * Per-character options panel. Renders whatever the backend registry exposes
 * (`GET .../options`) — bool toggles and enum pickers — so adding an option is a
 * one-line registry change with no UI edit here.
 */

type Choice = { value: string; label: string };

type AccountOption = {
  key: string;
  label: string;
  description: string;
  type: "bool" | "enum";
  group: string;
  choices: Choice[];
  value: unknown;
};

export function AccountOptions({
  username,
  fid,
  disabled,
}: {
  username: string;
  fid: string;
  disabled?: boolean;
}) {
  const [options, setOptions] = useState<AccountOption[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const base = `/api/farm/accounts/${username}/characters/${encodeURIComponent(
    fid,
  )}/options`;

  useEffect(() => {
    let cancelled = false;
    setOptions(null);
    setLoadError(null);
    fetch(base)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => {
        if (!cancelled) setOptions(d.options ?? []);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(String(e?.message ?? e));
      });
    return () => {
      cancelled = true;
    };
  }, [base, reloadKey]);

  const setValue = useCallback(
    async (opt: AccountOption, value: unknown) => {
      setBusyKey(opt.key);
      setSaveError(null);
      // Optimistic; roll back on failure.
      setOptions((prev) =>
        prev?.map((o) => (o.key === opt.key ? { ...o, value } : o)) ?? prev,
      );
      try {
        const res = await fetch(base, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: opt.key, value }),
        });
        if (!res.ok) {
          throw new Error(`Couldn't save (HTTP ${res.status})`);
        }
        const d = await res.json();
        setOptions((prev) =>
          prev?.map((o) => (o.key === opt.key ? { ...o, value: d.value } : o)) ?? prev,
        );
      } catch (e) {
        setOptions((prev) =>
          prev?.map((o) => (o.key === opt.key ? { ...o, value: opt.value } : o)) ?? prev,
        );
        setSaveError(String((e as Error)?.message ?? e));
      } finally {
        setBusyKey(null);
      }
    },
    [base],
  );

  if (loadError)
    return (
      <div role="alert" className="flex items-center gap-2 text-xs text-rose-300">
        <span>Options unavailable: {loadError}</span>
        <button
          type="button"
          onClick={() => setReloadKey((k) => k + 1)}
          className="rounded border border-rose-300/40 px-1.5 py-0.5 font-medium text-rose-200 transition-colors hover:bg-rose-300/10"
        >
          Retry
        </button>
      </div>
    );
  if (!options)
    return (
      <p role="status" aria-busy="true" className="text-xs text-wos-text-muted">
        Loading options…
      </p>
    );
  if (options.length === 0) return null;

  const groups = [...new Set(options.map((o) => o.group))];

  return (
    <div className="flex flex-col gap-3">
      {groups.map((group) => {
        const headingId = `optgroup-${fid}-${group.toLowerCase().replace(/\s+/g, "-")}`;
        return (
          <div
            key={group}
            role="group"
            aria-labelledby={headingId}
            className="flex flex-col gap-2"
          >
            <div
              id={headingId}
              className="text-[0.7rem] font-semibold uppercase tracking-wide text-wos-text-muted"
            >
              {group}
            </div>
            {options
              .filter((o) => o.group === group)
              .map((opt) => (
                <OptionRow
                  key={opt.key}
                  opt={opt}
                  saving={busyKey === opt.key}
                  disabled={disabled}
                  onChange={(v) => setValue(opt, v)}
                />
              ))}
          </div>
        );
      })}
      {saveError ? (
        <p role="alert" className="text-xs text-rose-300">
          {saveError}
        </p>
      ) : null}
    </div>
  );
}

function OptionRow({
  opt,
  saving,
  disabled,
  onChange,
}: {
  opt: AccountOption;
  saving?: boolean;
  disabled?: boolean;
  onChange: (value: unknown) => void;
}) {
  const off = disabled || saving;
  const id = `opt-${opt.key}`;

  const control =
    opt.type === "bool" ? (
      <Toggle
        id={id}
        checked={Boolean(opt.value)}
        onChange={onChange}
        disabled={off}
        aria-label={opt.label}
      />
    ) : (
      <select
        id={id}
        value={String(opt.value)}
        disabled={off}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-wos-border-subtle bg-wos-surface px-2 py-1 text-xs text-wos-text disabled:opacity-50"
      >
        {opt.choices.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
    );

  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 flex-col">
        <label
          htmlFor={id}
          className={`flex items-center gap-1.5 text-sm font-medium text-wos-text ${
            off ? "" : "cursor-pointer"
          }`}
        >
          {opt.label}
        </label>
        <span className="text-xs text-wos-text-muted">{opt.description}</span>
      </div>
      <div className="flex shrink-0 items-center gap-2 pt-0.5">
        {saving ? <Spinner size="sm" label="Saving" /> : null}
        {control}
      </div>
    </div>
  );
}
