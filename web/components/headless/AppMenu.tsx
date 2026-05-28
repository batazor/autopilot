"use client";

import {
  Menu,
  MenuButton,
  MenuItem,
  MenuItems,
  MenuSeparator,
} from "@headlessui/react";
import Link from "next/link";
import type { ReactNode } from "react";

export type AppMenuAnchor =
  | "top"
  | "top start"
  | "top end"
  | "right"
  | "right start"
  | "right end"
  | "bottom"
  | "bottom start"
  | "bottom end"
  | "left"
  | "left start"
  | "left end";

export type AppMenuItem =
  | {
      kind?: "action";
      label: ReactNode;
      onClick: () => void;
      disabled?: boolean;
      danger?: boolean;
      title?: string;
    }
  | {
      kind: "link";
      label: ReactNode;
      href: string;
      title?: string;
    }
  | { kind: "separator" };

export type AppMenuProps = {
  /** Button content. Defaults to a kebab "⋮" icon. */
  trigger?: ReactNode;
  items: AppMenuItem[];
  anchor?: AppMenuAnchor;
  buttonClassName?: string;
  buttonTitle?: string;
  ariaLabel?: string;
  disabled?: boolean;
};

function KebabIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden>
      <circle cx="8" cy="3" r="1.4" />
      <circle cx="8" cy="8" r="1.4" />
      <circle cx="8" cy="13" r="1.4" />
    </svg>
  );
}

export function AppMenu({
  trigger,
  items,
  anchor = "bottom end",
  buttonClassName = "headless-menu__button",
  buttonTitle,
  ariaLabel,
  disabled = false,
}: AppMenuProps) {
  return (
    <Menu>
      <MenuButton
        className={buttonClassName}
        title={buttonTitle}
        aria-label={ariaLabel ?? "Open menu"}
        disabled={disabled}
      >
        {trigger ?? <KebabIcon />}
      </MenuButton>
      <MenuItems anchor={anchor} transition className="headless-menu__items">
        {items.map((item, idx) => {
          if (item.kind === "separator") {
            return (
              <MenuSeparator
                key={`sep-${idx}`}
                className="headless-menu__separator"
              />
            );
          }
          if (item.kind === "link") {
            return (
              <MenuItem key={idx}>
                <Link
                  href={item.href}
                  className="headless-menu__item"
                  title={item.title}
                >
                  {item.label}
                </Link>
              </MenuItem>
            );
          }
          const dangerClass = item.danger ? " headless-menu__item--danger" : "";
          return (
            <MenuItem key={idx} disabled={item.disabled}>
              <button
                type="button"
                onClick={item.onClick}
                className={`headless-menu__item${dangerClass}`}
                title={item.title}
                disabled={item.disabled}
              >
                {item.label}
              </button>
            </MenuItem>
          );
        })}
      </MenuItems>
    </Menu>
  );
}
