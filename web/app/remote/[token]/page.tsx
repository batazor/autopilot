import { RemoteScreen } from "@/components/RemoteScreen";

// Nothing here can be prerendered: the page is one live MJPEG stream keyed by
// the URL's token, and the root AppShell reads `usePathname()` to decide it
// must render bare. Both are runtime-only, so the segment opts out of
// instant-navigation validation (Next 16.3 cacheComponents).
export const instant = false;

export default async function RemoteControlPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return <RemoteScreen token={token} />;
}
