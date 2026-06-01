// Tiny client-side CSV export helper. Builds RFC-4180-ish CSV (CRLF rows,
// quote-escaped fields) and triggers a browser download.

export type CsvColumn<T> = {
  header: string;
  value: (row: T) => string | number | boolean | null | undefined;
};

function escapeField(value: unknown): string {
  const s = value == null ? "" : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv<T>(rows: T[], columns: CsvColumn<T>[]): string {
  const head = columns.map((c) => escapeField(c.header)).join(",");
  if (rows.length === 0) return head;
  const body = rows
    .map((row) => columns.map((c) => escapeField(c.value(row))).join(","))
    .join("\r\n");
  return `${head}\r\n${body}`;
}

/** Build a CSV from rows/columns and prompt a download. Prefixes a UTF-8 BOM
 *  so Excel opens it with the right encoding. No-op outside the browser. */
export function downloadCsv<T>(filename: string, rows: T[], columns: CsvColumn<T>[]): void {
  if (typeof document === "undefined") return;
  const csv = toCsv(rows, columns);
  const blob = new Blob([`﻿${csv}`], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
