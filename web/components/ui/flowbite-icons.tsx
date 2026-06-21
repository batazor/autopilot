// Standalone Flowbite icon set (kept separate from components/ui/Icon.tsx).
//
// Each glyph is the raw Flowbite SVG, preserving its own stroke caps. All are
// on the 24×24 stroke grid, inherit `currentColor`, and size via `className`
// (default h-6 w-6 — pass e.g. "h-5 w-5" to override). Grow this file as more
// of the pack is needed.
import type { ReactNode, SVGProps } from "react";

export type FlowbiteIconProps = SVGProps<SVGSVGElement> & { className?: string };

function StrokeIcon({
  className = "h-6 w-6",
  children,
  ...rest
}: FlowbiteIconProps & { children: ReactNode }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      width="24"
      height="24"
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden={rest["aria-label"] ? undefined : true}
      {...rest}
    >
      {children}
    </svg>
  );
}

/** Share-nodes — three connected nodes. */
export function ShareNodesIcon(props: FlowbiteIconProps) {
  return (
    <StrokeIcon {...props}>
      <path
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="2"
        d="M7.926 10.898 15 7.727m-7.074 5.39L15 16.29M8 12a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Zm12 5.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Zm0-11a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z"
      />
    </StrokeIcon>
  );
}

/** Printer. */
export function PrinterIcon(props: FlowbiteIconProps) {
  return (
    <StrokeIcon {...props}>
      <path
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M16.444 18H19a1 1 0 0 0 1-1v-5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1h2.556M17 11V5a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v6h10ZM7 15h10v4a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1v-4Z"
      />
    </StrokeIcon>
  );
}

/** Download — tray with down arrow. */
export function DownloadIcon(props: FlowbiteIconProps) {
  return (
    <StrokeIcon {...props}>
      <path
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M12 13V4M7 14H5a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4a1 1 0 0 0-1-1h-2m-1-5-4 5-4-5m9 8h.01"
      />
    </StrokeIcon>
  );
}

/** Copy / duplicate document. */
export function CopyIcon(props: FlowbiteIconProps) {
  return (
    <StrokeIcon {...props}>
      <path
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M14 4v3a1 1 0 0 1-1 1h-3m4 10v1a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1h2m11-3v10a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1V7.87a1 1 0 0 1 .24-.65l2.46-2.87a1 1 0 0 1 .76-.35H18a1 1 0 0 1 1 1Z"
      />
    </StrokeIcon>
  );
}

/** Plus — used as the speed-dial toggle (rotate 45° → ✕). */
export function PlusIcon(props: FlowbiteIconProps) {
  return (
    <StrokeIcon {...props}>
      <path
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M5 12h14m-7 7V5"
      />
    </StrokeIcon>
  );
}
