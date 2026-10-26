import fs from 'node:fs';
import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { getOrderFull, getMasterByToken, masterAction, masterActiveOrder, masterEarnings, setEta } from '../lib/orders.ts';
import { getCompany, getPublicSettings } from '../lib/catalog.ts';
import { getMedia, mediaAbsolutePath } from '../lib/media.ts';
import { updateMasterLocation } from '../lib/live.ts';
import { haversineKm } from '../lib/utils.ts';

const routeCache = new Map<string, { at: number; data: unknown }>();

function masterFromParams(params: unknown) {
  const token = String((params as { token: string }).token || '');
  return { token, master: getMasterByToken(token) };
}

export async function masterPageRoutes(app: FastifyInstance): Promise<void> {
  app.get('/:token', async (req, reply) => {
    const { master } = masterFromParams(req.params);
    if (!master) return reply.code(404).send({ error: 'Страница не найдена' });
    const active = masterActiveOrder(master.id);
    const fullOrder = active ? getOrderFull(active.id) : null;
    const order = fullOrder ? { ...fullOrder, offers: [] } : null;
    return reply.send({
      master: {
        id: master.id,
        name: master.name,
        phone: master.phone,
        status: master.status,
        shift_started_at: master.shift_started_at,
        location_updated_at: master.location_updated_at,
        current_lat: master.current_lat,
        current_lng: master.current_lng,
      },
      order,
      earnings: masterEarnings(master.id),
      company: getCompany(),
      settings: getPublicSettings(),
      server_time: new Date().toISOString(),
    });
  });

  app.post('/:token/action', async (req, reply) => {
    const { master } = masterFromParams(req.params);
    if (!master) return reply.code(404).send({ error: 'Страница не найдена' });
    const parsed = z.object({ action: z.enum(['on_way', 'arrived', 'start', 'done', 'cancel']) }).safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Действие не распознано' });
    const active = masterActiveOrder(master.id);
    if (!active) return reply.code(400).send({ error: 'Нет активного заказа' });
    const result = masterAction(active.id, master.id, parsed.data.action);
    if (!result.ok) return reply.code(400).send({ error: result.error });
    return reply.send({ ok: true, order: getOrderFull(active.id) });
  });

  app.post('/:token/eta', async (req, reply) => {
    const { master } = masterFromParams(req.params);
    if (!master) return reply.code(404).send({ error: 'Страница не найдена' });
    const parsed = z.object({ minutes: z.number().int().min(0).max(1440) }).safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Укажите минуты' });
    const active = masterActiveOrder(master.id);
    if (!active) return reply.code(400).send({ error: 'Нет активного заказа' });
    setEta(active.id, parsed.data.minutes);
    return reply.send({ ok: true });
  });

  app.post('/:token/location', async (req, reply) => {
    const { master } = masterFromParams(req.params);
    if (!master) return reply.code(404).send({ error: 'Страница не найдена' });
    const parsed = z
      .object({ lat: z.number().min(-90).max(90), lng: z.number().min(-180).max(180), accuracy: z.number().optional() })
      .safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Некорректные координаты' });
    updateMasterLocation(master.id, parsed.data.lat, parsed.data.lng, parsed.data.accuracy);
    return reply.send({ ok: true });
  });

  app.get('/:token/route', async (req, reply) => {
    const { master } = masterFromParams(req.params);
    if (!master) return reply.code(404).send({ error: 'Страница не найдена' });
    const q = req.query as Record<string, string | undefined>;
    const fromLat = Number(q.fromLat);
    const fromLng = Number(q.fromLng);
    const toLat = Number(q.toLat);
    const toLng = Number(q.toLng);
    if (![fromLat, fromLng, toLat, toLng].every((value) => Number.isFinite(value))) {
      return reply.code(400).send({ error: 'Некорректные координаты' });
    }
    const key = `${fromLat.toFixed(4)},${fromLng.toFixed(4)}-${toLat.toFixed(4)},${toLng.toFixed(4)}`;
    const cached = routeCache.get(key);
    if (cached && Date.now() - cached.at < 20_000) return reply.send(cached.data);
    try {
      const url = `https://router.project-osrm.org/route/v1/driving/${fromLng},${fromLat};${toLng},${toLat}?overview=full&geometries=geojson&steps=false`;
      const response = await fetch(url, { signal: AbortSignal.timeout(7000) });
      if (!response.ok) throw new Error(`osrm ${response.status}`);
      const json = (await response.json()) as { routes?: Array<{ geometry?: { coordinates?: Array<[number, number]> }; distance?: number; duration?: number }> };
      const route = json.routes?.[0];
      if (!route?.geometry?.coordinates?.length) throw new Error('osrm empty');
      const data = {
        coords: route.geometry.coordinates.map(([lng, lat]) => [lat, lng]),
        distance: route.distance ?? null,
        duration: route.duration ?? null,
        straight: false,
      };
      routeCache.set(key, { at: Date.now(), data });
      return reply.send(data);
    } catch (error) {
      console.error('[route]', error);
      const distance = haversineKm(fromLat, fromLng, toLat, toLng) * 1000;
      return reply.send({ coords: [[fromLat, fromLng], [toLat, toLng]], distance, duration: null, straight: true });
    }
  });

  app.get('/:token/media/:id', async (req, reply) => {
    const { master } = masterFromParams(req.params);
    if (!master) return reply.code(404).send({ error: 'Страница не найдена' });
    const media = getMedia(Number((req.params as { id: string }).id));
    if (!media) return reply.code(404).send({ error: 'Файл не найден' });
    const order = getOrderFull(media.order_id);
    if (!order || order.master_id !== master.id) return reply.code(403).send({ error: 'Нет доступа' });
    const filePath = mediaAbsolutePath(media);
    if (!fs.existsSync(filePath)) return reply.code(404).send({ error: 'Файл отсутствует' });
    reply.header('Content-Type', media.mime || 'application/octet-stream');
    return reply.send(fs.createReadStream(filePath));
  });
}
