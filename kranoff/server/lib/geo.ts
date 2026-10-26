import { haversineKm } from './utils.ts';

type GeoResult = { display_name: string; lat: number; lon: number; type?: string };

const cache = new Map<string, { at: number; data: GeoResult[] }>();
const TTL = 10 * 60_000;
const PAVLODAR = { lat: 52.2873, lng: 76.9674 };

export async function geocode(query: string): Promise<GeoResult[]> {
  const q = query.trim();
  if (q.length < 3) return [];
  const cached = cache.get(q);
  if (cached && Date.now() - cached.at < TTL) return cached.data;
  const url = new URL('https://nominatim.openstreetmap.org/search');
  url.searchParams.set('format', 'jsonv2');
  url.searchParams.set('q', q);
  url.searchParams.set('limit', '8');
  url.searchParams.set('accept-language', 'ru');
  url.searchParams.set('countrycodes', 'kz');
  url.searchParams.set('viewbox', '76.75,52.40,77.20,52.15');
  url.searchParams.set('addressdetails', '0');
  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'KRANOFF-plumbing-service/1.0 (Pavlodar, Kazakhstan)',
        Accept: 'application/json',
      },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return [];
    const json = (await res.json()) as Array<{ display_name: string; lat: string; lon: string; type?: string }>;
    const data = json
      .map((item) => ({
        display_name: item.display_name,
        lat: Number(item.lat),
        lon: Number(item.lon),
        type: item.type,
      }))
      .filter((item) => haversineKm(PAVLODAR.lat, PAVLODAR.lng, item.lat, item.lon) <= 45)
      .sort(
        (a, b) =>
          haversineKm(PAVLODAR.lat, PAVLODAR.lng, a.lat, a.lon) -
          haversineKm(PAVLODAR.lat, PAVLODAR.lng, b.lat, b.lon),
      )
      .slice(0, 6);
    cache.set(q, { at: Date.now(), data });
    return data;
  } catch (error) {
    console.error('[geocode]', error);
    return [];
  }
}
