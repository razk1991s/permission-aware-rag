# Meridian — Enterprise Agentic RAG Platform

פלטפורמת ידע ארגונית שרצה כולה מקומית: שליפה היברידית עם דירוג מחדש, בקרת גישה ברמת מסמך שנאכפת ב-SQL, סוכן עם כלים מוגבלים, שערי אישור שנגזרים ממסמכי נוהל, והערכה אוטומטית עם שערי רגרסיה.

> **Built an enterprise-grade Agentic RAG platform (Python/FastAPI + Angular, self-hosted open models) that ingests heterogeneous documents, performs hybrid retrieval with cross-encoder reranking, and enforces document-level access control in SQL. The agent executes governed write actions behind policy-driven human approval gates, exposes its tools over MCP, and is hardened against indirect prompt injection. Ships with an automated evaluation harness wired to CI regression gates, plus request-level tracing and an LLM gateway with per-user quotas, model routing and audit trails.**

---

## הרצה

```bash
cp .env.example .env
docker compose up -d          # PostgreSQL + pgvector, Ollama
make install                  # venv + תלויות פייתון
make ui-install               # npm ci בתיקיית ui/
# פרוס את חבילת הקורפוס לתוך data/corpus  (הנתיב הצפוי: data/corpus/manifest.json)

make demo                     # migrate + seed + ingest + index
make ui-build                 # בונה את ה-Angular ל-ui/dist
make dev                      # http://localhost:8000 — API + ממשק מאותו origin
```

`make demo` לוקח כמה דקות בפעם הראשונה — bge-m3 שוקל כ-2.3GB. לבדיקה מהירה בלי מודלים:

```bash
LLM_PROVIDER=stub EMBEDDING_PROVIDER=stub ENVIRONMENT=test make demo test
```

**דרישת חומרה:** 16GB RAM לעבודה מלאה. `make dry-run` (פרסור וחיתוך בלבד) רץ על הרבה פחות.

**על ווינדוס:** אין `make`, ולכן יש `make.ps1` עם אותם יעדים בדיוק — `.\make.ps1 demo`. ההוראות המלאות ב-[docs/RUN-WINDOWS.md](docs/RUN-WINDOWS.md).

### שני מצבי הרצה

| | פיתוח | פרודקשן |
|---|---|---|
| הרצה | `make dev` + `make ui-dev` (שני טרמינלים) | `make ui-build` ואז `make dev` |
| כתובת | `http://localhost:4200` | `http://localhost:8000` |
| הגשת הממשק | `ng serve` עם HMR, proxy ל-8000 | FastAPI מגיש את `ui/dist/browser` |
| CORS | פעיל, רק ל-4200 | **אין** — origin אחד |

בשני המצבים ה-Angular קורא ל-`/api/...`, ולכן חוזה ה-frontend זהה בפיתוח ובפרודקשן. חלוקת מרחב השמות חדה בכוונה: **`/api/*` שייך לשרת, `/health` ו-`/stats` לבדיקות חיים, וכל השאר ל-Angular Router** — כך שרענון דף על `/traces/<uuid>` מחזיר את האפליקציה ולא 401. ראה [ADR 0010](docs/adr/0010-angular-spa-served-by-fastapi.md).

אם נכנסים ל-8000 בלי `make ui-build`, מתקבל דף שמסביר איזו פקודה חסרה — לא 404.

---

## שלוש דקות של דמו

פתח `http://localhost:8000` (או `:4200` בפיתוח). מסך ההתחברות מציע את משתמשי הדמו בלחיצה — **החלפת התפקיד היא הדמו**.

**1 · שאלת ידע** — כ־`יובל (כספים)`:
> תוך כמה ימי עסקים יש לבצע זיכוי?

תשובה עם ציטוטים כשבבים מתחתיה. „טרייס מלא ←“ פותח את צופה הטרייס: זמן לפי שלב, טבלת המועמדים, ועמודת **Δ** — כמה מקומות הרירנקר הזיז כל קטע. זו הראיה החזותית שהרכיב עושה משהו.

**2 · הרשאות** — אותה שאלה, שני משתמשים:
> מה טווח השכר של דרגה 7?

`דנה (משאבי אנוש)` מקבלת 18,500–24,300 ₪ עם ציטוט. `יובל (כספים)` מקבל סירוב. הפילטר רץ ב-SQL, לפני הדירוג — לא סינון בדיעבד. שים לב שגם רשימת המסמכים בלשונית „מסמכים“ משתנה: היא מסוננת באותה שאילתה, לא בקוד הממשק.

