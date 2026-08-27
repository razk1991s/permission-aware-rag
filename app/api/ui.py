"""Serve the Angular application.

There are intentionally two run modes:

**Development** - `ng serve` on port 4200 with a proxy to port 8000.
Full HMR is enabled and CORS is limited to dev.

**Production** - `ng build` creates `ui/dist/browser`, which FastAPI serves.
There is one service, one port, and no CORS because browser and API share an origin.

Namespace split: `/api/*` belongs to the server, `/health` and `/stats` are
health checks, and everything else belongs to the Angular Router.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app.config import ROOT

log = logging.getLogger(__name__)

router = APIRouter(tags=["ui"])

# Angular 17+ מוציא ל-dist/browser; גרסאות ישנות ל-dist ישירות.
_CANDIDATES = (ROOT / "ui" / "dist" / "browser", ROOT / "ui" / "dist")


def dist_dir() -> Path | None:
    for path in _CANDIDATES:
        if (path / "index.html").exists():
            return path
    return None


_PLACEHOLDER = """<!doctype html>
<html lang="en" dir="ltr"><head><meta charset="utf-8"><title>Meridian</title>
<style>
body{font:16px/1.7 system-ui,Arial,sans-serif;max-width:640px;margin:14vh auto;padding:0 20px;
     background:#f3f5f8;color:#141a23}
@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e4e8ef}}
code{background:#0002;padding:.15em .4em;border-radius:4px;direction:ltr;display:inline-block}
a{color:#1f5fa8}
</style></head><body>
<h1>The API is running, but the UI has not been built</h1>
<p>The Angular application was not found in <code>ui/dist</code>. There are two ways to run it:</p>
<h3>Development</h3>
<p><code>make ui-dev</code> runs <code>ng serve</code> on
<a href="http://localhost:4200">localhost:4200</a> with a proxy to this server.</p>
<h3>Production</h3>
<p><code>make ui-build</code> creates the static build, which this URL will serve.</p>
<p style="margin-top:2rem"><a href="/docs">API documentation -></a></p>
</body></html>"""

# Server paths must not fall through to the SPA. Without this list, an unknown
# API path could receive index.html with 200 and be parsed as JSON by the client.
_SERVER_PREFIXES = ("api/", "docs", "redoc", "openapi.json", "health", "stats")


def mount_spa(app) -> None:
    """Mount the built SPA at the application root, if available.

    Called from main.py **after** all other routes are registered because this
    catch-all handles unmatched requests.
    """
    dist = dist_dir()
    if dist is None:
        log.info("ui/dist not found — serving the build placeholder at /")

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def _placeholder() -> str:
            return _PLACEHOLDER

        return

    log.info("serving Angular build from %s", dist)
    index = dist / "index.html"

    # Support HEAD as well as GET because health checks and proxies send HEAD.
    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(_SERVER_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")

        if full_path:
            candidate = (dist / full_path).resolve()
            # Resolve before comparing so paths such as ../../etc/passwd cannot
            # escape the build directory.
            if dist in candidate.parents and candidate.is_file():
                return FileResponse(candidate, headers=_asset_headers(candidate))

            # A missing file with an extension is a real 404, not an SPA route.
            if Path(full_path).suffix:
                raise HTTPException(status_code=404, detail="Not Found")

        # Everything else is an Angular route such as /chat or /traces/<uuid>.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _asset_headers(path: Path) -> dict[str, str]:
    """Hashed filenames are immutable; other files are not cached.

    The hash changes on every build, making a one-year cache safe.
    """
    hashed = len(path.stem.rsplit("-", 1)) == 2 and len(path.stem.rsplit("-", 1)[1]) >= 8
    if hashed and path.suffix in {".js", ".css", ".woff2", ".woff"}:
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {"Cache-Control": "no-cache"}


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    dist = dist_dir()
    icon = (dist / "favicon.ico") if dist else None
    if icon and icon.exists():
        return FileResponse(icon)
    return HTMLResponse(status_code=204, content="")
