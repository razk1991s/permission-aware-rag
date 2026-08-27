import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { TraceSummary } from '../../core/models';

@Component({
  selector: 'app-traces',
  standalone: true,
  imports: [RouterLink, FormsModule],
  template: `
    <div class="page">
      <div class="card">
        <div class="row" style="margin-bottom:10px">
          <div class="card__title" style="margin:0">טרייסים אחרונים</div>
          <span class="spacer"></span>
          <label class="small muted" style="display:flex;gap:6px;align-items:center;margin:0">
            <input
              type="checkbox"
              style="width:auto"
              [ngModel]="onlyRefused()"
              (ngModelChange)="toggleRefused($event)"
            />
            סירובים בלבד
          </label>
          <button class="subtle" (click)="load()">רענן</button>
        </div>

        @if (loading()) {
          <div class="empty">טוען…</div>
        } @else {
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>שאלה</th>
                  <th>מסלול</th>
                  <th>סטטוס</th>
                  <th class="num">ms</th>
                  <th class="num">טוקנים</th>
                  <th>מתי</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                @for (t of traces(); track t.trace_uuid) {
                  <tr>
                    <td>{{ t.question }}</td>
                    <td class="num">{{ t.route ?? '—' }}</td>
                    <td>
                      <span class="pill" [class]="t.refused ? 'pill--warn' : 'pill--ok'">
                        {{ t.stop_reason }}
                      </span>
                      @if (t.hallucination_flag) {
                        <span class="pill pill--bad">הזיה</span>
                      }
                    </td>
                    <td class="num">{{ t.latency_ms }}</td>
                    <td class="num">{{ t.prompt_tokens + t.completion_tokens }}</td>
                    <td class="small muted">{{ shortTime(t.created_at) }}</td>
                    <td><a [routerLink]="['/traces', t.trace_uuid]">פתח</a></td>
                  </tr>
                } @empty {
                  <tr>
                    <td colspan="7" class="empty">אין טרייסים עדיין. שאל שאלה בצ׳אט.</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }

        @if (error(); as e) {
          <p class="error small">{{ e }}</p>
        }
      </div>
    </div>
  `,
})
export class TracesComponent {
  private readonly api = inject(ApiService);

  readonly traces = signal<TraceSummary[]>([]);
  readonly loading = signal(false);
  readonly onlyRefused = signal(false);
  readonly error = signal<string | null>(null);

  constructor() {
    this.load();
  }

  toggleRefused(value: boolean): void {
    this.onlyRefused.set(value);
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.traces(50, this.onlyRefused()).subscribe({
      next: (rows) => {
        this.traces.set(rows);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? 'שגיאה בטעינת הטרייסים');
        this.loading.set(false);
      },
    });
  }

  shortTime(iso: string): string {
    return new Date(iso).toLocaleString('he-IL', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}
