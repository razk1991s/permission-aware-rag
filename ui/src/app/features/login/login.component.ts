import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { AuthService } from '../../core/auth.service';

interface DemoUser {
  email: string;
  label: string;
  hint: string;
}

/**
 * מסך התחברות עם בורר משתמשי דמו.
 *
 * הבורר אינו קיצור דרך — הוא **הדמו עצמו**: אותה שאלה, שני משתמשים,
 * שתי תוצאות. בלי מעבר מהיר בין תפקידים, הדבר המעניין ביותר במערכת
 * דורש שלוש דקות של הקלדה כדי להראות.
 */
@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule],
  styles: [
    `
      .wrap {
        max-width: 460px;
        margin: 8vh auto 0;
        padding: 0 18px;
      }
      .users {
        display: grid;
        gap: 8px;
        margin: 14px 0 18px;
      }
      .user {
        display: flex;
        align-items: center;
        gap: 10px;
        text-align: start;
        background: var(--card-2);
        color: var(--ink);
        border: 1px solid transparent;
        padding: 10px 12px;
        border-radius: 8px;
        width: 100%;
      }
      .user:hover {
        border-color: var(--accent-line);
      }
      .user.on {
        border-color: var(--accent);
        background: var(--accent-soft);
      }
      .user .hint {
        color: var(--muted);
        font-size: 0.8rem;
        margin-inline-start: auto;
      }
    `,
  ],
  template: `
    <div class="wrap">
      <div class="card">
        <h1>Meridian — מוח ארגוני</h1>
        <p class="muted small">
          בחר משתמש דמו. אותה שאלה תיענה אחרת לכל תפקיד — זו הנקודה.
        </p>

        <div class="users">
          @for (u of demoUsers; track u.email) {
            <button
              type="button"
              class="user"
              [class.on]="email() === u.email"
              (click)="email.set(u.email)"
            >
              <span>{{ u.label }}</span>
              <span class="hint">{{ u.hint }}</span>
            </button>
          }
        </div>

        <label for="email">דוא״ל</label>
        <input id="email" [ngModel]="email()" (ngModelChange)="email.set($event)" autocomplete="username" />

        <label for="password" style="margin-top:10px">סיסמה</label>
        <input
          id="password"
          type="password"
          [ngModel]="password()"
          (ngModelChange)="password.set($event)"
          autocomplete="current-password"
          (keydown.enter)="submit()"
        />

        @if (auth.error(); as err) {
          <p class="error small" style="margin-top:12px">{{ err }}</p>
        }

        <button style="margin-top:16px;width:100%" [disabled]="auth.loading()" (click)="submit()">
          {{ auth.loading() ? 'מתחבר…' : 'התחבר' }}
        </button>
      </div>
    </div>
  `,
})
export class LoginComponent {
  readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  readonly demoUsers: DemoUser[] = [
    { email: 'yuval@meridian.local', label: 'יובל כהן', hint: 'כספים' },
    { email: 'dana@meridian.local', label: 'דנה לוי', hint: 'משאבי אנוש' },
    { email: 'maya@meridian.local', label: 'מאיה בר', hint: 'שירות לקוחות' },
    { email: 'ori@meridian.local', label: 'אורי שמש', hint: 'עובד' },
    { email: 'admin@meridian.local', label: 'רז', hint: 'מנהל מערכת' },
  ];

  readonly email = signal(this.demoUsers[0].email);
  readonly password = signal('Demo1234!');

  async submit(): Promise<void> {
    const ok = await this.auth.login(this.email(), this.password());
    if (ok) {
      const target = this.route.snapshot.queryParamMap.get('returnUrl') ?? '/chat';
      void this.router.navigateByUrl(target);
    }
  }
}
