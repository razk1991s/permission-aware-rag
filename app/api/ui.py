"""הגשת אפליקציית ה-Angular.

שני מצבי הרצה, ובכוונה:

**פיתוח** — `ng serve` על 4200 עם proxy ל-8000. HMR מלא, וה-CORS
מוגדר רק לסביבת dev.

**פרודקשן** — `ng build` יוצר `ui/dist/browser`, ו-FastAPI מגיש אותו.
מכאן שירות אחד, פורט אחד, ואין CORS בכלל — הדפדפן וה-API על אותו
origin, מה שגם מייתר שאלות של cookies ו-preflight.

חלוקת מרחב השמות: `/api/*` שייך לשרת, `/health` ו-`/stats` לבדיקות
חיים, וכל השאר שייך ל-Angular Router.
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
<html lang="he" dir="rtl"><head><meta charset="utf-8"><title>Meridian</title>
<style>
body{font:16px/1.7 system-ui,Arial,sans-serif;max-width:640px;margin:14vh auto;padding:0 20px;
     background:#f3f5f8;color:#141a23}
@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e4e8ef}}
code{background:#0002;padding:.15em .4em;border-radius:4px;direction:ltr;display:inline-block}
a{color:#1f5fa8}
</style></head><body>
<h1>ה-API פועל, אבל ממשק המשתמש עוד לא נבנה</h1>
<p>אפליקציית ה-Angular לא נמצאה ב-<code>ui/dist</code>. יש שתי דרכים להריץ אותה:</p>
<h3>פיתוח</h3>
<p><code>make ui-dev</code> — מריץ <code>ng serve</code> על
<a href="http://localhost:4200">localhost:4200</a> עם proxy לשרת הזה.</p>
<h3>פרודקשן</h3>
<p><code>make ui-build</code> — בונה לסטטי, ואז הכתובת הזו תגיש את האפליקציה.</p>
<p style="margin-top:2rem"><a href="/docs">תיעוד ה-API →</a></p>
</body></html>"""

# נתיבים ששייכים לשרת ואסור שייפלו ל-SPA. בלי הרשימה הזו, בקשה
# ל-/api/typo הייתה מקבלת index.html עם קוד 200 — והלקוח היה מנסה
# לפרסר HTML כ-JSON ומדווח על שגיאה במקום הלא נכון.
_SERVER_PREFIXES = ("api/", "docs", "redoc", "openapi.json", "health", "stats")


def mount_spa(app) -> None:
    """מרכיב את ה-SPA על שורש האפליקציה, אם הוא נבנה.

    נקרא מ-main.py **אחרי** רישום כל שאר הנתיבים, כי ה-catch-all כאן
    תופס כל בקשה שלא הותאמה לפניו.
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

    # HEAD ולא רק GET: בודקי זמינות ו-proxies שולחים HEAD, ו-FastAPI
    # אינו מוסיף אותו מעצמו כפי ש-Starlette עושה לנתיב רגיל.
    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(_SERVER_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")

        if full_path:
            candidate = (dist / full_path).resolve()
            # resolve() לפני ההשוואה: בלעדיו '../../etc/passwd' היה
            # יוצא מתיקיית הבנייה. אחריו, נתיב שאינו מתחת ל-dist נדחה.
            if dist in candidate.parents and candidate.is_file():
                return FileResponse(candidate, headers=_asset_headers(candidate))

            # קובץ עם סיומת שלא נמצא הוא 404 אמיתי, לא ניתוב. אחרת
            # chunk חסר היה מוחזר כ-HTML, והדפדפן היה מתלונן על MIME
            # במקום להצביע על הקובץ שחסר.
            if Path(full_path).suffix:
                raise HTTPException(status_code=404, detail="Not Found")

        # כל השאר הוא נתיב Angular: /chat, /traces/<uuid>, /documents.
        # זה מה שמאפשר רענון דף ושיתוף קישור עמוק לטרייס.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _asset_headers(path: Path) -> dict[str, str]:
    """קבצים עם hash בשם הם immutable; השאר לא נשמרים במטמון.

    ה-hash משתנה בכל בנייה, ולכן אין סיכון להגיש גרסה ישנה — וזה בדיוק
    מה שהופך שנה של cache לבטוחה.
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
