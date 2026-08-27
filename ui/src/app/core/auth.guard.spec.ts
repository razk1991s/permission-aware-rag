import { TestBed } from '@angular/core/testing';
import { Router, RouterStateSnapshot, UrlTree } from '@angular/router';
import { provideRouter } from '@angular/router';

import { AuthService } from './auth.service';
import { authGuard } from './auth.guard';

function run(url: string) {
  const state = { url } as RouterStateSnapshot;
  return TestBed.runInInjectionContext(() => authGuard({} as never, state));
}

describe('authGuard', () => {
  let authenticated = false;

  beforeEach(() => {
    authenticated = false;
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: { isAuthenticated: () => authenticated } },
      ],
    });
  });

  it('allows a route when the user holds a token', () => {
    authenticated = true;
    expect(run('/chat')).toBeTrue();
  });

  it('redirects to login and preserves the target as returnUrl', () => {
    const result = run('/traces/abc-123') as UrlTree;
    expect(result).toBeInstanceOf(UrlTree);
    expect(TestBed.inject(Router).serializeUrl(result)).toBe(
      '/login?returnUrl=%2Ftraces%2Fabc-123',
    );
  });
});
