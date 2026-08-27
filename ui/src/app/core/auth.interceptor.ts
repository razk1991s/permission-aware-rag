import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';

import { AuthService } from './auth.service';

/**
 * מצרף את הטוקן לכל קריאה, ומנתק אוטומטית ב-401.
 *
 * ה-TTL של הטוקן הוא 15 דקות (JWT_TTL_MINUTES). ניתוק על 401 הוא
 * ההתנהגות הנכונה כאן: אין refresh token בפרויקט, ומוטב שהמשתמש
 * יתחבר מחדש מאשר שיראה שגיאות שלא מובנות לו.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const token = auth.token();

  const authorized = token
    ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
    : req;

  return next(authorized).pipe(
    catchError((err: HttpErrorResponse) => {
      if (err.status === 401 && !req.url.includes('/auth/login')) {
        auth.logout();
      }
      return throwError(() => err);
    }),
  );
};
