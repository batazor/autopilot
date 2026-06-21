// Ambient types for @svar-ui/react-calendar.
//
// Upstream ships `package.json#types` pointing at `./types/index.d.ts`, but
// that directory is missing from the published 2.6.1 tarball — so TypeScript
// falls back to `any`. The bundled JS does export `Calendar`, `Willow`,
// `WillowDark`, theme components, and view models; we only type the subset
// this project actually uses. Re-export shared store types from the
// (correctly-typed) `@svar-ui/calendar-store` package.

declare module "@svar-ui/react-calendar" {
  import type { FC, ReactNode } from "react";
  import type {
    CalendarEvent,
    EventContext,
    StoreActions,
  } from "@svar-ui/calendar-store";

  // Allow `import "@svar-ui/react-calendar/all.css"` for the bundled theme.
  // Side-effect-only imports don't need typed members.

  type ViewName = "day" | "week" | "month" | string;

  // `update-event`, `add-event`, etc. surface as `on<PascalCase>` props.
  // Anything beyond what we use stays open via the index signature.
  export interface CalendarProps {
    events?: CalendarEvent[];
    date?: Date;
    view?: ViewName;
    views?: ViewName[];
    toolbar?: unknown;
    cellCss?: (ctx: EventContext) => string;
    eventCss?: (ctx: EventContext) => string;
    eventContent?: FC<{ event: CalendarEvent }>;
    recurring?: boolean;
    readonly?: boolean;
    children?: ReactNode;
    onUpdateEvent?: (ev: StoreActions["update-event"]) => void;
    onAddEvent?: (ev: StoreActions["add-event"]) => void;
    onDeleteEvent?: (ev: StoreActions["delete-event"]) => void;
    onSelectEvent?: (ev: StoreActions["select-event"]) => void;
    onMoveEvent?: (ev: StoreActions["move-event"]) => void;
    onNavigateTo?: (ev: StoreActions["navigate-to"]) => void;
    onNavigateTime?: (ev: StoreActions["navigate-time"]) => void;
    init?: (api: unknown) => void;
    [prop: string]: unknown;
  }

  export const Calendar: FC<CalendarProps>;
  export const Widget: FC<CalendarProps>;
  export const CalendarPanel: FC<{ children?: ReactNode; [k: string]: unknown }>;
  export const Editor: FC<{ children?: ReactNode; [k: string]: unknown }>;
  export const ContextMenu: FC<{ children?: ReactNode; [k: string]: unknown }>;
  export const Willow: FC<{ fonts?: boolean; children?: ReactNode }>;
  export const WillowDark: FC<{ fonts?: boolean; children?: ReactNode }>;

  export {
    DayViewModel,
    WeekViewModel,
    MonthViewModel,
    RestDataProvider,
    registerCalendarView,
    registerEditorItem,
    getMenuOptions,
    getToolbarItems,
    parseICal,
    serializeICal,
    version,
  } from "@svar-ui/calendar-store";
}
