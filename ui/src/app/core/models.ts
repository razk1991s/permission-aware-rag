/**
 * חוזי ה-API, מטופסים.
 *
 * הטיפוסים כאן משקפים אחד לאחד את מודלי ה-Pydantic בצד השרת. כשחוזה
 * משתנה שם, הקומפילציה כאן נשברת — וזו בדיוק המטרה.
 */

export type Role = 'admin' | 'hr' | 'finance' | 'support' | 'employee';

export interface LoginResponse {
  access_token: string;
  token_type: string;
  roles: Role[];
}

export interface Me {
  id: number;
  email: string;
  display_name: string | null;
  roles: Role[];
  allowed_documents: number;
}

export interface Citation {
  marker: string;
  doc_id: string;
  title: string;
  section_path: string | null;
  page_number: number | null;
  chunk_id: number;
  score: number;
}

export interface ToolCall {
  tool: string;
  status: 'ok' | 'blocked' | 'failed' | 'empty';
  message: string | null;
  data: unknown;
  latency_ms: number;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  refused: boolean;
  refusal_reason: string | null;
  intent: string | null;
  tools_called: ToolCall[];
  groundedness: number | null;
  trace_uuid: string;
  latency_ms: number;
  stop_reason: string;
}

export interface DocumentSummary {
  doc_id: string;
  title: string;
  domain: string;
  doc_type: string | null;
  file_type: string;
  version: string | null;
  status: string;
  chunk_count: number;
}

export interface ChunkView {
  id: number;
  chunk_index: number;
  section_path: string | null;
  page_number: number | null;
  strategy: string | null;
  token_count: number | null;
  content: string;
}

export type ActionStatus =
  | 'completed'
  | 'pending_approval'
  | 'blocked'
  | 'rejected'
  | 'recommended'
  | 'failed';

export interface AgentAction {
  id: number;
  thread_id: string;
  action_type: string;
  payload: Record<string, unknown>;
  status: ActionStatus;
  required_role: string | null;
  policy_citation: string | null;
  decision_note: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  decided_at: string | null;
  requested_by_email: string | null;
  approved_by_email: string | null;
}

export interface TierPreview {
  amount: number;
  tier: string;
  required_role: string;
  policy_citation: string;
  reason: string;
  /** document = הסף נקרא מהנוהל · hard_ceiling = נכפתה תקרה · fallback = השליפה נכשלה */
  source: 'document' | 'hard_ceiling' | 'fallback' | 'stored';
  auto_approved_for_you: boolean;
}

export interface TraceSummary {
  trace_uuid: string;
  question: string;
  route: string | null;
  refused: boolean;
  stop_reason: string;
  groundedness: number | null;
  hallucination_flag: boolean | null;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  created_at: string;
  user_email: string | null;
}

export interface RetrievedChunk {
  chunk_id: number;
  doc_id: string;
  citation: string;
  vector_score: number | null;
  bm25_score: number | null;
  rrf: number | null;
  rerank_score: number | null;
  /** חיובי = הרירנקר העלה אותו במיקום, שלילי = הוריד */
  rerank_delta: number | null;
}

export interface TraceDetail extends TraceSummary {
  rewritten_queries: string[] | null;
  tools_called: ToolCall[] | null;
  retrieved_chunks: RetrievedChunk[] | null;
  answer: string | null;
  citations: Citation[] | null;
  stage_latencies: Record<string, number> | null;
  estimated_cost: number | null;
  error: string | null;
}

export interface SearchResult {
  query: string;
  rerank_model: string;
  candidates: (RetrievedChunk & { section_path: string | null; content: string })[];
  stage_latencies: Record<string, number>;
}

export interface Health {
  status: string;
  database: string;
  llm: string;
  provider: string;
  generation_model: string;
  embedding_provider: string;
  environment: string;
}
