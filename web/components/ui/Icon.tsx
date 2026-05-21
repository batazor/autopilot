import type { ReactNode, SVGProps } from "react";

export type IconSize = "sm" | "md" | "lg";

export type IconName =
  | "menu"
  | "close"
  | "search"
  | "clear"
  | "recent"
  | "check"
  | "info"
  | "dot"
  | "overview"
  | "instance"
  | "player-state"
  | "player-stats"
  | "approvals"
  | "overlay-test"
  | "queue"
  | "debug-run"
  | "routes"
  | "optimizer"
  | "gift-codes"
  | "wiki"
  | "labeling"
  | "edit-dsl"
  | "analyze"
  | "modules"
  | "adb"
  | "balance"
  | "operate"
  | "debug"
  | "assets"
  | "config"
  | "inbox-empty"
  | "list-empty"
  | "warning"
  | "alert";

const SIZE_CLASS: Record<IconSize, string> = {
  sm: "ui-icon--sm",
  md: "ui-icon--md",
  lg: "ui-icon--lg",
};

type IconProps = {
  name: IconName;
  size?: IconSize;
  className?: string;
} & SVGProps<SVGSVGElement>;

function strokeIcon(children: ReactNode) {
  return children;
}

const PATHS: Record<IconName, ReactNode> = {
  menu: strokeIcon(
    <>
      <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
    </>,
  ),
  close: strokeIcon(
    <>
      <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
    </>,
  ),
  search: strokeIcon(
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M16 16l4 4" strokeLinecap="round" />
    </>,
  ),
  clear: strokeIcon(
    <>
      <path d="M8 8l8 8M16 8l-8 8" strokeLinecap="round" />
    </>,
  ),
  recent: strokeIcon(
    <>
      <path d="M12 8v4l3 2" strokeLinecap="round" strokeLinejoin="round" />
      <path
        d="M12 20a8 8 0 1 0-6.93-4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>,
  ),
  check: strokeIcon(
    <path d="M6 12l4 4 8-9" strokeLinecap="round" strokeLinejoin="round" />,
  ),
  info: strokeIcon(
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 10v6M12 7h.01" strokeLinecap="round" />
    </>,
  ),
  dot: <circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />,
  overview: strokeIcon(
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </>,
  ),
  instance: strokeIcon(
    <>
      <rect x="5" y="6" width="14" height="12" rx="2" />
      <path d="M9 18h6" strokeLinecap="round" />
    </>,
  ),
  "player-state": strokeIcon(
    <>
      <circle cx="12" cy="9" r="3.5" />
      <path d="M6 19c1.2-2.5 3.2-3.8 6-3.8s4.8 1.3 6 3.8" strokeLinecap="round" />
    </>,
  ),
  "player-stats": strokeIcon(
    <>
      <path d="M4 18V8l4 3 4-6 4 8 4-5v10" strokeLinecap="round" strokeLinejoin="round" />
    </>,
  ),
  approvals: strokeIcon(
    <>
      <path d="M12 3l7 3v6c0 4.2-2.8 7.4-7 9-4.2-1.6-7-4.8-7-9V6z" strokeLinejoin="round" />
      <path d="M9 12l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </>,
  ),
  "overlay-test": strokeIcon(
    <>
      <path d="M4 8l8-4 8 4-8 4-8-4z" strokeLinejoin="round" />
      <path d="M4 16l8 4 8-4" strokeLinejoin="round" />
    </>,
  ),
  queue: strokeIcon(
    <>
      <path d="M7 7h14M7 12h14M7 17h14" strokeLinecap="round" />
      <path d="M4 7h.01M4 12h.01M4 17h.01" strokeLinecap="round" />
    </>,
  ),
  "debug-run": strokeIcon(
    <path d="M9 7l8 5-8 5V7z" strokeLinejoin="round" />,
  ),
  routes: strokeIcon(
    <>
      <circle cx="6" cy="18" r="2" />
      <circle cx="18" cy="6" r="2" />
      <path d="M8 16c4-1 5-4 8-8" strokeLinecap="round" />
    </>,
  ),
  optimizer: strokeIcon(
    <path d="M13 3L5 14h6l-1 7 9-13h-6l1-5z" strokeLinejoin="round" />,
  ),
  "gift-codes": strokeIcon(
    <>
      <rect x="4" y="8" width="16" height="12" rx="2" />
      <path d="M12 8V20M4 12h16" />
      <path d="M8 8c0-2 1.5-3 4-3s4 1 4 3" />
    </>,
  ),
  wiki: strokeIcon(
    <>
      <path d="M6 5h12v14H6z" />
      <path d="M9 9h6M9 13h6M9 17h4" strokeLinecap="round" />
    </>,
  ),
  labeling: strokeIcon(
    <>
      <path d="M6 6h12v12H6z" />
      <path d="M9 15l6-6" strokeLinecap="round" />
      <circle cx="9" cy="9" r="1" fill="currentColor" stroke="none" />
    </>,
  ),
  "edit-dsl": strokeIcon(
    <>
      <path d="M8 6h10v14H8z" />
      <path d="M6 9H4M6 13H4M6 17H4" strokeLinecap="round" />
    </>,
  ),
  analyze: strokeIcon(
    <>
      <circle cx="11" cy="11" r="5" />
      <path d="M15 15l4 4" strokeLinecap="round" />
    </>,
  ),
  modules: strokeIcon(
    <>
      <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z" strokeLinejoin="round" />
    </>,
  ),
  adb: strokeIcon(
    <>
      <rect x="5" y="6" width="14" height="12" rx="2" />
      <path d="M8 10h8M8 14h5" strokeLinecap="round" />
    </>,
  ),
  balance: strokeIcon(
    <>
      <path d="M12 5v14" />
      <path d="M6 9h12M8 15h8" strokeLinecap="round" />
    </>,
  ),
  operate: strokeIcon(
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="3" />
    </>,
  ),
  debug: strokeIcon(
    <>
      <path d="M9 4l-1 3H5l3 8H8l-1 3 3-1 1 3 3-1 1-3 3 1-1-3 3-1-3-8h2l1-3H9z" strokeLinejoin="round" />
    </>,
  ),
  assets: strokeIcon(
    <>
      <path d="M5 7h14v12H5z" />
      <path d="M8 7V5h8v2" strokeLinecap="round" />
    </>,
  ),
  config: strokeIcon(
    <>
      <circle cx="12" cy="12" r="3" />
      <path
        d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4l1.4-1.4M17 7l1.4-1.4"
        strokeLinecap="round"
      />
    </>,
  ),
  "inbox-empty": strokeIcon(
    <>
      <path d="M5 8h14l-2 10H7L5 8z" />
      <path d="M9 8V6h6v2" strokeLinecap="round" />
    </>,
  ),
  "list-empty": strokeIcon(
    <>
      <path d="M8 7h12M8 12h12M8 17h8" strokeLinecap="round" />
      <path d="M5 7h.01M5 12h.01M5 17h.01" strokeLinecap="round" />
    </>,
  ),
  warning: strokeIcon(
    <>
      <path d="M12 8v5M12 16h.01" strokeLinecap="round" />
      <path d="M12 4l8 14H4l8-14z" strokeLinejoin="round" />
    </>,
  ),
  alert: strokeIcon(
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4M12 16h.01" strokeLinecap="round" />
    </>,
  ),
};

export function Icon({ name, size = "md", className = "", ...rest }: IconProps) {
  return (
    <svg
      className={["ui-icon", SIZE_CLASS[size], className].filter(Boolean).join(" ")}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={rest["aria-label"] ? undefined : true}
      {...rest}
    >
      {PATHS[name]}
    </svg>
  );
}
