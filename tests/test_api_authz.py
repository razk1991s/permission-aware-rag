"""בדיקות אינטגרציה: הרשאות ברמת מסמך, מקצה לקצה מול ה-API.

הבדיקות האלה דורשות מסד נתונים טעון (migrate + seed + ingest). בלעדיו הן
מדולגות, כדי ש-`pytest` יעבור גם על מכונה נקייה.

    export DATABASE_URL=postgresql+asyncpg://rag:ragpass@localhost:5432/ragdb
    make ingest-fast && pytest tests/test_api_authz.py
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

RUN = bool(os.getenv("DATABASE_URL"))
skip_no_db = pytest.mark.skipif(not RUN, reason="לא הוגדר DATABASE_URL — בדיקת אינטגרציה")

USERS = {
    "hr": "dana@meridian.local",
    "finance": "yuval@meridian.local",
    "support": "maya@meridian.local",
    "employee": "ori@meridian.local",
    "admin": "admin@meridian.local",
}
PASSWORD = "Demo1234!"


@pytest_asyncio.fixture
async def client():
    from app.db import dispose_engine
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as c:
        yield c
    await dispose_engine()


async def token_for(client: AsyncClient, role: str) -> str:
    resp = await client.post("/auth/login", json={"email": USERS[role], "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def doc_ids(client: AsyncClient, role: str) -> set[str]:
    resp = await client.get("/documents", headers=auth(await token_for(client, role)))
    assert resp.status_code == 200, resp.text
    return {d["doc_id"] for d in resp.json()}


# ------------------------------------------------------------ התחברות
@skip_no_db
async def test_login_rejects_wrong_password(client):
    resp = await client.post(
        "/auth/login", json={"email": USERS["hr"], "password": "not-the-password"}
    )
    assert resp.status_code == 401


@skip_no_db
async def test_login_rejects_unknown_user(client):
    resp = await client.post("/auth/login", json={"email": "nobody@meridian.local", "password": PASSWORD})
    assert resp.status_code == 401


@skip_no_db
async def test_endpoint_requires_token(client):
    assert (await client.get("/documents")).status_code == 401
    assert (await client.get("/documents", headers=auth("garbage"))).status_code == 401


# ------------------------------------------------------------ הפרדת דומיינים
@skip_no_db
async def test_hr_cannot_see_finance_documents(client):
    hr = await doc_ids(client, "hr")
    assert "HR-003" in hr, "משתמש hr חייב לראות את טבלת השכר"
    assert not {d for d in hr if d.startswith("FIN-")}, "משתמש hr רואה מסמכי כספים"


@skip_no_db
async def test_finance_cannot_see_hr_documents(client):
    fin = await doc_ids(client, "finance")
    assert "FIN-001" in fin
    assert not {d for d in fin if d.startswith("HR-")}, "משתמש finance רואה מסמכי משאבי אנוש"


@skip_no_db
async def test_support_sees_subset_of_finance_only(client):
    """הבדיקה המעניינת: שני תפקידים באותו דומיין עם גישה שונה."""
    support = await doc_ids(client, "support")
    assert "FIN-001" in support, "support חייב לראות את נוהל הזיכויים"
    assert "FIN-002" not in support, "support אינו אמור לראות את מדיניות הריבית"
    assert "FIN-005" not in support, "support אינו אמור לראות את נוהל האשראי"


@skip_no_db
async def test_employee_sees_public_only(client):
    emp = await doc_ids(client, "employee")
    assert emp and all(d.startswith("PUB-") for d in emp)


@skip_no_db
async def test_admin_sees_everything(client):
    admin = await doc_ids(client, "admin")
    assert {"FIN-001", "HR-003", "PUB-001"} <= admin


# ------------------------------------------------------------ אין דליפה בצ'אנקים
@skip_no_db
async def test_forbidden_document_chunks_return_404_not_403(client):
    """404 ולא 403: אחרת אפשר למפות אילו מסמכים קיימים במערכת."""
    resp = await client.get(
        "/documents/HR-003/chunks", headers=auth(await token_for(client, "finance"))
    )
    assert resp.status_code == 404


@skip_no_db
async def test_permitted_document_chunks_are_returned(client):
    resp = await client.get(
        "/documents/HR-003/chunks", headers=auth(await token_for(client, "hr"))
    )
    assert resp.status_code == 200
    chunks = resp.json()
    assert chunks
    grade_7 = [c for c in chunks if "18500" in c["content"] or "18,500" in c["content"]]
    assert grade_7, "טבלת השכר נטענה בלי דרגה 7"


@skip_no_db
async def test_no_role_can_read_a_chunk_outside_its_acl(client):
    """סריקה רוחבית: אף תפקיד לא מקבל 200 על מסמך שאינו ברשימה שלו."""
    matrix = {
        "hr": ["FIN-001", "FIN-002", "FIN-010"],
        "finance": ["HR-001", "HR-003", "HR-004"],
        "support": ["FIN-002", "FIN-004", "FIN-005", "FIN-007", "HR-003"],
        "employee": ["FIN-001", "HR-001", "FIN-010"],
    }
    leaks: list[str] = []
    for role, forbidden in matrix.items():
        headers = auth(await token_for(client, role))
        for doc in forbidden:
            resp = await client.get(f"/documents/{doc}/chunks", headers=headers)
            if resp.status_code == 200:
                leaks.append(f"{role} → {doc}")
    assert not leaks, f"permission_leak_rate != 0 — דליפות: {leaks}"


# ------------------------------------------------------------ גרסאות
@skip_no_db
async def test_superseded_document_is_hidden_by_default(client):
    """המסמך שפג תוקפו קיים במסד, אבל אינו מוחזר בברירת מחדל."""
    headers = auth(await token_for(client, "finance"))
    default = {d["doc_id"] for d in (await client.get("/documents", headers=headers)).json()}
    everything = {
        d["doc_id"]
        for d in (
            await client.get("/documents?include_superseded=true", headers=headers)
        ).json()
    }
    assert "FIN-001-OLD" not in default
    assert "FIN-001-OLD" in everything
