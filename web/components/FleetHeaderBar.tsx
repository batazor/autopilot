"use client";

import { InstanceSelect } from "@/components/InstanceSelect";
import { PlayerSelect } from "@/components/PlayerSelect";
import { useFleet } from "@/components/FleetContextProvider";

export type FleetHeaderBarProps = {
  /** Show player dropdown (Player state and similar). */
  showPlayer?: boolean;
};

/**
 * The instance/player selectors rendered on the right side of {@link PageHeader}
 * when `fleet` is enabled.
 */
export function FleetHeaderBar({ showPlayer = false }: FleetHeaderBarProps) {
  const {
    instances,
    players,
    instanceId,
    playerId,
    setInstanceId,
    setPlayerId,
    instancesLoading,
    playersLoading,
  } = useFleet();

  return (
    <div className="fleet-header-bar">
      <InstanceSelect
        instances={instances}
        value={instanceId}
        onChange={setInstanceId}
        loading={instancesLoading}
      />
      {showPlayer ? (
        <PlayerSelect
          players={players}
          value={playerId}
          onChange={setPlayerId}
          loading={playersLoading}
        />
      ) : null}
    </div>
  );
}
