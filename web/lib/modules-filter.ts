import type { ModuleRow } from "@/lib/config-pages";

/**
 * Case-insensitive substring filter over the fields an operator would search
 * by on the Modules page. An empty/whitespace query returns the list as-is.
 *
 * Extracted from the page so the matching rules are unit-testable without
 * rendering the whole component.
 */
export function filterModules(modules: ModuleRow[], query: string): ModuleRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return modules;
  return modules.filter(
    (m) =>
      m.id.toLowerCase().includes(q) ||
      m.title.toLowerCase().includes(q) ||
      m.storage_key.toLowerCase().includes(q) ||
      m.description.toLowerCase().includes(q) ||
      m.rel_path.toLowerCase().includes(q),
  );
}
