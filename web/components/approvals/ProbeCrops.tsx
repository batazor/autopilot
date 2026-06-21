import type { AreaRegionProbeResult } from "@/lib/types";

function ProbeCropTile({
  side,
  fallbackCaption,
}: {
  side?: { available?: boolean; width?: number; height?: number; label?: string; data_url?: string };
  fallbackCaption: string;
}) {
  const caption = side?.label || fallbackCaption;
  const w = side?.width ?? 0;
  const h = side?.height ?? 0;
  const sizeLabel = w > 0 && h > 0 ? `${w}×${h} px` : null;

  return (
    <figure className="approvals-probe-crops__tile">
      <figcaption className="meta">{caption}</figcaption>
      {side?.available && side.data_url ? (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={side.data_url} alt={caption} className="approvals-probe-crops__img" />
          {sizeLabel ? <p className="meta approvals-probe-crops__size">{sizeLabel}</p> : null}
        </>
      ) : (
        <p className="meta approvals-probe-crops__empty">—</p>
      )}
    </figure>
  );
}

export function RegionProbeCropCompare({
  crops,
  region,
}: {
  crops: AreaRegionProbeResult["crops"];
  region: string;
}) {
  if (!crops) return null;

  const live = crops.live;
  const template = crops.template;
  const titleRegion = crops.region || region || "—";

  return (
    <section className="approvals-probe-crops panel" aria-label="Live crop vs template">
      <h3 className="approvals-probe-crops__title">
        <code>{titleRegion}</code>
        {crops.resolved_region && crops.resolved_region !== titleRegion ? (
          <>
            {" "}
            → <code>{crops.resolved_region}</code>
          </>
        ) : null}
        {" "}
        — live crop vs template
      </h3>
      {crops.reference_rel ? (
        <p className="meta approvals-probe-crops__ref">
          Template from <code>{crops.reference_rel}</code>
          {template?.label && template.label !== "Template crop" ? (
            <>
              {" "}
              · <code>references/crop/{template.label}</code>
            </>
          ) : null}
        </p>
      ) : null}
      <div className="approvals-probe-crops__grid">
        <ProbeCropTile side={live} fallbackCaption="Live (rolling PNG)" />
        <ProbeCropTile side={template} fallbackCaption="Template crop" />
      </div>
    </section>
  );
}
