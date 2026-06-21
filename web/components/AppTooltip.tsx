"use client";

import { Tooltip } from "react-tooltip";
import "react-tooltip/dist/react-tooltip.css";

export const APP_TIP_ID = "app-tip";

export type TipProps = {
  "data-tooltip-id": string;
  "data-tooltip-content": string;
  "data-tooltip-place"?: "top" | "right" | "bottom" | "left";
};

export function tip(content: string, place?: TipProps["data-tooltip-place"]): TipProps {
  return {
    "data-tooltip-id": APP_TIP_ID,
    "data-tooltip-content": content,
    ...(place ? { "data-tooltip-place": place } : {}),
  };
}

export function AppTooltipHost() {
  return (
    <Tooltip
      id={APP_TIP_ID}
      delayShow={250}
      delayHide={50}
      className="!z-50 !rounded-md !border !border-wos-border-subtle !bg-wos-panel-raised !px-2 !py-1 !text-xs !text-wos-text !shadow-lg"
      noArrow={false}
    />
  );
}
