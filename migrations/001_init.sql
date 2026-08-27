-- 001_init.sql — סכמת הבסיס של הפלטפורמה
-- כל האובייקטים נוצרים עם IF NOT EXISTS כדי שהמיגרציה תהיה idempotent.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =====================================================================
-- זהות והרשאות
-- =====================================================================
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS roles (
    id   BIGSERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- =====================================================================
-- מסמכים
-- =====================================================================
CREATE TABLE IF NOT EXISTS documents (
    id             BIGSERIAL PRIMARY KEY,
    doc_id         TEXT UNIQUE NOT NULL,          -- המזהה העסקי: FIN-001
    title          TEXT NOT NULL,
    source_path    TEXT NOT NULL,
    file_type      TEXT NOT NULL CHECK (file_type IN ('pdf','docx','xlsx','html','md')),
    domain         TEXT NOT NULL,                 -- finance | hr | public
    doc_type       TEXT,
    language       TEXT NOT NULL DEFAULT 'he',
    version        TEXT,
    effective_from DATE,
    effective_to   DATE,
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','superseded','archived')),
    checksum       TEXT NOT NULL,                 -- SHA-256 — מונע אינג'סט כפול
    page_count     INT,
    chunk_count    INT NOT NULL DEFAULT 0,
    meta           JSONB NOT NULL DEFAULT '{}'::jsonb,
    uploaded_by    BIGINT REFERENCES users(id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_domain_status ON documents (domain, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_checksum ON documents (checksum);

-- ה-ACL. ראה ADR 0002: זו נקודת האמת היחידה לבקרת גישה.
CREATE TABLE IF NOT EXISTS document_acl (
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    role_id     BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission  TEXT NOT NULL DEFAULT 'read' CHECK (permission IN ('read','write')),
    PRIMARY KEY (document_id, role_id, permission)
);

CREATE INDEX IF NOT EXISTS idx_document_acl_role ON document_acl (role_id);

-- =====================================================================
-- צ'אנקים
-- =====================================================================
CREATE TABLE IF NOT EXISTS chunks (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index  INT NOT NULL,
    content      TEXT NOT NULL,
    section_path TEXT,                            -- "4 › 4.2 חריגה ממועד הביצוע"
    page_number  INT,
    sheet_name   TEXT,
    row_number   INT,
    token_count  INT,
    strategy     TEXT,                            -- structure | row | qa
    embedding    VECTOR(1024),
    meta         JSONB NOT NULL DEFAULT '{}'::jsonb,
    tsv_simple   TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple',  content)) STORED,
    tsv_en       TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks (document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv_simple ON chunks USING GIN (tsv_simple);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv_en ON chunks USING GIN (tsv_en);
CREATE INDEX IF NOT EXISTS idx_chunks_trgm ON chunks USING GIN (content gin_trgm_ops);
-- אינדקס הווקטורים נבנה רק אחרי שיש נתונים (ראה scripts/build_vector_index.sql).

-- =====================================================================
-- נתונים תפעוליים — ה-DB שהסוכן יתשאל (שבוע 5)
-- =====================================================================
CREATE TABLE IF NOT EXISTS customers (
    id        BIGSERIAL PRIMARY KEY,
    full_name TEXT NOT NULL,
    segment   TEXT,
    joined_at DATE,
    status    TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS transactions (
    id          BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    amount      NUMERIC(12,2) NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'ILS',
    tx_type     TEXT,
    status      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transactions_customer ON transactions (customer_id);

CREATE TABLE IF NOT EXISTS refund_requests (
    id             BIGSERIAL PRIMARY KEY,
    customer_id    BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    transaction_id BIGINT REFERENCES transactions(id) ON DELETE SET NULL,
    amount         NUMERIC(12,2) NOT NULL,
    reason         TEXT,
    status         TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open','approved','rejected','paid')),
    opened_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ
);

-- אינדקס חלקי: כל שאילתות החריגה נוגעות רק בבקשות פתוחות.
CREATE INDEX IF NOT EXISTS idx_refunds_open_age
    ON refund_requests (opened_at) WHERE status = 'open';

-- =====================================================================
-- תצפיתיות וממשל (מתמלא משבוע 3 ואילך)
-- =====================================================================
CREATE TABLE IF NOT EXISTS traces (
    id                 BIGSERIAL PRIMARY KEY,
    trace_uuid         UUID UNIQUE NOT NULL,
    user_id            BIGINT REFERENCES users(id),
    session_id         UUID,
    question           TEXT NOT NULL,
    rewritten_queries  JSONB,
    route              TEXT,
    tools_called       JSONB,
    retrieved_chunks   JSONB,
    final_context_ids  BIGINT[],
    answer             TEXT,
    citations          JSONB,
    groundedness       NUMERIC(4,3),
    hallucination_flag BOOLEAN,
    refused            BOOLEAN NOT NULL DEFAULT FALSE,
    stop_reason        TEXT NOT NULL DEFAULT 'completed',
    prompt_tokens      INT,
    completion_tokens  INT,
    estimated_cost     NUMERIC(10,6) NOT NULL DEFAULT 0,
    latency_ms         INT,
    stage_latencies    JSONB,
    error              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_traces_created ON traces (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_user ON traces (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_actions (
    id              BIGSERIAL PRIMARY KEY,
    trace_uuid      UUID REFERENCES traces(trace_uuid) ON DELETE SET NULL,
    thread_id       TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending_approval'
                    CHECK (status IN ('completed','pending_approval','blocked',
                                      'rejected','recommended','failed')),
    requested_by    BIGINT REFERENCES users(id),
    required_role   TEXT,
    policy_citation TEXT,
    approved_by     BIGINT REFERENCES users(id),
    decided_at      TIMESTAMPTZ,
    decision_note   TEXT,
    result          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_actions_status ON agent_actions (status, created_at DESC);

-- append-only. אין לתת לתפקיד האפליקציה הרשאת UPDATE/DELETE כאן.
CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    actor_id   BIGINT,
    actor_type TEXT NOT NULL DEFAULT 'user' CHECK (actor_type IN ('user','agent','system')),
    action     TEXT NOT NULL,
    resource   TEXT,
    outcome    TEXT NOT NULL CHECK (outcome IN ('allowed','blocked','error')),
    detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_outcome ON audit_log (outcome, created_at DESC);

-- =====================================================================
-- הערכה (מתמלא בשבוע 6)
-- =====================================================================
CREATE TABLE IF NOT EXISTS eval_datasets (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_items (
    id                  BIGSERIAL PRIMARY KEY,
    dataset_id          BIGINT NOT NULL REFERENCES eval_datasets(id) ON DELETE CASCADE,
    question            TEXT NOT NULL,
    ground_truth_answer TEXT,
    relevant_chunk_ids  BIGINT[],
    as_role             TEXT,
    expected_refusal    BOOLEAN NOT NULL DEFAULT FALSE,
    category            TEXT
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id          BIGSERIAL PRIMARY KEY,
    dataset_id  BIGINT REFERENCES eval_datasets(id) ON DELETE SET NULL,
    config_name TEXT NOT NULL,
    config      JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_results (
    id            BIGSERIAL PRIMARY KEY,
    run_id        BIGINT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    item_id       BIGINT REFERENCES eval_items(id) ON DELETE SET NULL,
    answer        TEXT,
    retrieved_ids BIGINT[],
    scores        JSONB NOT NULL DEFAULT '{}'::jsonb,
    passed        BOOLEAN
);
