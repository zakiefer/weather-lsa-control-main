import { MapStateSchema } from '@shared/schemas/map';

export async function getMapState() {
  const base = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';
  const res = await fetch(`${base}/v1/map/state`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch map state');
  const json = await res.json();
  return MapStateSchema.parse(json);
}
