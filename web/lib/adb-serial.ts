export function canonicalAdbSerial(serial: string): string {
  const s = (serial || "").trim();
  const match = /^emulator-(\d+)$/.exec(s);
  if (!match) {
    return s;
  }
  return `127.0.0.1:${Number(match[1]) + 1}`;
}

export function adbSerialAliases(
  serial: string,
  canonicalSerial?: string,
): string[] {
  const aliases = new Set<string>();
  const raw = (serial || "").trim();
  const canonical = (canonicalSerial || canonicalAdbSerial(raw)).trim();
  if (raw) {
    aliases.add(raw);
  }
  if (canonical) {
    aliases.add(canonical);
  }
  return [...aliases];
}

export function adbSerialMatches(
  a: string,
  b: string,
  bCanonical?: string,
): boolean {
  const aAliases = new Set(adbSerialAliases(a));
  return adbSerialAliases(b, bCanonical).some((alias) => aAliases.has(alias));
}
