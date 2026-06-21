"use client";

import { useMemo } from "react";
import { ApprovalCanvas } from "@/components/ApprovalCanvas";
import { AppSelect } from "@/components/AppSelect";
import { Button, MetricCard, MetricGrid } from "@/components/ui";
import { asNumber, clamp01, fmtMaybe } from "@/lib/approvals/format";
import type { AreaRegionProbeResult } from "@/lib/types";
import { RegionProbeCropCompare } from "./ProbeCrops";

export function RegionProbePanel({
  probe,
  imageUrl,
  selectedRegion,
  threshold,
  error,
  onRegionChange,
  onThresholdChange,
  onRefresh,
}: {
  probe: AreaRegionProbeResult | null;
  imageUrl: string | null;
  selectedRegion: string;
  threshold: number | null;
  error: string | null;
  onRegionChange: (region: string) => void;
  onThresholdChange: (threshold: number) => void;
  onRefresh: () => void;
}) {
  const result = probe?.result ?? null;
  const matched = !!result?.matched;
  const action = String(result?.action || "findIcon");
  const isRedDotProbe = action === "red_dot";
  const redDotPresent = result?.red_dot_present === true;
  const score = asNumber(result?.score);
  const thresholdSeen = asNumber(result?.threshold) ?? threshold ?? 0.9;
  const thresholdValue = threshold ?? thresholdSeen;
  const searchRegion = String(result?.search_region || selectedRegion || "—");
  const resolvedRegion = String(result?.resolved_region || result?.region || selectedRegion || "—");
  const reason = [result?.reason, result?.detail].filter(Boolean).join(" · ");

  // Stabilise overlays for ApprovalCanvas so the probe canvas doesn't redraw
  // on every probe tick that returns the same shapes.
  const overlaysStable = useMemo(
    () => probe?.overlays ?? [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(probe?.overlays ?? [])],
  );

  return (
    <div className="approvals-probe-body">
      <p className="meta">
        Live check for a merged area region (core `area.json` + module `area.yaml`).
        Orange shows where the probe searched,
        green/gray shows the detected region or best template match.
      </p>

      <div className="toolbar">
        <AppSelect
          label="Region"
          options={(probe?.regions ?? []).map((r) => ({ value: r, label: r }))}
          value={selectedRegion || probe?.selected_region || ""}
          onChange={onRegionChange}
          placeholder="Select region…"
          minWidth={260}
          maxWidth={360}
        />
        <label>
          Threshold
          <input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={thresholdValue}
            onChange={(e) => onThresholdChange(clamp01(Number(e.target.value)))}
          />
        </label>
        <Button onClick={onRefresh}>Probe now</Button>
        {probe ? (
          <span className="meta">
            screen: <code>{probe.current_screen || "—"}</code>
            {probe.active_player ? (
              <>
                {" "}
                · player: <code>{probe.active_player}</code>
              </>
            ) : null}
          </span>
        ) : null}
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      <div className="approvals-grid">
        <div>
          <ApprovalCanvas
            imageUrl={imageUrl}
            width={probe?.preview.width ?? 0}
            height={probe?.preview.height ?? 0}
            overlays={overlaysStable}
          />
          <p className="meta probe-legend">
            <span className="probe-legend__search">■ search area</span>{" "}
            <span className="probe-legend__match">■ matched</span>{" "}
            <span className="probe-legend__miss">■ best below threshold</span>{" "}
            <span className="probe-legend__tap">+ tap point</span>
          </p>
        </div>

        <div>
          {!probe ? (
            <p className="meta">Loading region list…</p>
          ) : !result ? (
            <p className="meta">Select a region to run the probe.</p>
          ) : (
            <>
              <MetricGrid>
                <MetricCard
                  label="Matched"
                  value={matched ? "yes" : "no"}
                  tone={matched ? "ok" : "danger"}
                />
                <MetricCard
                  label={isRedDotProbe ? "Red dot" : "Score"}
                  value={
                    isRedDotProbe
                      ? redDotPresent
                        ? "present"
                        : "absent"
                      : score != null
                        ? score.toFixed(4)
                        : "—"
                  }
                  tone={isRedDotProbe ? (redDotPresent ? "ok" : "danger") : undefined}
                />
                <MetricCard
                  label="Threshold"
                  value={isRedDotProbe ? "—" : thresholdSeen.toFixed(3)}
                />
                <MetricCard label="Action" value={action} />
              </MetricGrid>
              <p className="meta">
                Region: <code>{resolvedRegion}</code>
                {result.resolved_version ? (
                  <>
                    {" "}
                    · version: <code>{String(result.resolved_version)}</code>
                  </>
                ) : null}
                {" "}
                · search: <code>{searchRegion}</code>
              </p>
              {isRedDotProbe ? (
                <p className="meta">
                  Red-dot search:{" "}
                  <code>{String(result.red_dot_search_mode || "within_zone")}</code>
                </p>
              ) : (
                <p className="meta">
                  Template:{" "}
                  <code>
                    {asNumber(result.template_w) ?? "—"}×{asNumber(result.template_h) ?? "—"}
                  </code>
                  {Array.isArray(result.top_left) ? (
                    <>
                      {" "}
                      · top-left: <code>{result.top_left.join(", ")}</code>
                    </>
                  ) : null}
                  {result.match_source ? (
                    <>
                      {" "}
                      · source: <code>{String(result.match_source)}</code>
                    </>
                  ) : null}
                </p>
              )}
              {result.template_bright_ratio != null || result.patch_bright_ratio != null ? (
                <p className="meta">
                  Bright detail ratio · template{" "}
                  <code>{fmtMaybe(result.template_bright_ratio)}</code> · live{" "}
                  <code>{fmtMaybe(result.patch_bright_ratio)}</code>
                </p>
              ) : null}
              {result.mean_saturation != null ? (
                <p className="meta">
                  Mean saturation: <code>{fmtMaybe(result.mean_saturation)}</code>
                </p>
              ) : null}
              {reason ? <p className="approvals-callout approvals-callout--warn">{reason}</p> : null}
              <RegionProbeCropCompare crops={probe.crops} region={resolvedRegion} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
