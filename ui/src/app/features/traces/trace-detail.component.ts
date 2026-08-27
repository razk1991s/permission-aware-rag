import { Component, computed, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { RetrievedChunk, TraceDetail } from '../../core/models';

/**
 * צופה הטרייס — המסך שמראה שהמערכת אינה קופסה שחורה.
 *
 * העמודה שהכי שווה להסתכל עליה היא Δ: כמה מקומות הרירנקר הזיז כל קטע.
 * זו הוכחה חזותית שהרכיב עושה משהו, ובראיון היא שווה יותר מהסבר על
 * ההבדל בין bi-encoder ל-cross-encoder.
 */
@Component({
  selector: 'app-trace-detail',
  standalone: true,
  imports: [RouterLink],
  styles: [
    `
      .stage {
        display: flex;
        align-items: center;
        gap: 10px;
        font-family: var(--mono);
        font-size: 12px;
        margin: 4px 0;
      }
      .stage .name {
        flex: 0 0 140px;
        color: var(--muted);
      }
      .stage .bar {
        height: 8px;
        border-radius: 2px;
        background: var(--accent);
        opacity: 0.75;
        min-width: 2px;
      }
      .delta-up {
        color: var(--ok);
        font-weight: 600;
      }
      .delta-down {
        color: var(--bad);
      }
      .answer {
        white-space: pre-wrap;
        background: var(--bg);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px 14px;
      }
      .kv {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        margin: 8px 0 0;
      }
    `,
  ],
  template: `
    <div class="page">
      <a routerLink="/traces" class="small">← חזרה לרשימה</a>

      @if (loading()) {
        <div class="empty">טוען…</div>
      } @else if (error()) {
        <div class="card"><p class="error">{{ error() }}</p></div>
      }

      @if (trace(); as t) {
        <div class="card" style="margin-top:12px">
          <h2>{{ t.question }}</h2>
          <div class="kv">
            <span class="pill pill--accent">{{ t.route ?? '—' }}</span>
            <span class="pill" [class]="t.refused ? 'pill--warn' : 'pill--ok'">{{ t.stop_reason }}</span>
            <span class="pill pill--muted">{{ t.latency_ms }} ms</span>
            <span class="pill pill--muted">{{ t.prompt_tokens + t.completion_tokens }} טוקנים</span>
            @if (t.groundedness !== null) {
              <span class="pill pill--muted">ביסוס {{ t.groundedness }}</span>
            }
            @if (t.user_email) {
              <span class="pill pill--muted">{{ t.user_email }}</span>
            }
          </div>

          @if (t.rewritten_queries?.length) {
            <div class="small muted" style="margin-top:10px">
              ניסוחים שנוצרו:
              @for (q of t.rewritten_queries; track q) {
                <span class="pill pill--muted">{{ q }}</span>
              }
            </div>
          }
        </div>

        <!-- ============ זמני שלבים ============ -->
        <div class="card">
          <div class="card__title">זמן לפי שלב</div>
          @for (s of stages(); track s.name) {
            <div class="stage">
              <span class="name">{{ s.name }}</span>
              <span class="bar" [style.width.px]="s.width"></span>
              <span>{{ s.ms }} ms</span>
            </div>
          } @empty {
            <div class="empty small">אין נתוני שלבים.</div>
          }
        </div>

        <!-- ============ מועמדים ============ -->
        <div class="card">
          <div class="card__title">
            מועמדים וציונים
            <span class="small muted">— Δ הוא כמה מקומות הרירנקר הזיז את הקטע</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th class="num">#</th>
                  <th>מקור</th>
                  <th class="num">וקטור</th>
                  <th class="num">BM25</th>
                  <th class="num">RRF</th>
                  <th class="num">rerank</th>
                  <th class="num">Δ</th>
                </tr>
              </thead>
              <tbody>
                @for (c of candidates(); track c.chunk_id; let i = $index) {
                  <tr>
                    <td class="num">{{ i + 1 }}</td>
                    <td class="small">{{ c.citation }}</td>
                    <td class="num">{{ fmt(c.vector_score) }}</td>
                    <td class="num">{{ fmt(c.bm25_score) }}</td>
                    <td class="num">{{ fmt(c.rrf) }}</td>
                    <td class="num">{{ fmt(c.rerank_score) }}</td>
                    <td class="num" [class.delta-up]="up(c)" [class.delta-down]="down(c)">
                      {{ delta(c) }}
                    </td>
                  </tr>
                } @empty {
                  <tr><td colspan="7" class="empty">לא נשלפו מועמדים.</td></tr>
                }
              </tbody>
            </table>
          </div>
        </div>

        <!-- ============ כלים ============ -->
        @if (t.tools_called?.length) {
          <div class="card">
            <div class="card__title">כלים שהופעלו</div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr><th>כלי</th><th>סטטוס</th><th>הודעה</th><th class="num">ms</th></tr>
                </thead>
                <tbody>
                  @for (tool of t.tools_called; track $index) {
                    <tr>
                      <td class="num">{{ tool.tool }}</td>
                      <td>
                        <span class="pill" [class]="tool.status === 'ok' ? 'pill--ok' : 'pill--warn'">
                          {{ tool.status }}
                        </span>
                      </td>
                      <td class="small muted">{{ tool.message ?? '—' }}</td>
                      <td class="num">{{ tool.latency_ms }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </div>
        }

        <!-- ============ תשובה ============ -->
        <div class="card">
          <div class="card__title">התשובה</div>
          <div class="answer">{{ t.answer ?? '—' }}</div>
          @if (t.error) {
            <p class="error small" style="margin-top:10px">{{ t.error }}</p>
          }
        </div>
      }
    </div>
  `,
})
export class TraceDetailComponent {
  private readonly api = inject(ApiService);

  /** מגיע מפרמטר הנתיב דרך withComponentInputBinding() */
  readonly id = input.required<string>();

  readonly trace = signal<TraceDetail | null>(null);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  readonly candidates = computed(() => this.trace()?.retrieved_chunks ?? []);

  readonly stages = computed(() => {
    const raw = this.trace()?.stage_latencies ?? {};
    const entries = Object.entries(raw);
    const max = Math.max(1, ...entries.map(([, v]) => v));
    return entries.map(([name, ms]) => ({
      name,
      ms,
      width: Math.max(2, Math.round((ms / max) * 300)),
    }));
  });

  constructor() {
    // input() זמין כבר בבנאי כשהניתוב מזין אותו
    queueMicrotask(() => this.load());
  }

  private load(): void {
    this.api.trace(this.id()).subscribe({
      next: (t) => {
        this.trace.set(t);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? 'הטרייס לא נמצא');
        this.loading.set(false);
      },
    });
  }

  fmt(value: number | null): string {
    return value === null || value === undefined ? '—' : String(value);
  }

  delta(c: RetrievedChunk): string {
    if (!c.rerank_delta) return '—';
    return c.rerank_delta > 0 ? `↑${c.rerank_delta}` : `↓${-c.rerank_delta}`;
  }

  up(c: RetrievedChunk): boolean {
    return (c.rerank_delta ?? 0) > 0;
  }

  down(c: RetrievedChunk): boolean {
    return (c.rerank_delta ?? 0) < 0;
  }
}
