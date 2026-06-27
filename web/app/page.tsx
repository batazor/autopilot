import { redirect } from "next/navigation";

// Pure redirect to /farm — there is no shell to render here, so opt this
// segment out of instant-navigation validation (Next 16.3 cacheComponents).
// `redirect()` throws NEXT_REDIRECT, which the `instant` check otherwise flags.
export const instant = false;

export default function Home() {
  redirect("/farm");
}
