import { NextResponse, type NextRequest } from "next/server";

const API_URL = process.env.WOS_API_URL || "http://127.0.0.1:8765";

const PUBLIC_PREFIXES = ["/license", "/api", "/health", "/_next", "/favicon"];

function isPublic(pathname: string): boolean {
  return PUBLIC_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (isPublic(pathname)) return NextResponse.next();

  try {
    const res = await fetch(`${API_URL}/api/license/status`, {
      cache: "no-store",
    });
    if (res.ok) {
      const status = (await res.json()) as { state?: string };
      if (status.state === "active") return NextResponse.next();
    }
  } catch {
    return NextResponse.next();
  }

  const url = req.nextUrl.clone();
  url.pathname = "/license";
  url.searchParams.set("from", pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
