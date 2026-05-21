export type NavGroupId = "operate" | "debug" | "assets" | "config";

export type NavTab = {
  href: string;
  label: string;
  description?: string;
};

export type NavGroup = {
  id: NavGroupId;
  label: string;
  description?: string;
  defaultHref: string;
  tabs: NavTab[];
};

export const NAV_GROUPS: NavGroup[] = [
  {
    id: "operate",
    label: "Operate",
    description: "Fleet overview & instance control",
    defaultHref: "/overview",
    tabs: [
      { href: "/overview", label: "Overview" },
      { href: "/instance", label: "Instance" },
      { href: "/player-state", label: "Player state" },
      { href: "/player-stats", label: "Statistics" },
      { href: "/alliance-stats", label: "Alliance stats" },
    ],
  },
  {
    id: "debug",
    label: "Debug",
    description: "Approvals, queue & tooling",
    defaultHref: "/approvals",
    tabs: [
      {
        href: "/approvals",
        label: "Click approvals",
        description: "Live approve / reject",
      },
      {
        href: "/overlay-test",
        label: "Overlay test",
        description: "Rule match debugger",
      },
      {
        href: "/queue",
        label: "Queue",
        description: "Fleet task queue",
      },
      { href: "/debug-run", label: "DSL runner" },
      { href: "/routes", label: "Routes" },
      { href: "/optimizer", label: "Optimizer" },
    ],
  },
  {
    id: "assets",
    label: "Assets",
    description: "Wiki, labeling & reference",
    defaultHref: "/labeling",
    tabs: [
      { href: "/labeling", label: "Labeling" },
      { href: "/edit-dsl", label: "DSL editor" },
      { href: "/analyze", label: "Analyze" },
      { href: "/wiki", label: "Wiki reference" },
      { href: "/gift-codes", label: "Gift codes" },
    ],
  },
  {
    id: "config",
    label: "Config",
    description: "Modules, ADB & balance",
    defaultHref: "/modules",
    tabs: [
      { href: "/modules", label: "Modules" },
      { href: "/adb", label: "ADB" },
      { href: "/balance", label: "Balance" },
    ],
  },
];

export function allNavTabs(): NavTab[] {
  return NAV_GROUPS.flatMap((g) => g.tabs);
}

export function groupForPath(pathname: string): NavGroup | undefined {
  return NAV_GROUPS.find((g) =>
    g.tabs.some(
      (t) => pathname === t.href || pathname.startsWith(`${t.href}/`),
    ),
  );
}

export function labelForHref(href: string): string {
  const tab = allNavTabs().find((t) => t.href === href);
  return tab?.label ?? href;
}
