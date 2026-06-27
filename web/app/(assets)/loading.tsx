import { PageLoading } from "@/components/ui";

/**
 * Route-segment fallback (Next 16.3 instant navigations). Shown only while a
 * cold route segment loads; once partialPrefetching has cached the route shell,
 * navigations are instant and this never appears. The AppShell/sidebar persist —
 * only the content area shows this.
 */
export default function Loading() {
  return <PageLoading />;
}
