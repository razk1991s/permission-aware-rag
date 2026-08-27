-- אינדקס HNSW נבנה אחרי טעינת הנתונים, לא לפני.
-- בנייה על טבלה ריקה עובדת, אבל בנייה אחרי הטעינה מהירה יותר ומייצרת
-- גרף איכותי יותר. m ו-ef_construction הם ברירות המחדל של pgvector,
-- ולא כוונו — הכיול יידחה עד שחבילת ההערכה תוכל למדוד את ההשפעה.

SET maintenance_work_mem = '512MB';

CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

ANALYZE chunks;

-- ef_search משפיע על איכות השליפה בזמן ריצה (ברירת מחדל 40).
-- SET hnsw.ef_search = 100;
