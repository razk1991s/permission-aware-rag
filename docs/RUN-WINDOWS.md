# הרצה על ווינדוס

הפרויקט רץ על ווינדוס בלי WSL ובלי VM. הקוד עצמו נייטרלי — הוא משתמש ב-`pathlib` ואין בו אף סקריפט bash — ולכן שני הפערים היחידים הם `make`, שלא קיים בווינדוס, ו-`psql`, שדורש התקנה. שניהם נפתרים כאן: `make.ps1` מחליף את הראשון, ו-`psql` רץ בתוך הקונטיינר במקום על המחשב.

---

## 1 · מה להתקין

| כלי | למה | הערה |
|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | PostgreSQL + pgvector | מתקין WSL2 בעצמו כ-backend. אין צורך לפתוח אותו או להשתמש בו. |
| [Python 3.12](https://www.python.org/downloads/) | השרת | בהתקנה **סמן "Add python.exe to PATH"**. |
| [Node.js 22](https://nodejs.org/) | ממשק Angular | LTS. |
| [Ollama](https://ollama.com/download/windows) | מודלים מקומיים | נייטיב ולא בקונטיינר — כך הוא מקבל GPU. |
| [Git](https://git-scm.com/download/win) | — | |

**אל תריץ `docker compose up -d` ללא שם שירות.** זה מרים גם קונטיינר Ollama, שיתנגש עם ה-Ollama הנייטיב על פורט 11434. `.\make.ps1 up` מרים רק את מסד הנתונים, וזה מכוון.

---

## 2 · לאפשר הרצת סקריפטים

ווינדוס חוסם הרצת קובצי `.ps1` כברירת מחדל. פעם אחת, ב-PowerShell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

ובנוסף — קובץ שהגיע מתוך ZIP שהורדת מסומן כ"מהאינטרנט" ונחסם גם אחרי זה. לכן, בתיקיית הפרויקט:

```powershell
Unblock-File .\make.ps1
```

אם דילגת על השורה הזו תקבל `cannot be loaded because running scripts is disabled` ותחשוב שההגדרה הקודמת לא עבדה. היא כן עבדה — זה סימון אחר.

---

## 3 · הרצה ראשונה

```powershell
Copy-Item .env.example .env

.\make.ps1 install        # יוצר .venv ומתקין תלויות פייתון
.\make.ps1 ui-install     # npm ci בתיקיית ui
.\make.ps1 up             # מרים PostgreSQL ובודק שה-Ollama מוכן
.\make.ps1 models         # מוריד את מודלי qwen2.5 (כמה דקות)

# פרוס את חבילת הקורפוס לתוך data\corpus
# הנתיב הצפוי:  data\corpus\manifest.json

.\make.ps1 demo           # migrate + seed + ingest + index
.\make.ps1 ui-build       # בונה את ה-Angular
.\make.ps1 dev            # http://localhost:8000
```

להטמעות אמיתיות (bge-m3, כ-2.3GB) צריך גם:

```powershell
.\.venv\Scripts\python -m pip install "sentence-transformers>=3.3.0"
```

בלי זה השתמש ב-`ingest-fast`, שמדלג על ההטמעות.

---

## 4 · שני מצבי הרצה

**פרודקשן — שירות אחד, פורט אחד:**

```powershell
.\make.ps1 ui-build
.\make.ps1 dev            # http://localhost:8000 — API + ממשק מאותו origin
```

**פיתוח — שני טרמינלים, עם HMR:**

```powershell
# טרמינל 1
.\make.ps1 dev

# טרמינל 2
.\make.ps1 ui-dev         # http://localhost:4200
```

ב-4200 ה-`ng serve` מעביר כל `/api` ל-8000. זה המצב היחיד שבו CORS פעיל, ורק ל-4200.

---

## 5 · טבלת תרגום

| Linux | Windows |
|---|---|
| `make up` | `.\make.ps1 up` |
| `make install` | `.\make.ps1 install` |
| `make demo` | `.\make.ps1 demo` |
| `make dev` | `.\make.ps1 dev` |
| `make test` | `.\make.ps1 test` |
| `make eval` | `.\make.ps1 eval` |
| `make ui-build` | `.\make.ps1 ui-build` |
| `psql "postgresql://..."` | `.\make.ps1 psql` |
| — | `.\make.ps1 models` (אין מקבילה — בלינוקס זה חלק מ-`up`) |

`.\make.ps1 help` מדפיס את הרשימה המלאה.

---

## 6 · משתני סביבה ב-PowerShell

התחביר שונה מ-bash, וזו הטעות הנפוצה ביותר במעבר:

```powershell
# bash:      LLM_PROVIDER=stub make test
# PowerShell:
$env:LLM_PROVIDER = 'stub'
$env:EMBEDDING_PROVIDER = 'stub'
$env:ENVIRONMENT = 'test'
.\make.ps1 test
```

שים לב: הגדרה כזו **נשארת עד שתסגור את הטרמינל**. ב-bash הקידומת חלה על פקודה אחת בלבד. לניקוי:

```powershell
Remove-Item Env:LLM_PROVIDER, Env:EMBEDDING_PROVIDER, Env:ENVIRONMENT
```

**כדי להריץ גם את בדיקות האינטגרציה** צריך `DATABASE_URL` בסביבת התהליך — הבדיקות קוראות אותו ישירות ולא דרך `.env`, כדי שהן ידלגו על עצמן במכונה נקייה במקום להיכשל:

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://rag:ragpass@localhost:5432/ragdb'
.\make.ps1 test
```

בלי זה יעברו רק בדיקות היחידה, ותראה `skipped` ליד השאר. זה תקין.

---

## 7 · תקלות נפוצות

**`cannot be loaded because running scripts is disabled`**
סעיף 2. סביר שחסר `Unblock-File`.

**`python` לא נמצא, או פותח את חנות מיקרוסופט**
ווינדוס מגיע עם קיצור דרך מזויף ל-`python`. התקן פייתון אמיתי עם "Add to PATH", או כבה את הקיצור ב-Settings → Apps → Advanced app settings → App execution aliases.

**עברית מוצגת כג'יבריש בטרמינל**
השתמש ב-Windows Terminal ולא ב-`cmd.exe` הישן. הסקריפט כבר מגדיר UTF-8 בפלט.

**`docker compose cp` נכשל**
Docker Desktop לא רץ. פתח אותו וחכה שהאייקון יתייצב.

**הפורט 5432 תפוס**
כנראה יש לך SQL Server או פוסטגרס מותקן מקומית. שנה ב-`.env`:
`POSTGRES_PORT=5433` וגם `DATABASE_URL=postgresql+asyncpg://rag:ragpass@localhost:5433/ragdb`.

**`ng serve` לא מזהה שינויים בקבצים**
זה קורה רק כשהפרויקט יושב על כונן רשת או בתיקייה מסונכרנת (OneDrive). העבר אותו לתיקייה מקומית רגילה.

**Ollama איטי מאוד**
בדוק שהוא באמת משתמש ב-GPU: `ollama ps` בזמן שאילתה. אם לא, זו כנראה בעיית דרייבר NVIDIA. מודל 7B על CPU ייקח עשרות שניות לתשובה — לפיתוח ולבדיקות עדיף אז ספק `stub`.
