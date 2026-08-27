# ============================================================================
# שלב 1: בניית ה-Angular
# ----------------------------------------------------------------------------
# הבנייה מופרדת לשלב משלו כדי ש-node, ה-npm cache ו-node_modules (מאות
# מגה) לא ייכנסו לתמונה הסופית. מה שעובר הלאה הוא רק תוצר הבנייה.
# ============================================================================
FROM node:22-slim AS ui

WORKDIR /ui

# קודם המניפסטים בלבד: כך שכבת ה-npm ci נשמרת ב-cache כל עוד התלויות
# לא השתנו, גם כשקוד המקור משתנה.
COPY ui/package.json ui/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY ui/ ./
RUN npm run build


# ============================================================================
# שלב 2: זמן ריצה — Python
# ============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "sentence-transformers>=3.3.0"

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

# תוצר ה-Angular נכנס בדיוק לאן ש-app/api/ui.py מחפש אותו.
COPY --from=ui /ui/dist/browser ./ui/dist/browser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
