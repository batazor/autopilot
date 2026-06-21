"use client";

import { Tab, TabGroup, TabList, TabPanel, TabPanels } from "@headlessui/react";
import type { ReactNode } from "react";

export type AppTabItem = {
  key: string;
  label: ReactNode;
  panel?: ReactNode;
  disabled?: boolean;
  title?: string;
};

export type AppTabsProps = {
  tabs: AppTabItem[];
  selectedKey: string;
  onChange: (key: string) => void;
  /** Section nav (wiki-style) vs toolbar pill buttons */
  variant?: "toolbar" | "section";
  className?: string;
  listClassName?: string;
  /** Rendered after the tab list (e.g. scope filter, action buttons) */
  afterTabs?: ReactNode;
  /** When false, only the tab list is rendered */
  renderPanels?: boolean;
};

function tabIndex(tabs: AppTabItem[], selectedKey: string): number {
  const i = tabs.findIndex((t) => t.key === selectedKey);
  return i >= 0 ? i : 0;
}

export function AppTabs({
  tabs,
  selectedKey,
  onChange,
  variant = "toolbar",
  className = "",
  listClassName = "",
  afterTabs,
  renderPanels = true,
}: AppTabsProps) {
  const selectedIndex = tabIndex(tabs, selectedKey);
  const hasPanels =
    renderPanels && tabs.some((t) => t.panel !== undefined && t.panel !== null);

  const listVariant =
    variant === "section"
      ? "headless-tab-list headless-tab-list--section section-tabs"
      : "headless-tab-list headless-tab-list--toolbar";

  return (
    <TabGroup
      selectedIndex={selectedIndex}
      onChange={(index) => {
        const item = tabs[index];
        if (item) onChange(item.key);
      }}
      className={className}
    >
      <div
        className={
          variant === "toolbar"
            ? `toolbar headless-tabs-toolbar${listClassName ? ` ${listClassName}` : ""}`
            : listClassName
        }
      >
        <TabList className={`${listVariant}`.trim()}>
          {tabs.map((t) => (
            <Tab
              key={t.key}
              disabled={t.disabled}
              className={
                variant === "section" ? "headless-tab section-tab" : "headless-tab"
              }
              title={t.title}
            >
              {t.label}
            </Tab>
          ))}
        </TabList>
        {afterTabs}
      </div>
      {hasPanels ? (
        <TabPanels>
          {tabs.map((t) => (
            <TabPanel key={t.key} className="headless-tab-panel">
              {t.panel}
            </TabPanel>
          ))}
        </TabPanels>
      ) : null}
    </TabGroup>
  );
}
