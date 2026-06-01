"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ComponentType } from "react";
import {
  CopyIcon,
  DownloadIcon,
  type FlowbiteIconProps,
  PlusIcon,
  PrinterIcon,
  ShareNodesIcon,
} from "@/components/ui/flowbite-icons";

type IconComponent = ComponentType<FlowbiteIconProps>;

export type QuickAction = {
  key: string;
  label: string;
  icon: IconComponent;
  /** Click handler. Ignored when `href` is set. */
  onClick?: () => void;
  /** Render as a link instead of a button. */
  href?: string;
};

/**
 * Best-effort defaults so <QuickMenu /> is usable with no props. Share uses the
 * Web Share API (clipboard fallback); Copy/Print are wired; Download is a stub
 * meant to be overridden by passing your own `actions`.
 */
export const DEFAULT_QUICK_ACTIONS: QuickAction[] = [
  {
    key: "share",
    label: "Share",
    icon: ShareNodesIcon,
    onClick: () => {
      const url = window.location.href;
      if (navigator.share) void navigator.share({ url }).catch(() => undefined);
      else void navigator.clipboard?.writeText(url).catch(() => undefined);
    },
  },
  { key: "print", label: "Print", icon: PrinterIcon, onClick: () => window.print() },
  {
    key: "download",
    label: "Download",
    icon: DownloadIcon,
    onClick: () =>
      window.dispatchEvent(new CustomEvent("quickmenu:download")),
  },
  {
    key: "copy",
    label: "Copy link",
    icon: CopyIcon,
    onClick: () =>
      void navigator.clipboard?.writeText(window.location.href).catch(() => undefined),
  },
];

type QuickMenuProps = {
  actions?: QuickAction[];
  /** Accessible label for the toggle. */
  toggleLabel?: string;
  className?: string;
};

const ITEM_BTN =
  "flex h-[52px] w-[52px] items-center justify-center rounded-full border " +
  "border-wos-border bg-wos-surface text-wos-text-muted shadow-sm transition " +
  "hover:bg-wos-panel-raised hover:text-wos-text " +
  "focus:outline-none focus:ring-4 focus:ring-accent/30";

export function QuickMenu({
  actions = DEFAULT_QUICK_ACTIONS,
  toggleLabel = "Open actions menu",
  className,
}: QuickMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  // Close on Escape or click/tap outside (covers click-to-open on touch).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const runAction = useCallback((action: QuickAction) => {
    action.onClick?.();
    setOpen(false);
  }, []);

  return (
    <div
      ref={rootRef}
      className={`fixed end-6 bottom-6 z-40 ${className ?? ""}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <div
        id={menuId}
        className={`mb-4 flex-col items-center gap-2 ${open ? "flex" : "hidden"}`}
      >
        {actions.map((action) => {
          const Icon = action.icon;
          const inner = (
            <>
              <Icon className="h-5 w-5" />
              <span className="sr-only">{action.label}</span>
            </>
          );
          return (
            <div key={action.key} className="group/qm relative flex items-center">
              {action.href ? (
                <a className={ITEM_BTN} href={action.href} onClick={() => setOpen(false)}>
                  {inner}
                </a>
              ) : (
                <button type="button" className={ITEM_BTN} onClick={() => runAction(action)}>
                  {inner}
                </button>
              )}
              <span
                role="tooltip"
                className="pointer-events-none absolute right-full top-1/2 mr-3
                  -translate-y-1/2 whitespace-nowrap rounded-md border border-wos-border
                  bg-wos-panel-raised px-3 py-2 text-sm font-medium text-wos-text shadow-md
                  opacity-0 transition-opacity group-hover/qm:opacity-100"
              >
                {action.label}
              </span>
            </div>
          );
        })}
      </div>

      <button
        type="button"
        aria-controls={menuId}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex h-14 w-14 items-center justify-center rounded-full bg-accent
          text-white shadow-lg transition hover:bg-accent/90
          focus:outline-none focus:ring-4 focus:ring-accent/40"
      >
        <PlusIcon
          className={`h-5 w-5 transition-transform ${open ? "rotate-45" : ""}`}
        />
        <span className="sr-only">{toggleLabel}</span>
      </button>
    </div>
  );
}