**3 · שער אישור** — לשונית „אישורים“, כ־`מאיה (שירות לקוחות)`:

בקש זיכוי של 4,200 ₪. לחיצה על „מי יאשר?“ מראה `team_lead` עם הנימוק `FIN-001 §5.2`. הסף **נקרא מהמסמך**, לא מקודד. הבקשה נשמרת כ־`pending_approval`; החלף ל-`יובל (כספים)` ואשר — הסטטוס עובר ל-`completed` ובקשת הזיכוי נוצרת בפועל.

**4 · שאלה משולבת** — כ־`יובל`:
> לפי נוהל הזיכויים, אילו לקוחות כרגע בחריגה?

הסוכן שולף את הנוהל, מחלץ את הסף (14), ומריץ שאילתה מהקטלוג עם המספר הזה. בטרייס רואים את שני הצעדים בנפרד.

**5 · כלי חסום** — כ־`מאיה`, שאל שאלה שדורשת נתוני שכר. הכלי נדחה, והדחייה מוצגת כשבב אזהרה מתחת לתשובה במקום להיעלם ללוג.

---

## ארכיטקטורה

```
                    ┌──────────────────────────────────┐
                    │  Angular 19 SPA                  │
                    │  צ׳אט · אישורים · טרייסים · מסמכים│
                    └────────────────┬─────────────────┘
                                     │ JWT · /api/*
                                     │ (אותו origin בפרודקשן)
                    ┌────────────────▼─────────────────┐
                    │            FastAPI               │
                    │  /api/auth /api/documents /api/chat│
                    │  /api/actions /api/traces  · /health│
                    │  + הגשת ui/dist/browser בשורש      │
                    └────────────────┬─────────────────┘
          ┌──────────────────────────┼──────────────────────────┐
   ┌──────▼───────┐        ┌─────────▼─────────┐       ┌────────▼────────┐
   │  Ingestion   │        │  LangGraph Agent  │       │   LLM Gateway   │
   │ parse·clean  │        │ understand·route  │       │ ניתוב·מכסות·PII │
   │ chunk·embed  │        │ retrieve·tools    │       │ fallback·עלות   │
   │ ACL          │        │ generate·validate │       └────────┬────────┘
   └──────┬───────┘        └─────────┬─────────┘                │
          └──────────────────────────┼──────────────────────────┘
                    ┌────────────────▼─────────────────┐
                    │        PostgreSQL 16             │
                    │ pgvector · tsvector · document_acl│
                    │ traces · agent_actions · audit_log│
                    └──────────────────────────────────┘
```

מסלול שאלה: `authz → understand → route → vector ‖ lexical → RRF → rerank → context → generate → validate`.

---

## מה מיוחד כאן

### 1 · ההרשאות נאכפות ב-SQL, לא באפליקציה

`allowed_doc_ids` נפתר פעם אחת מה-JWT, ונכנס ל-CTE של כל שאילתת שליפה. **הוא אינו קיים בסכמות הכלים** — הסוכן לא יכול להעביר אותו כי הוא לא רואה אותו.

```sql
WITH allowed AS (
    SELECT DISTINCT a.document_id FROM document_acl a
    JOIN user_roles ur ON ur.role_id = a.role_id
    WHERE ur.user_id = :user_id AND a.permission = 'read'
)
SELECT c.id, 1 - (c.embedding <=> :qvec) AS score
FROM chunks c JOIN documents d ON d.id = c.document_id
WHERE c.document_id IN (SELECT document_id FROM allowed) AND d.status = 'active'
ORDER BY c.embedding <=> :qvec LIMIT 30;
```

קטע שנשלף ואז נזרק כבר הודלף — ומעבר לכך, top-5 אחרי סינון אינו ה-top-5 של הקבוצה המותרת. ראה [ADR 0002](docs/adr/0002-authz-in-sql-not-post-filter.md).

### 2 · הסוכן לא כותב SQL

`query_database` מקבל **שם שאילתה** מקטלוג ופרמטרים מטופסים. פרמטר שאינו בסכמה נדחה, לא מתעלמים ממנו. שאילתה שאינה מותרת לתפקיד אינה מוצגת למודל בכלל. ראה [ADR 0005](docs/adr/0005-query-catalog-over-text-to-sql.md).

### 3 · ספי האישור נקראים מהנוהל

הסוכן שולף את `FIN-001 §5`, מחלץ את הספים ברמת משפט, ושומר את הציטוט עם הבקשה כדי שהמאשר יוכל לאמת. מעל `APPROVAL_HARD_CEILING` תמיד נדרשת ועדה — **השליפה יכולה רק להחמיר, לא להקל**. ראה [ADR 0006](docs/adr/0006-policy-driven-approval-gates.md).

