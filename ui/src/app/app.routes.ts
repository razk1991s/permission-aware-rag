import { Routes } from '@angular/router';

import { authGuard } from './core/auth.guard';

/**
 * All screens are lazy-loaded. The trace screen loads large tables and should
 * not be loaded for users who only ask questions.
 */
export const routes: Routes = [
  {
    path: 'login',
    title: 'Login · Meridian',
    loadComponent: () => import('./features/login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: 'chat',
    title: 'Chat · Meridian',
    canActivate: [authGuard],
    loadComponent: () => import('./features/chat/chat.component').then((m) => m.ChatComponent),
  },
  {
    path: 'approvals',
    title: 'Approvals · Meridian',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/approvals/approvals.component').then((m) => m.ApprovalsComponent),
  },
  {
    path: 'traces',
    title: 'Traces · Meridian',
    canActivate: [authGuard],
    loadComponent: () => import('./features/traces/traces.component').then((m) => m.TracesComponent),
  },
  {
    path: 'traces/:id',
    title: 'Trace · Meridian',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/traces/trace-detail.component').then((m) => m.TraceDetailComponent),
  },
  {
    path: 'documents',
    title: 'Documents · Meridian',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/documents/documents.component').then((m) => m.DocumentsComponent),
  },
  { path: '', pathMatch: 'full', redirectTo: 'chat' },
  { path: '**', redirectTo: 'chat' },
];
