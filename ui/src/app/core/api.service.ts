import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AgentAction,
  ChatResponse,
  ChunkView,
  DocumentSummary,
  Health,
  SearchResult,
  TierPreview,
  TraceDetail,
  TraceSummary,
} from './models';

/**
 * שכבת גישה אחת ל-API. כל קריאת רשת באפליקציה עוברת דרך כאן, כדי
 * שהוספת header, טיפול בשגיאות או שינוי base-url ייעשו במקום אחד.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api';

  // ---------------------------------------------------------- צ'אט
  chat(question: string, sessionId?: string): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(`${this.base}/chat`, {
      question,
      session_id: sessionId ?? null,
    });
  }

  search(query: string, topK = 5, hybrid = true, rerank = true): Observable<SearchResult> {
    return this.http.post<SearchResult>(`${this.base}/search`, {
      query,
      top_k: topK,
      hybrid,
      rerank,
    });
  }

  // ---------------------------------------------------------- מסמכים
  documents(domain?: string, includeSuperseded = false): Observable<DocumentSummary[]> {
    let params = new HttpParams().set('include_superseded', includeSuperseded);
    if (domain) params = params.set('domain', domain);
    return this.http.get<DocumentSummary[]>(`${this.base}/documents`, { params });
  }

  chunks(docId: string): Observable<ChunkView[]> {
    return this.http.get<ChunkView[]>(`${this.base}/documents/${encodeURIComponent(docId)}/chunks`);
  }

  // ---------------------------------------------------------- פעולות
  previewTier(amount: number): Observable<TierPreview> {
    return this.http.get<TierPreview>(`${this.base}/actions/preview`, {
      params: new HttpParams().set('amount', amount),
    });
  }

  createAction(actionType: string, payload: Record<string, unknown>): Observable<AgentAction> {
    return this.http.post<AgentAction>(`${this.base}/actions`, {
      action_type: actionType,
      payload,
    });
  }

  actions(status?: string, limit = 50): Observable<AgentAction[]> {
    let params = new HttpParams().set('limit', limit);
    if (status) params = params.set('status_filter', status);
    return this.http.get<AgentAction[]>(`${this.base}/actions`, { params });
  }

  decide(actionId: number, approve: boolean, note?: string): Observable<AgentAction> {
    return this.http.post<AgentAction>(`${this.base}/actions/${actionId}/decision`, {
      approve,
      note: note ?? null,
    });
  }

  // ---------------------------------------------------------- טרייסים
  traces(limit = 50, onlyRefused = false): Observable<TraceSummary[]> {
    return this.http.get<TraceSummary[]>(`${this.base}/traces`, {
      params: new HttpParams().set('limit', limit).set('only_refused', onlyRefused),
    });
  }

  trace(uuid: string): Observable<TraceDetail> {
    return this.http.get<TraceDetail>(`${this.base}/traces/${uuid}`);
  }

  health(): Observable<Health> {
    return this.http.get<Health>(`${this.base}/health`);
  }
}
