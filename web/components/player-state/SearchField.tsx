"use client";

type Props = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
};

export function SearchField({
  label,
  value,
  onChange,
  placeholder,
  className = "",
}: Props) {
  return (
    <label className={`player-state-search ${className}`.trim()}>
      <span className="player-state-search__label">{label}</span>
      <input
        type="search"
        className="player-state-search__input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
      />
    </label>
  );
}
