import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { ActionStatus, AgentAction, TierPreview } from '../../core/models';

@Component({
  selector: 'app-approvals',
  standalone: true,
  imports: [FormsModule],
  styles: [
    `
      .preview {
        margin-top: 12px;
        padding: 12px 14px;
        border-radius: 8px;
        background: var(--card-2);
        font-size: 0.88rem;
        border-inline-start: 3px solid var(--accent);
      }
      .preview.ceiling {
        border-inline-start-color: var(--bad);
      }
      .preview.fallback {
        border-inline-start-color: var(--warn);
      }
      .cite {
        font-family: var(--mono);
        font-size: 0.82rem;
      }
      .actions-cell {
        display: flex;
        gap: 6px;
      }
      .payload {
        font-family: var(--mono);
        font-size: 0.78rem;
        color: var(--muted);
      }
    `,
  ],
  template: `
    <div class="page">
      <!-- ============ בקשת פעולה ============ -->
      <div class="card">
        <div class="card__title">בקשת זיכוי</div>

        <div class="row">
          <div class="grow">
            <label for="cust">שם הלקוח</label>
            <input id="cust" [ngModel]="customer()" (ngModelChange)="customer.set($event)" />
          </div>
          <div class="grow">
            <label for="amt">סכום (₪)</label>
            <input
              id="amt"
              type="number"
              [ngModel]="amount()"
              (ngModelChange)="onAmount($event)"
            />
          </div>
          <div class="grow">
            <label for="reason">סיבה</label>
            <input id="reason" [ngModel]="reason()" (ngModelChange)="reason.set($event)" />
          </div>
        </div>

        <div class="row" style="margin-top:12px">
          <button class="ghost" (click)="preview()">מי יאשר?</button>
          <button [disabled]="creating()" (click)="create()">
            {{ creating() ? 'פותח…' : 'פתח בקשה' }}
          </button>
          @if (formError(); as e) {
            <span class="error small">{{ e }}</span>
          }
        </div>

        @if (tier(); as t) {
          <div class="preview" [class.ceiling]="t.source === 'hard_ceiling'" [class.fallback]="t.source === 'fallback'">
            נדרש אישור <b>{{ tierLabel(t.tier) }}</b> (תפקיד <code>{{ t.required_role }}</code>)
            @if (t.auto_approved_for_you) {
              <span class="pill pill--ok">בסמכותך — יבוצע מיד</span>
            }
            <div class="muted" style="margin-top:6px">
              <span class="cite">{{ t.policy_citation }}</span> — {{ t.reason }}
            </div>
            <div class="small muted" style="margin-top:4px">
              @switch (t.source) {
                @case ('document') { הסף נקרא ממסמך הנוהל, לא מקודד בקוד. }
                @case ('hard_ceiling') { תקרה קשיחה גברה על הסף שבמסמך — השליפה יכולה רק להחמיר. }
                @case ('fallback') { שליפת הנוהל נכשלה. הופעלו ספי ברירת מחדל שמרניים. }
                @default { }
              }
            </div>
          </div>
        }
      </div>

      <!-- ============ רשימת בקשות ============ -->
      <div class="card">
        <div class="row" style="margin-bottom:10px">
          <div class="card__title" style="margin:0">בקשות</div>
          <span class="spacer"></span>
          <select style="width:auto" [ngModel]="filter()" (ngModelChange)="setFilter($event)">
            <option value="">הכול</option>
            <option value="pending_approval">ממתינות</option>
            <option value="completed">הושלמו</option>
            <option value="rejected">נדחו</option>
            <option value="blocked">נחסמו</option>
          </select>
          <button class="subtle" (click)="load()">רענן</button>
        </div>

        @if (loading()) {
          <div class="empty">טוען…</div>
        } @else {
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th class="num">#</th>
                  <th>פרטים</th>
                  <th>סטטוס</th>
                  <th>נדרש</th>
                  <th>נימוק מהנוהל</th>
                  <th>מבקש</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                @for (a of actions(); track a.id) {
                  <tr>
                    <td class="num">{{ a.id }}</td>
                    <td>
                      {{ a.action_type }}
                      <div class="payload">{{ describe(a) }}</div>
                    </td>
                    <td>
                      <span class="pill" [class]="statusClass(a.status)">{{ statusLabel(a.status) }}</span>
                    </td>
                    <td class="num">{{ a.required_role ?? '—' }}</td>
                    <td class="small muted">{{ a.policy_citation ?? '—' }}</td>
                    <td class="small muted">{{ a.requested_by_email ?? '—' }}</td>
                    <td>
                      @if (a.status === 'pending_approval') {
                        <div class="actions-cell">
                          <button (click)="decide(a, true)">אשר</button>
                          <button class="ghost" (click)="decide(a, false)">דחה</button>
                        </div>
                      } @else if (a.approved_by_email) {
                        <span class="small muted">{{ a.approved_by_email }}</span>
                      }
                    </td>
                  </tr>
                } @empty {
                  <tr>
                    <td colspan="7" class="empty">אין בקשות להצגה.</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }

        @if (listError(); as e) {
          <p class="error small">{{ e }}</p>
        }
      </div>
    </div>
  `,
})
export class ApprovalsComponent {
  private readonly api = inject(ApiService);
  readonly auth = inject(AuthService);

