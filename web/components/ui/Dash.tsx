/**
 * Muted placeholder for an empty / unknown cell value. De-emphasises the
 * em-dash so real data reads first and a column of blanks stops competing
 * for attention. Use anywhere a table or card field has no value.
 */
export function Dash({ title }: { title?: string }) {
  return (
    <span className="select-none text-wos-text-muted/60" title={title} aria-hidden>
      —
    </span>
  );
}
