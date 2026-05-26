"use client";

import Link from "next/link";
import { useState } from "react";
import CreatableSelect from "react-select/creatable";
import type { SingleValue, StylesConfig } from "react-select";
import type { SelectOption } from "@/components/AppSelect";
import { selectThemeStyles } from "@/lib/select-theme-styles";

type Props = {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  /** Optional thumbnail URL builder for the currently selected value. */
  previewUrl?: (value: string) => string | null;
  /** Optional labeling page link for the currently selected value. */
  labelingHref?: (value: string) => string | null;
  /** When true and the current value is not in `options`, shows a warning. */
  warnIfUnknown?: boolean;
};

export function SelectWithFreetext({
  label,
  value,
  options,
  onChange,
  disabled = false,
  placeholder,
  previewUrl,
  labelingHref,
  warnIfUnknown = false,
}: Props) {
  const cur = (value || "").trim();
  const opts: SelectOption[] = [];
  const seen = new Set<string>();
  if (cur && !options.includes(cur)) {
    opts.push({ value: cur, label: cur });
    seen.add(cur);
  }
  for (const o of options) {
    if (o && !seen.has(o)) {
      opts.push({ value: o, label: o });
      seen.add(o);
    }
  }
  if (!opts.length && !cur) {
    opts.push({ value: "", label: "(empty)" });
  }

  const selected = opts.find((o) => o.value === cur) ?? (cur ? { value: cur, label: cur } : null);
  const preview = previewUrl && cur ? previewUrl(cur) : null;
  const href = labelingHref && cur ? labelingHref(cur) : null;
  const unknown = warnIfUnknown && !!cur && !options.includes(cur);

  return (
    <div className="app-select-field">
      <span>
        {label}
        {href && (
          <>
            {" "}
            <Link
              href={href}
              className="region-labeling-link"
              target="_blank"
              rel="noreferrer"
            >
              labeling ↗
            </Link>
          </>
        )}
        {unknown && (
          <span className="region-unknown-warning" title="Region not found in area.yaml / area.json">
            {" "}⚠ unknown region
          </span>
        )}
      </span>
      <div className="select-with-preview">
        <CreatableSelect<SelectOption, false>
          className="app-select"
          classNamePrefix="app-select"
          styles={{
            ...selectThemeStyles,
            container: (base) => ({ ...base, minWidth: 160, flex: 1 }),
          }}
          options={opts}
          value={selected}
          onChange={(next: SingleValue<SelectOption>) => onChange(next?.value ?? "")}
          onCreateOption={(input) => onChange(input.trim())}
          placeholder={placeholder ?? "Select or type…"}
          isDisabled={disabled}
          isClearable
          formatCreateLabel={(input) => `Use "${input}"`}
          menuShouldScrollIntoView={false}
          instanceId={`creatable-${label.replace(/\s+/g, "-").toLowerCase()}`}
        />
        {preview ? (
          <RegionPreviewThumb src={preview} alt={cur} labelingHref={href} />
        ) : null}
      </div>
    </div>
  );
}

function RegionPreviewThumb({
  src,
  alt,
  labelingHref,
}: {
  src: string;
  alt: string;
  labelingHref?: string | null;
}) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  const thumb = (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      className="region-preview-thumb"
      onError={() => setBroken(true)}
    />
  );
  const wrapped = labelingHref ? (
    <Link
      href={labelingHref}
      target="_blank"
      rel="noreferrer"
      className="region-preview-thumb-link"
      title={`${alt} — click to open in labeling`}
    >
      {thumb}
    </Link>
  ) : (
    <span className="region-preview-thumb-link" title={alt}>
      {thumb}
    </span>
  );
  return (
    <span className="region-preview-thumb-wrap">
      {wrapped}
      <span className="region-preview-popover">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src} alt={alt} className="region-preview-popover-img" />
        <span className="region-preview-popover-caption">{alt}</span>
      </span>
    </span>
  );
}