  readonly customer = signal('');
  readonly amount = signal(4200);
  readonly reason = signal('חיוב כפול');

  readonly tier = signal<TierPreview | null>(null);
  readonly actions = signal<AgentAction[]>([]);
  readonly filter = signal('');
  readonly loading = signal(false);
  readonly creating = signal(false);
  readonly formError = signal<string | null>(null);
  readonly listError = signal<string | null>(null);

  readonly pendingCount = computed(
    () => this.actions().filter((a) => a.status === 'pending_approval').length,
  );

  constructor() {
    this.load();
  }

  /** תצוגה מקדימה מתבטלת ברגע שהסכום משתנה — אחרת היא מטעה. */
  onAmount(value: number): void {
    this.amount.set(Number(value) || 0);
    this.tier.set(null);
  }

  setFilter(value: string): void {
    this.filter.set(value);
    this.load();
  }

  preview(): void {
    this.formError.set(null);
    this.api.previewTier(this.amount()).subscribe({
      next: (t) => this.tier.set(t),
      error: (e) => this.formError.set(this.detail(e)),
    });
  }

  create(): void {
    if (!this.customer().trim()) {
      this.formError.set('חובה לציין שם לקוח');
      return;
    }
    this.creating.set(true);
    this.formError.set(null);
    this.api
      .createAction('create_refund', {
        customer_name: this.customer().trim(),
        amount: this.amount(),
        reason: this.reason(),
      })
      .subscribe({
        next: () => {
          this.creating.set(false);
          this.load();
        },
        error: (e) => {
          this.creating.set(false);
          this.formError.set(this.detail(e));
        },
      });
  }

  decide(action: AgentAction, approve: boolean): void {
    this.listError.set(null);
    this.api.decide(action.id, approve, approve ? 'אושר מהממשק' : 'נדחה מהממשק').subscribe({
      next: () => this.load(),
      error: (e) => this.listError.set(this.detail(e)),
    });
  }

  load(): void {
    this.loading.set(true);
    this.listError.set(null);
    this.api.actions(this.filter() || undefined).subscribe({
      next: (rows) => {
        this.actions.set(rows);
        this.loading.set(false);
      },
      error: (e) => {
        this.listError.set(this.detail(e));
        this.loading.set(false);
      },
    });
  }

  describe(a: AgentAction): string {
    const p = a.payload as { customer_name?: string; amount?: number; reason?: string };
    return [p.customer_name, p.amount ? `${p.amount} ₪` : null, p.reason]
      .filter(Boolean)
      .join(' · ');
  }

  tierLabel(tier: string): string {
    return (
      { representative: 'נציג מוקד', team_lead: 'מנהל צוות', committee: 'ועדת זיכויים' }[tier] ??
      tier
    );
  }

  statusLabel(status: ActionStatus): string {
    return (
      {
        completed: 'בוצע',
        pending_approval: 'ממתין לאישור',
        blocked: 'נחסם',
        rejected: 'נדחה',
        recommended: 'הומלץ',
        failed: 'נכשל',
      }[status] ?? status
    );
  }

  statusClass(status: ActionStatus): string {
    if (status === 'completed') return 'pill--ok';
    if (status === 'pending_approval' || status === 'recommended') return 'pill--warn';
    return 'pill--bad';
  }

  private detail(err: unknown): string {
    const e = err as { error?: { detail?: string }; message?: string };
    return e?.error?.detail ?? e?.message ?? 'שגיאה לא ידועה';
  }
}
