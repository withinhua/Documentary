import { protocol, net } from "electron";
import path from "node:path";
import { pathToFileURL } from "node:url";

const SCHEME = "app";

export function registerAppSchemePrivileges(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: SCHEME,
      privileges: {
        standard: true,
        secure: true,
        supportFetchAPI: true,
        corsEnabled: true,
        stream: true,
      },
    },
  ]);
}

export function handleAppScheme(rendererRoot: string, studioFeedRoot?: () => string | null): void {
  protocol.handle(SCHEME, async (request) => {
    const url = new URL(request.url);
    let pathname = decodeURIComponent(url.pathname);
    if (pathname === "/" || pathname === "") pathname = "/index.html";
    if (pathname === "/motion") pathname = "/index.html";

    // Documentary Studio: /studio-feed/* comes from the user's workspace (live pipeline output)
    // when one is set up; otherwise from the bundled demo feed inside the renderer.
    const feedRoot = pathname.startsWith("/studio-feed/") ? studioFeedRoot?.() : null;
    if (feedRoot) pathname = pathname.slice("/studio-feed".length);
    const root = path.normalize(feedRoot ?? rendererRoot);
    const resolved = path.normalize(path.join(root, pathname));
    const rel = path.relative(root, resolved);
    if (rel !== "" && (rel === ".." || rel.startsWith(".." + path.sep) || path.isAbsolute(rel))) {
      return new Response("Forbidden", { status: 403 });
    }

    let response: Response;
    try {
      response = await net.fetch(pathToFileURL(resolved).toString());
    } catch {
      if (feedRoot) return new Response("Not found", { status: 404 });
      response = await net.fetch(pathToFileURL(path.join(rendererRoot, "index.html")).toString());
    }

    const headers = new Headers(response.headers);
    headers.set("Cross-Origin-Opener-Policy", "same-origin");
    headers.set("Cross-Origin-Embedder-Policy", "require-corp");
    headers.set("Cross-Origin-Resource-Policy", "same-origin");
    if (feedRoot) headers.set("Cache-Control", "no-store");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  });
}

export const APP_ORIGIN = `${SCHEME}://openreel`;
export const APP_INDEX = `${APP_ORIGIN}/index.html`;
