import { NAV_GROUPS } from "@/lib/nav-groups";

export type NavItem = {
  href: string;
  label: string;
  description?: string;
};

export type NavSection = {
  title: string;
  items: NavItem[];
};

/** Operator shortcuts — also pinned at the top of the sidebar. */
export const NAV_PINNED: Pick<NavItem, "href" | "label" | "description">[] = [
  { href: "/queue", label: "Queue", description: "Pending & running tasks" },
  {
    href: "/labeling",
    label: "Labeling",
    description: "Regions, references & crops",
  },
];

export const NAV_PINNED_HREFS = new Set(NAV_PINNED.map((p) => p.href));

/** @deprecated Use NAV_GROUPS — kept for tests or external imports. */
export const NAV_SECTIONS: NavSection[] = NAV_GROUPS.map((g) => ({
  title: g.label,
  items: g.tabs.map((t) => ({
    href: t.href,
    label: t.label,
    description: t.description,
  })),
}));

export {
  NAV_GROUPS,
  allNavTabs,
  groupForPath,
  labelForHref,
  type NavTab,
  type NavGroup,
  type NavGroupId,
} from "@/lib/nav-groups";
