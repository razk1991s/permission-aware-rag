.PHONY: help up down logs dev install migrate ingest ingest-fast dry-run seed test fmt psql clean \
        ui-install ui-dev ui-build ui-test ui-lint

help:
	@echo "--- תשתית ---"
	@echo "up          — מרים PostgreSQL ו-Ollama"
	@echo "down        — עוצר את התשתית"
	@echo "install     — יוצר venv ומתקין תלויות"
	@echo "migrate     — מריץ מיגרציות"
	@echo "seed        — טוען משתמשי דמו ונתונים תפעוליים"
	@echo "demo        — טעינה מלאה מאפס: migrate + seed + ingest + index"
	@echo ""
	@echo "--- שרת ---"
	@echo "dev         — מריץ את ה-API עם reload על 8000"
	@echo "dry-run     — פרסור וחיתוך בלבד, בלי מסד נתונים"
	@echo "ingest-fast — טעינת הקורפוס בלי הטמעות (מהיר)"
	@echo "ingest      — טעינת הקורפוס עם הטמעות"
	@echo "test        — pytest"
	@echo ""
	@echo "--- ממשק (Angular) ---"
	@echo "ui-install  — npm ci בתיקיית ui/"
	@echo "ui-dev      — ng serve על 4200 עם proxy ל-8000"
	@echo "ui-build    — בונה ל-ui/dist; אחריו 'make dev' מגיש הכול מפורט אחד"
	@echo "ui-test     — בדיקות יחידה של Angular (headless Chromium)"
	@echo ""
	@echo "--- מדידה ---"
	@echo "eval        — חבילת ההערכה, כל הקונפיגורציות"
	@echo "eval-gate   — הערכה + שערי רגרסיה (יוצא עם שגיאה בחריגה)"
	@echo "injection   — חבילת ההזרקות"
	@echo "index       — בניית אינדקס HNSW (אחרי ingest)"
	@echo "mcp         — שרת MCP על stdio"

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
CORPUS ?= data/corpus
UI := ui

up:
	docker compose up -d
	@echo "ממתין למסד הנתונים..."
	@until docker compose exec -T db pg_isready -U rag -d ragdb >/dev/null 2>&1; do sleep 1; done
	@echo "מוריד מודלים ל-Ollama (בפעם הראשונה זה לוקח כמה דקות)..."
	-docker compose exec -T ollama ollama pull qwen2.5:7b-instruct
	-docker compose exec -T ollama ollama pull qwen2.5:3b-instruct

down:
	docker compose down

logs:
	docker compose logs -f

install:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt
	@echo "להטמעות:  $(PIP) install 'sentence-transformers>=3.3.0'"

dev:
	$(VENV)/bin/uvicorn app.main:app --reload --port 8000

migrate:
	$(PY) scripts/migrate.py

seed:
	psql "$${PSQL_URL:-postgresql://rag:ragpass@localhost:5432/ragdb}" -f $(CORPUS)/data/seed/seed_auth.sql
	psql "$${PSQL_URL:-postgresql://rag:ragpass@localhost:5432/ragdb}" -f $(CORPUS)/data/seed/seed_operational.sql

dry-run:
	$(PY) scripts/ingest_corpus.py --corpus $(CORPUS) --dry-run

ingest-fast:
	$(PY) scripts/ingest_corpus.py --corpus $(CORPUS) --skip-embeddings

ingest:
	$(PY) scripts/ingest_corpus.py --corpus $(CORPUS)

test:
	$(VENV)/bin/pytest -q -p no:warnings

eval:
	$(PY) scripts/run_eval.py

eval-gate:
	$(PY) scripts/run_eval.py --config v5-full --gate

injection:
	$(PY) scripts/run_injection.py

index:
	psql "$${PSQL_URL:-postgresql://rag:ragpass@localhost:5432/ragdb}" -f scripts/build_vector_index.sql

mcp:
	$(PY) -m mcp_server.server

# ---------------------------------------------------------------- ממשק
# npm ci ולא npm install: הבנייה חייבת להיות זהה בין מכונה למכונה,
# ו-package-lock.json הוא מה שקובע. אם אין lock, נופלים ל-install.
ui-install:
	cd $(UI) && (test -f package-lock.json && npm ci || npm install)

ui-dev:
	cd $(UI) && npm start

ui-build:
	cd $(UI) && npm run build
	@echo "נבנה ל-$(UI)/dist/browser — 'make dev' יגיש אותו מ-http://localhost:8000"

ui-test:
	cd $(UI) && CHROME_BIN=$${CHROME_BIN:-/opt/pw-browsers/chromium} npm test -- --watch=false --browsers=ChromeHeadlessNoSandbox

ui-lint:
	cd $(UI) && npx tsc --noEmit -p tsconfig.app.json

demo: migrate seed ingest index
	@echo "מוכן. הרץ 'make ui-build && make dev' ופתח http://localhost:8000"
	@echo "לפיתוח ממשק: 'make dev' בטרמינל אחד, 'make ui-dev' בשני."

psql:
	psql "$${PSQL_URL:-postgresql://rag:ragpass@localhost:5432/ragdb}"

clean:
	rm -rf $(VENV) .pytest_cache **/__pycache__