### 4 · הגנת ההזרקות היא ארכיטקטונית, לא פרומפט

הפרומפט מפריד נתונים מהוראות, אבל זו השכבה החלשה. מה שבאמת מגן: ההרשאות מחוץ להישג ידו של המודל, אכיפה בעוטף הכלי, וסינון פלט דטרמיניסטי שמוודא שכל ציטוט מצביע על קטע שנשלח בפועל.

### 5 · המדדים הם שער merge, לא דוח

`permission_leak_rate` ו-`injection_success_rate` חייבים להיות אפס. `recall@5` לא יורד יותר מ-3 נקודות מול ה-baseline. השערים מוגדרים ב-`app/evaluation/gates.py` — **לפני** הקוד שהם בודקים.

ה-CI רץ מול ספק stub, ולכן שער שתלוי במה שהמודל בוחר לומר (סירובים, ציטוטים, הזיות) מסומן `requires_generation` ומדווח שם בלי לחסום. שערי האבטחה והשליפה חוסמים תמיד — `tests/test_gates.py` נועל את ההבחנה הזו, כי שער אבטחה שמפסיק לחסום „כי רצים על stub“ הוא הדרך שבה בדיקה נעלמת בשקט.

---

## הערכה

```bash
make eval                      # כל חמש הקונפיגורציות, טבלת השוואה
make eval-gate                 # + שערי רגרסיה, יוצא עם שגיאה בחריגה
make injection                 # 10 התקפות + 4 בקרות
python scripts/run_eval.py --save-baseline v5-full
```

הדאטהסט (`data/eval/dataset.json`) הוא 44 פריטים בשש קטגוריות: ידע, נתונים, משולב, הרשאות, גרסאות, ובלתי־ניתן־למענה. אמת המידה נגזרת מ-`FACTS.md` שבחבילת הקורפוס.

**הטבלה שממלאים לפני ראיון:**

| Config | Recall@5 | MRR | Correct | Leak |
|---|---|---|---|---|
| v1 — vector only | | | | |
| v2 — + hybrid RRF | | | | |
| v3 — + reranker | | | | |
| v4 — + multi-query | | | | |
| v5 — מלא | | | | |

**אל תמלא אותה משוער.** `make eval` ממלא אותה. אם v3 לא שיפר — זו תוצאה מעניינת יותר, והיא מה שמוכיח שמדדת.

⚠️ **הרצה עם `LLM_PROVIDER=stub` אינה מודדת איכות.** ה-runner יסרב לרוץ מולו בלי `--allow-stub`, ובאותה הרצה מודדים רק צנרת ואכיפה. ראה [ADR 0009](docs/adr/0009-deterministic-provider-for-ci.md).

---

## מבנה

```
enterprise-rag/
├── app/
│   ├── config.py · db.py · main.py
│   ├── core/          security · deps (זהות, הרשאות, audit)
│   ├── api/           auth · documents · chat · actions · traces · ui
│   ├── ingestion/     parsers · cleaning · chunking · embedder · pipeline
│   ├── retrieval/     search (וקטורי/לקסיקלי/RRF) · rerank · pipeline
│   ├── rag/           answer (ייצור, ציטוטים, ביסוס, סירוב)
│   ├── agent/         graph · state · tools · query_catalog · approval · actions
│   ├── security/      pii · prompt_guard
│   ├── llm/           base · providers (ollama, stub) · gateway
│   └── evaluation/    metrics · gates · runner
├── ui/                Angular 19 — standalone, signals, lazy routes
│   └── src/app/
│       ├── core/      models · api.service · auth.service · interceptor · guard
│       └── features/  login · chat · approvals · traces · documents
├── mcp_server/        שרת MCP על אותו מסלול authz
├── migrations/        001_init.sql — 16 טבלאות
├── scripts/           migrate · ingest_corpus · run_eval · run_injection · build_vector_index
├── tests/             121 בדיקות פייתון (+17 ב-Angular)
├── docs/adr/          10 החלטות ארכיטקטוניות
└── .github/workflows/ci.yml
```

---

## בדיקות

```bash
make test                                        # יחידה בלבד
DATABASE_URL=... LLM_PROVIDER=stub make test     # כולל אינטגרציה
make ui-test                                     # Angular, headless Chromium
```

121 בדיקות פייתון. השלוש החשובות:

