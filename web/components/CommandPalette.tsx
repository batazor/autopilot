"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "@/components/ThemeProvider";
import { NAV_PINNED } from "@/lib/nav";
import { NAV_GROUPS } from "@/lib/nav-groups";

type Command = {
  id: string;
  label: string;
  hint?: string;
  group: string;
  run: () => void;
};

export function CommandPalette({
  open,
  onClose,
  onToggleSidebar,
}: {
  open: boolean;
  onClose: () => void;
  onToggleSidebar?: () => void;
}) {
  const router = useRouter();
  const { theme, toggleTheme } = useTheme();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const commands = useMemo<Command[]>(() => {
    const go = (href: string) => () => {
      router.push(href);
      onClose();
    };
    const seen = new Set<string>();
    const nav: Command[] = [];
    for (const p of NAV_PINNED) {
      if (seen.has(p.href)) continue;
      seen.add(p.href);
      nav.push({
        id: `nav:${p.href}`,
        label: p.label,
        hint: p.description ?? "Quick access",
        group: "Go to",
        run: go(p.href),
      });
    }
    for (const grp of NAV_GROUPS) {
      for (const tab of grp.tabs) {
        if (seen.has(tab.href)) continue;
        seen.add(tab.href);
        nav.push({
          id: `nav:${tab.href}`,
          label: tab.label,
          hint: grp.label,
          group: "Go to",
          run: go(tab.href),
        });
      }
    }
    const actions: Command[] = [
      {
        id: "action:theme",
        label: `Switch to ${theme === "dark" ? "light" : "dark"} theme`,
        group: "Actions",
        run: () => {
          toggleTheme();
          onClose();
        },
      },
    ];
    if (onToggleSidebar) {
      actions.push({
        id: "action:sidebar",
        label: "Toggle sidebar",
        hint: "Collapse / expand the left menu",
        group: "Actions",
        run: () => {
          onToggleSidebar();
          onClose();
        },
      });
    }
    return [...nav, ...actions];
  }, [router, onClose, onToggleSidebar, theme, toggleTheme]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) =>
      `${c.label} ${c.hint ?? ""} ${c.group}`.toLowerCase().includes(q),
    );
  }, [commands, query]);

  // Reset transient UI whenever the palette opens.
  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      // Focus after the dialog paints.
      const id = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
  }, [open]);

  // Keep the highlighted row in range as the filter narrows.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  // Scroll the active row into view.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(
      `[data-idx="${active}"]`,
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  if (!open) return null;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (filtered.length ? (a + 1) % filtered.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) =>
        filtered.length ? (a - 1 + filtered.length) % filtered.length : 0,
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      filtered[active]?.run();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-[12vh]"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        style={{ backgroundColor: "var(--wos-overlay-scrim)" }}
        aria-label="Close command palette"
        onClick={onClose}
        tabIndex={-1}
      />
      <div
        className="relative w-full max-w-xl overflow-hidden rounded-xl border border-wos-border-subtle bg-wos-surface shadow-2xl shadow-black/40"
        onKeyDown={onKeyDown}
      >
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search pages and actions…"
          className="w-full border-0 border-b border-wos-border-subtle bg-transparent px-4 py-3 text-sm text-wos-text placeholder:text-wos-text-muted focus:outline-none focus:ring-0"
          aria-label="Search pages and actions"
          aria-controls="command-palette-list"
          autoComplete="off"
          spellCheck={false}
        />
        <ul
          ref={listRef}
          id="command-palette-list"
          role="listbox"
          className="max-h-[50vh] overflow-y-auto py-1"
        >
          {filtered.length === 0 ? (
            <li className="px-4 py-6 text-center text-sm text-wos-text-muted">
              No matches
            </li>
          ) : (
            filtered.map((c, i) => (
              <li key={c.id} data-idx={i} role="option" aria-selected={i === active}>
                <button
                  type="button"
                  className={[
                    "flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm",
                    i === active
                      ? "bg-wos-panel-raised text-wos-text"
                      : "text-wos-text-secondary hover:bg-wos-panel-raised/60",
                  ].join(" ")}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => c.run()}
                >
                  <span className="min-w-0 flex-1 truncate font-medium text-wos-text">
                    {c.label}
                  </span>
                  {c.hint ? (
                    <span className="shrink-0 truncate text-xs text-wos-text-muted">
                      {c.hint}
                    </span>
                  ) : null}
                </button>
              </li>
            ))
          )}
        </ul>
        <div className="flex items-center gap-3 border-t border-wos-border-subtle px-4 py-2 text-[11px] text-wos-text-muted">
          <span>↑↓ navigate</span>
          <span>↵ open</span>
          <span>esc close</span>
        </div>
      </div>
    </div>
  );
}
