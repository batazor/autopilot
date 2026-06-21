import type { HeroStateRow } from "@/lib/types";
import { DataTable } from "./DataTable";

export function HeroTable({ rows, locked }: { rows: HeroStateRow[]; locked: boolean }) {
  const cols = locked
    ? [
        { key: "id", label: "ID" },
        { key: "hero", label: "Hero" },
        { key: "shards_current", label: "Shards", align: "right" as const },
        { key: "shards_required", label: "Required", align: "right" as const },
        { key: "rarity", label: "Rarity" },
        { key: "class", label: "Class" },
      ]
    : [
        { key: "id", label: "ID" },
        { key: "hero", label: "Hero" },
        { key: "level", label: "Lv", align: "right" as const },
        { key: "rarity", label: "Rarity" },
        { key: "seen", label: "Seen" },
      ];
  return <DataTable columns={cols} rows={rows} />;
}
