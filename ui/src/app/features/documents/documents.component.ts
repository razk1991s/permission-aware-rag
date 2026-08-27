import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { ChunkView, DocumentSummary } from '../../core/models';

/** Documents screen and a simple demonstration of access control. */
@Component({
  selector: 'app-documents',
  standalone: true,
  imports: [FormsModule],
  styles: [
    `
      .layout { display: grid; grid-template-columns: minmax(260px, 340px) 1fr; gap: 14px; align-items: start; }
      @media (max-width: 820px) { .layout { grid-template-columns: 1fr; } }
      .doc { width: 100%; text-align: start; background: none; color: var(--ink); border: 1px solid transparent; border-radius: 8px; padding: 9px 11px; display: block; }
      .doc:hover { background: var(--card-2); }
      .doc.on { background: var(--accent-soft); border-color: var(--accent-line); }
      .doc .id { font-family: var(--mono); font-size: 0.76rem; color: var(--muted); }
      .chunk { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }
      .chunk__head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 0.78rem; color: var(--muted); margin-bottom: 6px; }
      .chunk__body { white-space: pre-wrap; font-size: 0.88rem; }
    `,
  ],
  template: `
    <div class="page">
      <div class="layout">
        <div class="card">
          <div class="row" style="margin-bottom:8px">
            <div class="card__title" style="margin:0">
              Documents
              <span class="small muted">({{ documents().length }})</span>
            </div>
            <span class="spacer"></span>
            <select style="width:auto" [ngModel]="domain()" (ngModelChange)="setDomain($event)">
              <option value="">All domains</option>
              <option value="finance">Finance</option>
              <option value="hr">Human Resources</option>
              <option value="public">Public</option>
            </select>
          </div>

          <label class="small muted" style="display:flex;gap:6px;align-items:center">
            <input type="checkbox" style="width:auto" [ngModel]="includeSuperseded()" (ngModelChange)="setSuperseded($event)" />
            Include expired documents
          </label>

          <div style="margin-top:10px">
            @for (d of documents(); track d.doc_id) {
              <button class="doc" [class.on]="selected()?.doc_id === d.doc_id" (click)="select(d)">
                <span class="id">{{ d.doc_id }}</span> · {{ d.title }}
                <div class="small muted">
                  {{ d.file_type }} · {{ d.chunk_count }} chunks
                  @if (d.status !== 'active') { <span class="pill pill--warn">{{ d.status }}</span> }
                </div>
              </button>
            } @empty {
              <div class="empty small">No authorized documents for your role.</div>
            }
          </div>
        </div>

        <div class="card">
          @if (selected(); as doc) {
            <div class="card__title">
              {{ doc.title }}
              <span class="small muted">- {{ chunks().length }} chunks</span>
            </div>

            @if (loadingChunks()) {
              <div class="empty">Loading...</div>
            } @else {
              @for (c of chunks(); track c.id) {
                <div class="chunk">
                  <div class="chunk__head">
                    <span class="pill pill--muted">#{{ c.chunk_index }}</span>
                    @if (c.strategy) { <span class="pill pill--accent">{{ c.strategy }}</span> }
                    @if (c.section_path) { <span>{{ c.section_path }}</span> }
                    @if (c.page_number) { <span>Page {{ c.page_number }}</span> }
                    <span class="spacer"></span>
                    <span>{{ c.token_count }} tokens</span>
                  </div>
                  <div class="chunk__body">{{ c.content }}</div>
                </div>
              }
            }
          } @else {
            <div class="empty">Select a document to inspect its chunks.</div>
          }

          @if (error(); as e) { <p class="error small">{{ e }}</p> }
        </div>
      </div>
    </div>
  `,
})
export class DocumentsComponent {
  private readonly api = inject(ApiService);
  readonly auth = inject(AuthService);
  readonly documents = signal<DocumentSummary[]>([]);
  readonly selected = signal<DocumentSummary | null>(null);
  readonly chunks = signal<ChunkView[]>([]);
  readonly domain = signal('');
  readonly includeSuperseded = signal(false);
  readonly loadingChunks = signal(false);
  readonly error = signal<string | null>(null);

  constructor() { this.load(); }

  setDomain(value: string): void { this.domain.set(value); this.load(); }
  setSuperseded(value: boolean): void { this.includeSuperseded.set(value); this.load(); }

  load(): void {
    this.error.set(null);
    this.api.documents(this.domain() || undefined, this.includeSuperseded()).subscribe({
      next: (docs) => {
        this.documents.set(docs);
        if (this.selected() && !docs.some((d) => d.doc_id === this.selected()!.doc_id)) {
          this.selected.set(null);
          this.chunks.set([]);
        }
      },
      error: (e) => this.error.set(e?.error?.detail ?? 'Failed to load documents'),
    });
  }

  select(doc: DocumentSummary): void {
    this.selected.set(doc);
    this.chunks.set([]);
    this.loadingChunks.set(true);
    this.error.set(null);
    this.api.chunks(doc.doc_id).subscribe({
      next: (rows) => { this.chunks.set(rows); this.loadingChunks.set(false); },
      error: (e) => { this.error.set(e?.error?.detail ?? 'Document not found'); this.loadingChunks.set(false); },
    });
  }
}
