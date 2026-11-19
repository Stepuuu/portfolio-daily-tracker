// The notification inbox behind the bell (backend/api/notifications.py). Paging rules: lib in
// features/notifications/inbox.ts.
import api from './api'
import type { InboxPage } from '@/features/notifications/inbox'

export type { InboxPage, Notification } from '@/features/notifications/inbox'

/** Newest page (no cursor), newer than `after` (oldest first) or older than `before` (newest first). */
export async function fetchInbox(params: { after?: number; before?: number; limit?: number } = {}): Promise<InboxPage> {
  return (await api.get<InboxPage>('/notifications', { params })).data
}

export async function markNotificationRead(seq: number): Promise<{ seq: number; read: boolean; unread: number }> {
  return (await api.post(`/notifications/${seq}/read`)).data
}

/** Marks read everything up to `upTo`, the newest seq the user has been shown. */
export async function markNotificationsRead(upTo: number): Promise<{ marked: number; unread: number }> {
  return (await api.post('/notifications/read-all', null, { params: { up_to: upTo } })).data
}
