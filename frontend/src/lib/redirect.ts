/**
 * The post-login destination, restricted to paths inside this app.
 *
 * "/documents/1" is kept; "//evil.com", "/\\evil.com" and absolute URLs would
 * let a crafted link bounce a freshly signed-in user to another site.
 */
export function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/")) return "/";
  // Resolve it the way the router will. A prefix check alone is not enough:
  // the URL parser drops tabs and newlines, so "/\t/evil.com" becomes
  // "//evil.com", which is another site.
  const origin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
  let url: URL;
  try {
    url = new URL(value, origin);
  } catch {
    return "/";
  }
  if (url.origin !== origin) return "/";
  const path = `${url.pathname}${url.search}${url.hash}`;
  return url.pathname.startsWith("/login") ? "/" : path;
}

export function loginUrl(pathname: string, search = ""): string {
  const next = `${pathname}${search}`;
  return next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
}
