"use client";

import { useEffect, useRef, useState } from "react";
import { Pill, type PillTone } from "@/components/ui";
import type { RoleOption } from "@/lib/farm/types";

/** Role → pill colour: farm = economy/green, fighter = combat/red, else neutral. */
const TONE_BY_ROLE: Record<string, PillTone> = {
  balanced: "neutral",
  farm: "ok",
  fighter: "danger",
};

/**
 * Clickable per-character role badge. Shows the current planner profile as a
 * coloured pill; clicking opens a menu to switch it ({@link onChange}).
 */
export function RoleBadge({
  role,
  roles,
  disabled,
  onChange,
}: {
  role: string;
  roles: RoleOption[];
  disabled?: boolean;
  onChange: (roleId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const current = roles.find((r) => r.id === role);
  const label = current?.label ?? role;
  const tone = TONE_BY_ROLE[role] ?? "neutral";

  return (
    <div ref={ref} className="relative inline-flex">
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        title={current?.description ?? "Change profile"}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center transition-opacity hover:opacity-80 disabled:opacity-50"
      >
        <Pill tone={tone} dot>
          <span className="flex items-center gap-1">
            {label}
            <span aria-hidden className="text-[0.7em] opacity-70">
              ▾
            </span>
          </span>
        </Pill>
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute left-0 top-full z-20 mt-1 min-w-[13rem] rounded-md border border-wos-border-subtle bg-wos-panel-raised p-1 shadow-lg"
        >
          {roles.map((r) => {
            const active = r.id === role;
            return (
              <button
                key={r.id}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                onClick={() => {
                  setOpen(false);
                  if (r.id !== role) onChange(r.id);
                }}
                className={`flex w-full flex-col gap-0.5 rounded px-2 py-1.5 text-left hover:bg-wos-surface/60 ${
                  active ? "bg-wos-surface/40" : ""
                }`}
              >
                <span className="flex items-center gap-1.5 text-sm font-medium text-wos-text">
                  <span
                    aria-hidden
                    className={`w-3 text-emerald-300 ${active ? "" : "opacity-0"}`}
                  >
                    ✓
                  </span>
                  {r.label}
                </span>
                <span className="pl-[1.125rem] text-xs text-wos-text-muted">
                  {r.description}
                </span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
