"use client";

import { AppSelect } from "@/components/AppSelect";
import { instanceSelectPlaceholder } from "@/lib/fleet-select";

export function InstanceSelect({
  instances,
  value,
  onChange,
  loading = false,
}: {
  instances: string[];
  value: string;
  onChange: (id: string) => void;
  loading?: boolean;
}) {
  const options = instances.map((id) => ({ value: id, label: id }));
  const empty = !loading && options.length === 0;

  return (
    <AppSelect
      label="Instance"
      options={options}
      value={value}
      onChange={onChange}
      loading={loading}
      disabled={empty}
      placeholder={instanceSelectPlaceholder(loading, empty)}
      minWidth={170}
    />
  );
}
