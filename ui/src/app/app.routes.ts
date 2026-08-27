import { Routes } from '@angular/router';

import { authGuard } from './core/auth.guard';

/**
 * כל המסכים נטענים lazy. זה לא אופטימיזציה מוקדמת — מסך הטרייסים
 * מושך טבלאות כבדות, ואין סיבה שהוא ייטען למי שרק שואל שאלה.
 */
export const routes: Routes = [
  {
    path: 'login',
    title: 'התחברות · Meridian',
    loadComponent: () => import('./features/login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: 'chat',
    title: 'צ׳אט · Meridian',
    canActivate: [authGuard],
    loadComponent: () => import('./features/chat/chat.component').then((m) => m.ChatComponent),
  },
  {
    path: 'approvals',
    title: 'אישורים · Meridian',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/approvals/approvals.component').then((m) => m.ApprovalsComponent),
  },
  {
    path: 'traces',
    title: 'טרייסים · Meridian',
    canActivate: [authGuard],
    loadComponent: () => import('./features/traces/traces.component').then((m) => m.TracesComponent),
  },
  {
    path: 'traces/:id',
    title: 'טרייס · Meridian',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/traces/trace-detail.component').then((m) => m.TraceDetailComponent),
  },
  {
    path: 'documents',
    title: 'מסמכים · Meridian',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/documents/documents.component').then((m) => m.DocumentsComponent),
  },
  { path: '', pathMatch: 'full', redirectTo: 'chat' },
  { path: '**', redirectTo: 'chat' },
];
