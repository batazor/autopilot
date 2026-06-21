import type { Dispatch, SetStateAction } from "react";
import { Button, Card, Icon } from "@/components/ui";

type GeneratePanelProps = {
  count: number;
  setCount: Dispatch<SetStateAction<number>>;
  seed: string;
  setSeed: Dispatch<SetStateAction<string>>;
  busy: boolean;
  onGenerate: () => void;
};

/** "Generate accounts" form panel. */
export function GeneratePanel({
  count,
  setCount,
  seed,
  setSeed,
  busy,
  onGenerate,
}: GeneratePanelProps) {
  return (
    <Card>
      <h2 className="m-0 text-base font-semibold text-wos-text">Generate accounts</h2>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="muted block">Count</span>
          <input
            type="number"
            min={1}
            max={50}
            value={count}
            onChange={(e) => setCount(Math.max(1, Number(e.target.value) || 1))}
            className="field mt-1 w-20"
          />
        </label>
        <label className="text-sm">
          <span className="muted block">Seed (optional)</span>
          <input
            type="text"
            value={seed}
            placeholder="reproducible batch"
            onChange={(e) => setSeed(e.target.value)}
            className="field mt-1 w-48"
          />
        </label>
        <Button className="inline-flex items-center gap-1.5" disabled={busy} onClick={onGenerate}>
          <Icon name="plus" size="sm" />
          Generate {count}
        </Button>
      </div>
    </Card>
  );
}
