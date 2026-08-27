import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { LoginResponse, Me, Role } from './models';

const TOKEN_KEY = 'meridian.token';

/**
 * Client identity state.
 *
 * Important: roles stored on the client are **display-only** -
 * They are used only to hide buttons and control navigation. Authorization
 * decisions never rely on them; the server resolves permissions from the
 * database on every request (see ADR 0002). Editing localStorage can change
 * only what the user sees, never what the server returns.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  private readonly _token = signal<string | null>(this.readStoredToken());
  private readonly _me = signal<Me | null>(null);
  private readonly _loading = signal(false);
  private readonly _error = signal<string | null>(null);

  readonly token = this._token.asReadonly();
  readonly me = this._me.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly error = this._error.asReadonly();

  readonly isAuthenticated = computed(() => this._token() !== null);
  readonly roles = computed<Role[]>(() => this._me()?.roles ?? []);
  readonly isAdmin = computed(() => this.roles().includes('admin'));

  hasAnyRole(...roles: Role[]): boolean {
    return this.roles().some((r) => roles.includes(r));
  }

  private readStoredToken(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      // If the browser blocks storage, continue in memory only.
      return null;
    }
  }

  private store(token: string | null): void {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* Storage is blocked; this is non-critical. */
    }
  }

  async login(email: string, password: string): Promise<boolean> {
    this._loading.set(true);
    this._error.set(null);
    try {
      const res = await firstValueFrom(
        this.http.post<LoginResponse>('/api/auth/login', { email, password }),
      );
      this._token.set(res.access_token);
      this.store(res.access_token);
      await this.refreshMe();
      return true;
    } catch {
      // Use the same message for every login failure to prevent account enumeration.
      this._error.set('Invalid login credentials');
      this.logout(false);
      return false;
    } finally {
      this._loading.set(false);
    }
  }

  async refreshMe(): Promise<void> {
    if (!this._token()) return;
    try {
      this._me.set(await firstValueFrom(this.http.get<Me>('/api/auth/me')));
    } catch {
      this.logout();
    }
  }

  logout(navigate = true): void {
    this._token.set(null);
    this._me.set(null);
    this.store(null);
    if (navigate) void this.router.navigate(['/login']);
  }
}
