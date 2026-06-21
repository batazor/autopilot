import type { ReactNode, SVGProps } from "react";
import {
  Archive,
  ArrowDown,
  ArrowUp,
  Bell,
  BookOpen,
  Boxes,
  Bug,
  CalendarDays,
  ChartNoAxesCombined,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleDot,
  CircleX,
  Copy,
  Cpu,
  FileCode2,
  Gamepad2,
  Gift,
  GitBranch,
  History,
  Images,
  Inbox,
  Info,
  Layers,
  LayoutGrid,
  List,
  ListX,
  Menu,
  Monitor,
  Pause,
  Play,
  Plus,
  Radar,
  RefreshCw,
  Route,
  Scale,
  Search,
  Settings,
  ShieldCheck,
  Smartphone,
  Square,
  Tag,
  Trash2,
  TriangleAlert,
  User,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";

export type IconSize = "sm" | "md" | "lg";

export type IconName =
  | "menu"
  | "close"
  | "search"
  | "clear"
  | "recent"
  | "refresh"
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
  | "routes"
  | "calendar"
  | "optimizer"
  | "gift-codes"
  | "dreamscape-memory"
  | "notify-monitor"
  | "radar"
  | "trees"
  | "wiki"
  | "labeling"
  | "gallery"
  | "edit-dsl"
  | "modules"
  | "adb"
  | "balance"
  | "operate"
  | "games"
  | "debug"
  | "assets"
  | "config"
  | "inbox-empty"
  | "list-empty"
  | "warning"
  | "alert"
  | "arrow-up"
  | "arrow-down"
  | "plus"
  | "chevron-right"
  | "chevron-left"
  | "copy"
  | "trash"
  | "play"
  | "pause"
  | "stop"
  | "discord";

const SIZE_CLASS: Record<IconSize, string> = {
  sm: "ui-icon--sm",
  md: "ui-icon--md",
  lg: "ui-icon--lg",
};

type IconProps = {
  name: IconName;
  size?: IconSize;
  className?: string;
} & Omit<SVGProps<SVGSVGElement>, "name" | "size">;

const LUCIDE_ICONS: Partial<Record<IconName, LucideIcon>> = {
  menu: Menu,
  close: X,
  search: Search,
  clear: CircleX,
  recent: History,
  refresh: RefreshCw,
  check: Check,
  info: Info,
  overview: LayoutGrid,
  instance: Monitor,
  "player-state": User,
  "player-stats": ChartNoAxesCombined,
  approvals: ShieldCheck,
  "overlay-test": Layers,
  queue: List,
  routes: Route,
  calendar: CalendarDays,
  optimizer: Zap,
  "gift-codes": Gift,
  "dreamscape-memory": Cpu,
  "notify-monitor": Bell,
  radar: Radar,
  trees: GitBranch,
  wiki: BookOpen,
  labeling: Tag,
  gallery: Images,
  "edit-dsl": FileCode2,
  modules: Boxes,
  adb: Smartphone,
  balance: Scale,
  operate: CircleDot,
  games: Gamepad2,
  debug: Bug,
  assets: Archive,
  config: Settings,
  "inbox-empty": Inbox,
  "list-empty": ListX,
  warning: TriangleAlert,
  alert: CircleAlert,
  "arrow-up": ArrowUp,
  "arrow-down": ArrowDown,
  plus: Plus,
  "chevron-right": ChevronRight,
  "chevron-left": ChevronLeft,
  copy: Copy,
  trash: Trash2,
  play: Play,
  pause: Pause,
  stop: Square,
};

const CUSTOM_PATHS: Partial<Record<IconName, ReactNode>> = {
  dot: <circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />,
  // Discord is a brand-like app action in this UI, so keep it as a local
  // monochrome glyph instead of pulling a second icon family.
  discord: (
    <>
      <path
        d="M7 6.5C9 5.7 11 5.5 12 5.5s3 .2 5 1L18.5 7c1.5 2 2.2 4.5 2 7-1.4 1.1-2.8 1.8-4.2 2.2l-.8-1.4c.7-.3 1.4-.7 2-1.2-2.6 1.4-5.4 1.4-8 0 .6.5 1.3.9 2 1.2l-.8 1.4c-1.4-.4-2.8-1.1-4.2-2.2-.2-2.5.5-5 2-7L7 6.5z"
        strokeLinejoin="round"
      />
      <circle cx="9.5" cy="12" r="1" />
      <circle cx="14.5" cy="12" r="1" />
    </>
  ),
};

export function Icon({ name, size = "md", className = "", ...rest }: IconProps) {
  const classes = ["ui-icon", SIZE_CLASS[size], className].filter(Boolean).join(" ");
  const ariaHidden = rest["aria-label"] ? undefined : true;
  const LucideIconComponent = LUCIDE_ICONS[name];

  if (LucideIconComponent) {
    return (
      <LucideIconComponent
        className={classes}
        aria-hidden={ariaHidden}
        strokeWidth={1.75}
        {...rest}
      />
    );
  }

  return (
    <svg
      className={classes}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={ariaHidden}
      {...rest}
    >
      {CUSTOM_PATHS[name]}
    </svg>
  );
}
