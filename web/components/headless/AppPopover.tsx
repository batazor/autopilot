"use client";

import { Popover, PopoverButton, PopoverPanel } from "@headlessui/react";
import type { ReactNode } from "react";

export type AppPopoverAnchor =
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

export type AppPopoverProps = {
  trigger: ReactNode;
  children: ReactNode | ((api: { close: () => void }) => ReactNode);
  anchor?: AppPopoverAnchor;
  buttonClassName?: string;
  panelClassName?: string;
  buttonTitle?: string;
  ariaLabel?: string;
  disabled?: boolean;
};

export function AppPopover({
  trigger,
  children,
  anchor = "bottom end",
  buttonClassName = "headless-popover__button",
  panelClassName = "headless-popover__panel",
  buttonTitle,
  ariaLabel,
  disabled = false,
}: AppPopoverProps) {
  return (
    <Popover className="headless-popover">
      <PopoverButton
        className={buttonClassName}
        title={buttonTitle}
        aria-label={ariaLabel}
        disabled={disabled}
      >
        {trigger}
      </PopoverButton>
      <PopoverPanel anchor={anchor} transition className={panelClassName}>
        {(bag) => (
          <>
            {typeof children === "function"
              ? children({ close: bag.close })
              : children}
          </>
        )}
      </PopoverPanel>
    </Popover>
  );
}
