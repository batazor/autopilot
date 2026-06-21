import { describe, expect, it } from "vitest";
import { toCsv, type CsvColumn } from "./csv";

type Row = { id: number; name: string; note: string };

const columns: CsvColumn<Row>[] = [
  { header: "id", value: (r) => r.id },
  { header: "name", value: (r) => r.name },
  { header: "note", value: (r) => r.note },
];

describe("toCsv", () => {
  it("emits a header row only when there are no data rows", () => {
    expect(toCsv([], columns)).toBe("id,name,note");
  });

  it("joins rows with CRLF and fields with commas", () => {
    const rows: Row[] = [
      { id: 1, name: "Tazor", note: "ok" },
      { id: 2, name: "King", note: "fine" },
    ];
    expect(toCsv(rows, columns)).toBe("id,name,note\r\n1,Tazor,ok\r\n2,King,fine");
  });

  it("quotes fields containing commas, quotes, or newlines and escapes quotes", () => {
    const rows: Row[] = [{ id: 3, name: 'a,b"c', note: "line1\nline2" }];
    expect(toCsv(rows, columns)).toBe('id,name,note\r\n3,"a,b""c","line1\nline2"');
  });

  it("renders null/undefined as empty fields", () => {
    const cols: CsvColumn<{ a?: string }>[] = [{ header: "a", value: (r) => r.a }];
    expect(toCsv([{ a: undefined }], cols)).toBe("a\r\n");
  });
});