- `test_no_role_can_read_a_chunk_outside_its_acl` — ארבעה תפקידים מול 14 מסמכים אסורים.
- `test_injection_marker_never_appears_in_an_answer` — הסמן מהמסמכים המורעלים לא יוצא החוצה.
- `test_tiers_are_parsed_per_sentence_not_per_chunk` — נכתבה בעקבות באג אמיתי (ראה למטה).

בדיקות האינטגרציה מדלגות אוטומטית ללא `DATABASE_URL`.

17 בדיקות Angular. השתיים ששוות קריאה הן ב-`api.service.spec.ts` וב-`auth.service.spec.ts`: הן מוודאות שהלקוח **לעולם אינו שולח** `user_id` או רשימת מסמכים מותרים. רשימת הרשאות שמגיעה מהלקוח היא רשימה שניתן לזייף — התפקידים בצד הלקוח משמשים להסתרת כפתורים בלבד, והשרת פותר הרשאות מחדש בכל בקשה.

---

## MCP

```bash
MCP_TOKEN=$(curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"yuval@meridian.local","password":"Demo1234!"}' | jq -r .access_token) make mcp
```

הזהות נגזרת מהטוקן, וההרשאות מחושבות מהמסד — אותו מסלול בדיוק כמו ב-API. שרת MCP שעוקף את ה-ACL הוא דלת אחורית לכל מה שהושקע בו.

---

## ארבעה באגים שנתפסו בבנייה

התיעוד הזה קיים כי הוא שווה יותר מהקוד עצמו:

**1 · חפיפה שנגררה מעבר לגבול סעיף.** הזנב של §3.2 נכנס לצ'אנק שנתיב הסעיף שלו כבר היה §4 — ציטוט היה מפנה לסעיף הלא נכון. נעול ב-`tests/test_section_integrity.py`.

**2 · ספי אישור שנותחו ברמת צ'אנק.** צ'אנק אחד מכיל את §5.1 ואת §5.2, והסכום הגדול שויך לדרג הראשון: נציג מוקד "הוסמך" ל-15,000 ₪. בנוסף, נוהל האשראי (75,000 ₪) נשלף יחד וזיהם את הספים. התיקון: ניתוח ברמת משפט + סינון לפי `doc_id`.

**3 · מדד שהגדיר את עצמו לא נכון.** `permission_leak_rate` ספר גם "לא סירב" וגם "דלף מידע" כאותו כשל. אלה דברים שונים: הראשון באג התנהגותי, השני כשל אבטחה. פוצל ל-`permission_leak_rate` ו-`missed_refusal_rate`.

**4 · פריטי הערכה שנכשלו בצדק הלא נכון.** פריט שאסר את המחרוזת "21 ימי עסקים" נכשל — כי הנוהל **שבתוקף** מזכיר את 21 כשהוא מסביר את השינוי. הבדיקה האמיתית היא שהמסמך שפג תוקפו לא נשלף, ולכן נוסף `forbidden_docs`.

---

## מגבלות, בכנות

- **איכות מודל 7B מקומי נמוכה ממודלים בגודל GPT-4**, במיוחד בעברית מורכבת. הפרויקט מדגים ארכיטקטורה ומדידה. המעבר ל-Azure OpenAI הוא שינוי קונפיג — בשביל זה קיים שער המודלים.
- **CI רץ מול ספק דטרמיניסטי**, ולכן איכות ניסוח לא מכוסה בכל commit ונמדדת nightly.
- **המכסות בזיכרון** ומתאפסות בהפעלה מחדש. בפרודקשן זו טבלה או Redis.
- **`interrupt()` של LangGraph אינו בשימוש** — ראה [ADR 0008](docs/adr/0008-own-approval-state-not-graph-checkpointer.md) להסבר ולמחיר.
- **פרמטרי HNSW לא כוילו.** הכיול נדחה עד שחבילת ההערכה תוכל למדוד את ההשפעה.
- **אין בדיקות E2E על הממשק.** בדיקות היחידה מכסות את שכבת השירותים; המסכים עצמם נבדקו ידנית. Playwright היה הצעד הבא.
- **`ui/dist` ישן יוגש בשקט.** אין בדיקת טריות מול קוד המקור; אחרי שינוי בממשק צריך `make ui-build`.

---

## הערות

- `JWT_SECRET` — האפליקציה מסרבת לעלות עם ברירת המחדל כאשר `ENVIRONMENT != dev`.
- `audit_log` היא append-only. אין לתת לתפקיד האפליקציה הרשאת `UPDATE`/`DELETE` עליה.
- כל התוכן ב-`data/corpus` בדוי. Meridian Credit היא חברה שאינה קיימת, וכל הנהלים והרגולציה נכתבו לצורך הדגמה טכנית בלבד.
