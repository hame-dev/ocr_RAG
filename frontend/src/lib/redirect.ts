/**
 * The post-login destination, restricted to paths inside this app.
 *
 * "/documents/1" is kept; "//evil.com", "/\\evil.com" and absolute URLs would
 * let a crafted link bounce a freshly signed-in user to another site.
 */
export function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) {
    return "/";
  }
  return value.startsWith("/login") ? "/" : value;
}

export function loginUrl(pathname: string, search = ""): string {
  const next = `${pathname}${search}`;
  return next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
}
