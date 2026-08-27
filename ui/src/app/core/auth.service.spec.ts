import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router } from '@angular/router';

import { AuthService } from './auth.service';

describe('AuthService', () => {
  let service: AuthService;
  let http: HttpTestingController;
  let router: jasmine.SpyObj<Router>;

  beforeEach(() => {
    localStorage.clear();
    router = jasmine.createSpyObj<Router>('Router', ['navigate']);

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: Router, useValue: router },
      ],
    });

    service = TestBed.inject(AuthService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    localStorage.clear();
  });

  it('starts unauthenticated when no token is stored', () => {
    expect(service.isAuthenticated()).toBeFalse();
    expect(service.roles()).toEqual([]);
  });

  it('stores the token and loads the profile on successful login', async () => {
    const pending = service.login('dana@meridian.local', 'demo');

    http.expectOne('/api/auth/login').flush({ access_token: 'tok-1', token_type: 'bearer' });
    await Promise.resolve();
    http.expectOne('/api/auth/me').flush({
      user_id: 3,
      email: 'dana@meridian.local',
      display_name: 'דנה — משאבי אנוש',
      roles: ['hr', 'employee'],
    });

    expect(await pending).toBeTrue();
    expect(service.isAuthenticated()).toBeTrue();
    expect(localStorage.getItem('meridian.token')).toBe('tok-1');
    expect(service.hasAnyRole('hr')).toBeTrue();
  });

  it('reports the same generic error for a rejected login and keeps no token', async () => {
    const pending = service.login('dana@meridian.local', 'wrong');
    http.expectOne('/api/auth/login').flush({ detail: 'bad' }, { status: 401, statusText: 'x' });

    expect(await pending).toBeFalse();
    expect(service.error()).toBe('פרטי התחברות שגויים');
    expect(service.isAuthenticated()).toBeFalse();
    expect(localStorage.getItem('meridian.token')).toBeNull();
  });

  it('treats client-side roles as display state only', () => {
    // התפקידים בצד הלקוח אינם מקור סמכות. הבדיקה מתעדת את זה: אין
    // באובייקט הזה שום דרך "להעניק" הרשאה — רק לשקף מה שהשרת אמר.
    expect(service.isAdmin()).toBeFalse();
    expect(Object.keys(service).some((k) => /grant|setRole/i.test(k))).toBeFalse();
  });

  it('clears state and navigates to login on logout', () => {
    service.logout();
    expect(service.token()).toBeNull();
    expect(service.me()).toBeNull();
    expect(router.navigate).toHaveBeenCalledWith(['/login']);
  });
});
