"use client";

/** Editor for a scene's alternate on-screen names (matching aliases).
 *
 * A scene can be labeled differently in-game than its title, and that label can
 * vary (e.g. across seasons / device languages), so the solver matches against a
 * *list* of aliases. This renders that list as one input per alias plus add/
 * remove controls, surfacing the array directly to the operator.
 *
 * The parent owns the array; this component is a thin controlled editor over it. */
export function AltTitlesEditor({
  value,
  onChange,
  label = "Alt names (matching aliases)",
  className = "",
}: {
  value: string[];
  onChange: (next: string[]) => void;
  label?: string;
  className?: string;
}) {
  // Always show at least one (empty) row so there's somewhere to type.
  const rows = value.length ? value : [""];

  const setAt = (index: number, text: string) => {
    const next = rows.slice();
    next[index] = text;
    onChange(next);
  };
  const removeAt = (index: number) => {
    const next = rows.filter((_, i) => i !== index);
    onChange(next);
  };
  const addRow = () => onChange([...rows, ""]);

  return (
    <div className={`flex flex-col gap-1 text-xs text-wos-text-muted ${className}`}>
      <span>{label}</span>
      <div className="flex flex-col gap-1.5">
        {rows.map((alias, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <input
              type="text"
              value={alias}
              onChange={(e) => setAt(i, e.target.value)}
              placeholder={i === 0 ? "optional, e.g. Backyard" : "another alias"}
              title="An alternate on-screen level name this scene also matches against"
              className="w-44 rounded border border-wos-border bg-wos-bg-deep px-2 py-1 text-sm text-wos-text"
            />
            <button
              type="button"
              onClick={() => removeAt(i)}
              disabled={rows.length === 1 && !alias}
              title="Remove this alias"
              className="rounded border border-wos-border-subtle px-2 py-1 text-wos-text-muted transition hover:text-wos-text disabled:cursor-not-allowed disabled:opacity-40"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={addRow}
        className="self-start rounded border border-wos-border-subtle px-2 py-1 text-wos-text-muted transition hover:text-wos-text"
      >
        + Add alias
      </button>
    </div>
  );
}

/** Drop blanks and collapse whitespace before persisting an alias list. */
export function cleanAltTitles(values: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const text = value.trim().replace(/\s+/g, " ");
    const key = text.toLowerCase();
    if (text && !seen.has(key)) {
      seen.add(key);
      out.push(text);
    }
  }
  return out;
}
