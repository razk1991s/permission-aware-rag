import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService } from './core/auth.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  styles: [
    `
      header {
        background: var(--card);
        border-bottom: 1px solid var(--line);
        padding: 10px 18px;
        position: sticky;
        top: 0;
        z-index: 10;
      }
      .bar {
        max-width: 1040px;
        margin: 0 auto;
        display: flex;
        align-items: center;
        gap: 14px;
        flex-wrap: wrap;
      }
      .brand {
        font-weight: 600;
        letter-spacing: -0.01em;
        white-space: nowrap;
      }
      .brand small {
        color: var(--muted);
        font-weight: 400;
        margin-inline-start: 6px;
      }
      nav {
        display: flex;
        gap: 2px;
      }
      nav a {
        color: var(--muted);
        text-decoration: none;
        padding: 6px 11px;
        border-radius: 7px;
        font-size: 0.9rem;
        border: 1px solid transparent;
      }
      nav a:hover {
        background: var(--card-2);
        color: var(--ink);
      }
      nav a.active {
        background: var(--bg);
        border-color: var(--line);
        color: var(--ink);
        font-weight: 600;
      }
      .who {
        font-size: 0.82rem;
        color: var(--muted);
        display: flex;
        align-items: center;
        gap: 6px;
      }
    `,
  ],
  template: `
    <header>
      <div class="bar">
        <span class="brand">Meridian<small>מוח ארגוני</small></span>

        @if (auth.isAuthenticated()) {
          <nav>
            <a routerLink="/chat" routerLinkActive="active">צ׳אט</a>
            <a routerLink="/approvals" routerLinkActive="active">אישורים</a>
            <a routerLink="/traces" routerLinkActive="active">טרייסים</a>
            <a routerLink="/documents" routerLinkActive="active">מסמכים</a>
          </nav>

          <span class="spacer"></span>

          <span class="who">
            @for (role of auth.roles(); track role) {
              <span class="pill pill--accent">{{ role }}</span>
            }
            @if (auth.me(); as me) {
              <span>{{ me.allowed_documents }} מסמכים מורשים</span>
            }
          </span>
          <button class="ghost" (click)="auth.logout()">התנתק</button>
        }
      </div>
    </header>

    <router-outlet />
  `,
})
export class AppComponent {
  readonly auth = inject(AuthService);

  constructor() {
    // טוקן ששרד רענון דף — נטען מחדש את פרטי המשתמש, ואם הוא פג
    // ה-interceptor ינתק. אין כאן refresh token בכוונה.
    void this.auth.refreshMe();
  }
}
