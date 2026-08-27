"""חלוקת מרחב השמות בין ה-API לבין ה-Angular Router.

הבדיקות האלה קיימות בגלל באג אמיתי. בגרסה הראשונה כל נתיב API נרשם
פעמיים — גם ישירות וגם תחת /api — כדי שסקריפטים לא יצטרכו קידומת.
אבל /traces ו-/documents הם גם נתיבי Angular, ולכן רענון דף על
/traces/<uuid> נתפס על ידי ה-API והחזיר 401 JSON במקום את האפליקציה.
הקישור העמוק לטרייס הוא בדיוק מה ששולחים למישהו כשרוצים שיסתכל על
שליפה מסוימת, ולכן זה לא באג קוסמטי.

התיקון: /api שייך לשרת, השורש שייך ל-SPA, ו-/health חי בשניהם.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.ui import dist_dir
from app.main import app

HAS_BUILD = dist_dir() is not None
needs_build = pytest.mark.skipif(HAS_BUILD is False, reason="ui/dist לא נבנה (make ui-build)")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_api_routes_live_only_under_the_api_prefix():
    paths = set(app.openapi()["paths"])
    assert "/api/auth/login" in paths
    # אם אחד מאלה חוזר, הקישורים העמוקים של ה-SPA נשברים שוב.
    for collision in ("/auth/login", "/traces", "/documents", "/chat"):
        assert collision not in paths, f"{collision} מתנגש עם נתיב Angular"
    assert not any(p.startswith("/traces/") for p in paths)


def test_health_is_reachable_both_ways(client):
    """HEALTHCHECK של Docker פונה ל-/health; הממשק פונה ל-/api/health."""
    for path in ("/health", "/api/health"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.json()["database"] in {"up", "down"}


@needs_build
@pytest.mark.parametrize("path", ["/", "/chat", "/traces", "/traces/abc-123", "/documents", "/login"])
def test_deep_links_return_the_app_shell(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert "<app-root" in resp.text
    # index.html לעולם לא נשמר במטמון — אחרת בנייה חדשה לא מגיעה למשתמש
    assert resp.headers.get("cache-control") == "no-cache"


@needs_build
def test_an_unknown_api_path_is_a_404_and_not_the_app_shell(client):
    """אחרת הלקוח היה מנסה לפרסר HTML כ-JSON ומדווח על שגיאה במקום הלא נכון."""
    resp = client.get("/api/typo")
    assert resp.status_code == 404
    assert "<app-root" not in resp.text


@needs_build
def test_a_missing_asset_is_a_404_and_not_the_app_shell(client):
    """chunk חסר חייב להיראות כחסר, לא כ-HTML עם MIME שגוי."""
    resp = client.get("/missing-chunk.js")
    assert resp.status_code == 404


@needs_build
def test_path_traversal_cannot_escape_the_build_directory(client):
    resp = client.get("/../../../../etc/passwd")
    assert "root:" not in resp.text
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert "<app-root" in resp.text


@needs_build
def test_hashed_assets_are_served_immutable(client):
    """ה-hash משתנה בכל בנייה, ולכן שנה של cache בטוחה — ומשנה את זמן הטעינה."""
    import re

    shell = client.get("/").text
    match = re.search(r'src="(main-[A-Za-z0-9]+\.js)"', shell)
    assert match, "לא נמצא main-<hash>.js ב-index.html"
    resp = client.get(f"/{match.group(1)}")
    assert resp.status_code == 200
    assert "immutable" in resp.headers.get("cache-control", "")
