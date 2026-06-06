import type { ModuleRow } from "@/lib/config-pages";

export type ModuleSearchIndexItem = {
  module: ModuleRow;
  searchText: string;
};

function moduleSearchText(m: ModuleRow): string {
  return [
    m.id,
    m.title,
    m.storage_key,
    m.description,
    m.rel_path,
  ]
    .join("\n")
    .toLowerCase();
}

export function createModuleSearchIndex(
  modules: ModuleRow[],
): ModuleSearchIndexItem[] {
  return modules.map((module) => ({
    module,
    searchText: moduleSearchText(module),
  }));
}

export function filterModuleSearchIndex(
  index: ModuleSearchIndexItem[],
  query: string,
): ModuleRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return index.map((item) => item.module);
  return index
    .filter((item) => item.searchText.includes(q))
    .map((item) => item.module);
}

/**
 * Case-insensitive substring filter over the fields an operator would search
 * by on the Modules page. An empty/whitespace query returns the list as-is.
 *
 * Extracted from the page so the matching rules are unit-testable without
 * rendering the whole component.
 */
export function filterModules(modules: ModuleRow[], query: string): ModuleRow[] {
  return filterModuleSearchIndex(createModuleSearchIndex(modules), query);
}
