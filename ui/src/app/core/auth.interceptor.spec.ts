import { TestBed } from '@angular/core/testing';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { AuthService } from './auth.service';
import { authInterceptor } from './auth.interceptor';

describe('authInterceptor', () => {
  let http: HttpClient;
  let mock: HttpTestingController;
  let auth: jasmine.SpyObj<AuthService> & { token: () => string | null };

  beforeEach(() => {
    let token: string | null = 'tok-1';
    auth = jasmine.createSpyObj<AuthService>('AuthService', ['logout']) as never;
    (auth as unknown as { token: () => string | null }).token = () => token;
    (auth as unknown as { _set: (t: string | null) => void })._set = (t) => (token = t);

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: auth },
      ],
    });

    http = TestBed.inject(HttpClient);
    mock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => mock.verify());

  it('attaches the bearer token', () => {
    http.get('/api/documents').subscribe();
    const req = mock.expectOne('/api/documents');
    expect(req.request.headers.get('Authorization')).toBe('Bearer tok-1');
    req.flush([]);
  });

  it('logs out on 401', () => {
    http.get('/api/documents').subscribe({ error: () => undefined });
    mock.expectOne('/api/documents').flush(null, { status: 401, statusText: 'Unauthorized' });
    expect(auth.logout).toHaveBeenCalled();
  });

  it('does not log out when the login call itself returns 401', () => {
    // Otherwise a failed login would trigger unnecessary logout navigation
    // and hide the error message on the login screen.
    http.post('/api/auth/login', {}).subscribe({ error: () => undefined });
    mock.expectOne('/api/auth/login').flush(null, { status: 401, statusText: 'Unauthorized' });
    expect(auth.logout).not.toHaveBeenCalled();
  });

  it('sends no Authorization header when there is no token', () => {
    (auth as unknown as { _set: (t: string | null) => void })._set(null);
    http.get('/api/health').subscribe();
    const req = mock.expectOne('/api/health');
    expect(req.request.headers.has('Authorization')).toBeFalse();
    req.flush({});
  });
});
