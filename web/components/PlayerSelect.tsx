"use client";

import { AppSelect } from "@/components/AppSelect";
import { playerSelectPlaceholder } from "@/lib/fleet-select";

export function PlayerSelect({
  players,
  value,
  onChange,
  disabled,
  loading = false,
}: {
  players: string[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  loading?: boolean;
}) {
  const options = players.map((id) => ({ value: id, label: id }));
  const empty = !loading && options.length === 0;

  return (
    <AppSelect
      label="Player"
      options={options}
      value={value}
      onChange={onChange}
      loading={loading}
      disabled={disabled || empty}
      placeholder={playerSelectPlaceholder(loading, empty)}
      minWidth={200}
    />
  );
}
