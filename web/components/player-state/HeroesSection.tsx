import type { PlayerPersistedView } from "@/lib/types";
import {
  COLLAPSE_HEROES_ABOVE,
  countLabel,
  filterHeroRows,
} from "@/lib/player-state/helpers";
import { CollapsiblePanel } from "./CollapsiblePanel";
import { DataTable } from "./DataTable";
import { HeroTable } from "./HeroTable";
import { HeroTileGrid } from "./HeroTileGrid";
import { MetricsRow } from "./MetricsRow";

export function HeroesSection({
  heroView,
  heroFilter,
}: {
  heroView: NonNullable<NonNullable<PlayerPersistedView["player"]>["heroes"]>;
  heroFilter: string;
}) {
  const owned = filterHeroRows(heroView.owned, heroFilter);
  const locked = filterHeroRows(heroView.locked, heroFilter);
  const missing = filterHeroRows(heroView.missing, heroFilter);
  return (
    <>
      <MetricsRow
        items={[
          { label: "Owned", value: String(heroView.metrics.owned) },
          { label: "Locked", value: String(heroView.metrics.locked) },
          { label: "In registry", value: String(heroView.metrics.registry_total) },
          { label: "Notify", value: heroView.metrics.notify ? "yes" : "no" },
        ]}
      />
      <CollapsiblePanel
        title="Heroes · owned"
        meta={countLabel(owned.length, heroView.owned.length)}
        defaultOpen={heroView.owned.length <= COLLAPSE_HEROES_ABOVE}
      >
        <HeroTileGrid rows={owned} locked={false} />
        <details className="player-state-subsection">
          <summary className="meta">Table view</summary>
          <HeroTable rows={owned} locked={false} />
        </details>
      </CollapsiblePanel>
      <CollapsiblePanel
        title="Heroes · collecting shards"
        meta={countLabel(locked.length, heroView.locked.length)}
        defaultOpen={heroView.locked.length <= COLLAPSE_HEROES_ABOVE}
      >
        <HeroTileGrid rows={locked} locked />
        <details className="player-state-subsection">
          <summary className="meta">Table view</summary>
          <HeroTable rows={locked} locked />
        </details>
      </CollapsiblePanel>
      <CollapsiblePanel
        title="Heroes · not yet seen"
        meta={countLabel(missing.length, heroView.missing.length)}
        defaultOpen={heroView.missing.length <= COLLAPSE_HEROES_ABOVE}
      >
        {missing.length ? (
          <DataTable
            columns={[
              { key: "id", label: "ID" },
              { key: "hero", label: "Hero" },
              { key: "rarity", label: "Rarity" },
              { key: "class", label: "Class" },
            ]}
            rows={missing}
          />
        ) : (
          <p className="meta">No heroes matched the filter.</p>
        )}
      </CollapsiblePanel>
    </>
  );
}
