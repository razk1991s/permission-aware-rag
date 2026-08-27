import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { ChatResponse } from '../../core/models';

interface Turn {
  question: string;
  response: ChatResponse | null;
  error: string | null;
}

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [FormsModule, RouterLink],
  styles: [
    `
      .suggestions {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        margin-top: 10px;
      }
      .suggestions button {
        background: none;
        border: 1px dashed var(--line);
        color: var(--muted);
        font-size: 0.82rem;
        padding: 5px 10px;
      }
      .suggestions button:hover {
        border-color: var(--accent-line);
        color: var(--accent);
      }
      .turn {
        border-inline-start: 3px solid var(--accent);
        border-radius: 0 var(--radius) var(--radius) 0;
      }
      .turn.refused {
        border-inline-start-color: var(--warn);
      }
      .turn.failed {
        border-inline-start-color: var(--bad);
      }
      .q {
        color: var(--muted);
        font-size: 0.85rem;
        margin-bottom: 6px;
      }
      .answer {
        white-space: pre-wrap;
      }
      .meta {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        align-items: center;
        margin-top: 12px;
        padding-top: 10px;
        border-top: 1px solid var(--line);
        font-size: 0.82rem;
      }
      .cite {
        display: inline-block;
        border: 1px solid var(--line);
        border-radius: 999px;
        padding: 2px 9px;
        font-size: 0.78rem;
        color: var(--muted);
      }
    `,
  ],
  template: `
    <div class="page">
      <div class="card">
        <div class="row">
          <input
            class="grow"
            [ngModel]="question()"
            (ngModelChange)="question.set($event)"
            (keydown.enter)="ask()"
            placeholder="לדוגמה: לפי נוהל הזיכויים, אילו לקוחות כרגע בחריגה?"
            aria-label="שאלה"
          />
          <button [disabled]="pending() || !question().trim()" (click)="ask()">
            {{ pending() ? 'חושב…' : 'שאל' }}
          </button>
        </div>

        <div class="suggestions">
          @for (s of suggestions; track s) {
            <button type="button" (click)="question.set(s); ask()">{{ s }}</button>
          }
        </div>
      </div>

      <div class="stack" style="margin-top:14px">
        @for (turn of turns(); track $index) {
          <div
            class="card turn fade-in"
            [class.refused]="turn.response?.refused"
            [class.failed]="turn.error"
          >
            <div class="q">{{ turn.question }}</div>

            @if (turn.response; as r) {
              <div class="answer">{{ r.answer }}</div>

              @if (r.citations.length) {
                <div style="margin-top:10px">
                  @for (c of r.citations; track c.chunk_id) {
                    <span class="cite" [title]="'ציון ' + c.score">
                      {{ c.marker }} · {{ c.title }}
                      @if (c.section_path) {
                        › {{ lastSection(c.section_path) }}
                      }
                    </span>
                  }
                </div>
              }

              @for (t of blockedTools(r); track t.tool) {
                <div class="small" style="margin-top:8px">
                  <span class="pill pill--warn">{{ t.tool }}</span>
                  {{ t.message }}
                </div>
              }

              <div class="meta">
                <span class="pill pill--accent">{{ r.intent ?? '—' }}</span>
                <span class="pill" [class]="r.refused ? 'pill--warn' : 'pill--ok'">
                  {{ r.refused ? 'סירוב' : 'נענה' }}
                </span>
                @if (r.stop_reason !== 'completed') {
                  <span class="pill pill--muted">{{ r.stop_reason }}</span>
                }
                @if (r.groundedness !== null) {
                  <span class="pill pill--muted">ביסוס {{ r.groundedness }}</span>
                }
                <span class="pill pill--muted">{{ r.latency_ms }} ms</span>
                <span class="spacer"></span>
                <a [routerLink]="['/traces', r.trace_uuid]">טרייס מלא ←</a>
              </div>

              @if (r.refused && r.refusal_reason) {
                <div class="small muted" style="margin-top:6px">סיבה: {{ r.refusal_reason }}</div>
              }
            } @else if (turn.error) {
              <div class="error">{{ turn.error }}</div>
            }
          </div>
        } @empty {
          <div class="empty">
            שאל שאלה כדי להתחיל. נסה את אותה שאלה בשני תפקידים שונים.
          </div>
        }
      </div>
    </div>
  `,
})
export class ChatComponent {
  private readonly api = inject(ApiService);
  readonly auth = inject(AuthService);

  readonly question = signal('');
  readonly pending = signal(false);
  readonly turns = signal<Turn[]>([]);

  readonly suggestions = [
    'תוך כמה ימי עסקים יש לבצע זיכוי?',
    'מה טווח השכר של דרגה 7?',
    'לפי נוהל הזיכויים, אילו לקוחות כרגע בחריגה?',
    'מהי ריבית הפיגורים?',
  ];

  lastSection(path: string): string {
    return path.split(' › ').pop() ?? path;
  }

  blockedTools(r: ChatResponse) {
    return r.tools_called.filter((t) => t.status !== 'ok');
  }

  ask(): void {
    const q = this.question().trim();
    if (!q || this.pending()) return;

    this.pending.set(true);
    this.question.set('');

    this.api.chat(q).subscribe({
      next: (response) => {
        this.turns.update((t) => [{ question: q, response, error: null }, ...t]);
        this.pending.set(false);
      },
      error: (err) => {
        const detail = err?.error?.detail ?? err?.message ?? 'שגיאה לא ידועה';
        this.turns.update((t) => [{ question: q, response: null, error: detail }, ...t]);
        this.pending.set(false);
      },
    });
  }
}
