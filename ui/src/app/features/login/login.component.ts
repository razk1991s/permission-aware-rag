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
 * Login screen with a demo-user selector.
 *
 * The selector is the demo itself: ask the same question as two users and
 * compare the results without manually entering credentials.
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
        <h1>Meridian - Enterprise Knowledge</h1>
        <p class="muted small">
          Choose a demo user. The same question produces a role-specific answer.
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

        <label for="email">Email</label>
        <input id="email" [ngModel]="email()" (ngModelChange)="email.set($event)" autocomplete="username" />

        <label for="password" style="margin-top:10px">Password</label>
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
          {{ auth.loading() ? 'Signing in…' : 'Sign in' }}
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
    { email: 'yuval@meridian.local', label: 'Yuval Cohen', hint: 'Finance' },
    { email: 'dana@meridian.local', label: 'Dana Levi', hint: 'Human Resources' },
    { email: 'maya@meridian.local', label: 'Maya Bar', hint: 'Customer Service' },
    { email: 'ori@meridian.local', label: 'Ori Shemesh', hint: 'Employee' },
    { email: 'admin@meridian.local', label: 'Raz', hint: 'Administrator' },
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
