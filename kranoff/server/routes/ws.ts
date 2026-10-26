import type { FastifyInstance } from 'fastify';
import { SESSION_COOKIE, adminFromToken } from '../lib/auth.ts';
import { getMasterByToken } from '../lib/orders.ts';
import { attachAdmin, attachMaster, updateMasterLocation, type LiveSocket } from '../lib/live.ts';

function parseCookie(header: string | undefined, name: string): string | undefined {
  if (!header) return undefined;
  for (const part of header.split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return decodeURIComponent(rest.join('='));
  }
  return undefined;
}

export async function wsRoutes(app: FastifyInstance): Promise<void> {
  app.get('/admin', { websocket: true }, (socket, req) => {
    const token = parseCookie(req.headers.cookie, SESSION_COOKIE);
    const admin = adminFromToken(token);
    if (!admin || !admin.is_active) {
      socket.close(1008, 'unauthorized');
      return;
    }
    attachAdmin(socket as unknown as LiveSocket);
    socket.on('message', (raw: Buffer) => {
      try {
        const data = JSON.parse(raw.toString());
        if (data?.type === 'ping') socket.send(JSON.stringify({ type: 'pong', at: new Date().toISOString() }));
      } catch {
        // ignore
      }
    });
  });

  app.get('/master', { websocket: true }, (socket, req) => {
    const token = String((req.query as { token?: string } | undefined)?.token || '');
    const master = getMasterByToken(token);
    if (!master) {
      socket.close(1008, 'unauthorized');
      return;
    }
    attachMaster(master.id, socket as unknown as LiveSocket);
    let lastUpdate = 0;
    socket.on('message', (raw: Buffer) => {
      try {
        const data = JSON.parse(raw.toString());
        if (data?.type === 'ping') {
          socket.send(JSON.stringify({ type: 'pong', at: new Date().toISOString() }));
          return;
        }
        if (data?.type === 'location') {
          const lat = Number(data.lat);
          const lng = Number(data.lng);
          if (!Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) return;
          const now = Date.now();
          if (now - lastUpdate < 1000) return;
          lastUpdate = now;
          updateMasterLocation(master.id, lat, lng, Number(data.accuracy) || undefined);
        }
      } catch {
        // ignore malformed messages
      }
    });
  });
}
