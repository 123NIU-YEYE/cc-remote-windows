import { url } from '@kit.ArkTS';
import { serverOrigin } from './UrlPolicy';

export interface PushRoute {
  machine_id: string;
  session_id: string;
  engine: string;
  space: string;
}
export interface PushTarget { origin: string; route: PushRoute }

// Notification URIs are navigation hints, never proof of login or ownership.
// The Web client still validates device/session access after authentication.
export function parsePushTarget(uri: string): PushTarget | undefined {
  if (uri.length > 8192 || !uri.startsWith('cc-remote-app://notification?')) return undefined;
  try {
    const parsed = new url.URL(uri);
    if (parsed.hostname !== 'notification' || parsed.username !== '' || parsed.password !== ''
      || parsed.port !== '' || parsed.hash !== '' || (parsed.pathname !== '' && parsed.pathname !== '/')) return undefined;
    const origin = parsed.searchParams.get('origin') ?? '';
    if (!origin || serverOrigin(origin) !== origin) return undefined;
    const route = JSON.parse(parsed.searchParams.get('route') ?? '') as PushRoute;
    const id = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;
    if (!route || typeof route.machine_id !== 'string' || !id.test(route.machine_id)
      || typeof route.session_id !== 'string' || !id.test(route.session_id)
      || !['claude', 'codex'].includes(route.engine) || !['code', 'work'].includes(route.space)
      || Object.keys(route).length !== 4) return undefined;
    return { origin: origin, route: route };
  } catch (_) { return undefined; }
}
