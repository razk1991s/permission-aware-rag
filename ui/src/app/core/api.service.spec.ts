import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { ApiService } from './api.service';

describe('ApiService', () => {
  let api: ApiService;
  let mock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(ApiService);
    mock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => mock.verify());

  it('posts a chat question with an explicit null session', () => {
    api.chat('What is the late-payment interest rate?').subscribe();
    const req = mock.expectOne('/api/chat');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ question: 'What is the late-payment interest rate?', session_id: null });
    req.flush({});
  });

  it('never sends a user id or an allowed-document list', () => {
     // Authorization is resolved by the server from the token (ADR 0002).
     // Sending user_id or document lists from the client would make them forgeable input.
    api.chat('x').subscribe();
    const req = mock.expectOne('/api/chat');
    const body = JSON.stringify(req.request.body);
    expect(body).not.toContain('user_id');
    expect(body).not.toContain('allowed_doc');
    req.flush({});
  });

  it('url-encodes the document id when fetching chunks', () => {
    api.chunks('HR/001').subscribe();
    const req = mock.expectOne('/api/documents/HR%2F001/chunks');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('omits the domain parameter when no domain is given', () => {
    api.documents().subscribe();
    const req = mock.expectOne((r) => r.url === '/api/documents');
    expect(req.request.params.has('domain')).toBeFalse();
    expect(req.request.params.get('include_superseded')).toBe('false');
    req.flush([]);
  });

  it('sends the amount as a query parameter on the tier preview', () => {
    api.previewTier(4200).subscribe();
    const req = mock.expectOne((r) => r.url === '/api/actions/preview');
    expect(req.request.params.get('amount')).toBe('4200');
    req.flush({});
  });

  it('sends a decision as an approve flag plus an optional note', () => {
    api.decide(7, false).subscribe();
    const req = mock.expectOne('/api/actions/7/decision');
    expect(req.request.body).toEqual({ approve: false, note: null });
    req.flush({});
  });
});
