import { useEffect, useRef } from 'react';
import L from 'leaflet';

export type MapMarker = {
  id: string | number;
  lat: number;
  lng: number;
  html: string;
  popup?: string;
  title?: string;
  onClick?: () => void;
  stale?: boolean;
  pulse?: boolean;
};

type MapViewProps = {
  center?: [number, number];
  zoom?: number;
  markers?: MapMarker[];
  circle?: { lat: number; lng: number; radius: number; color?: string };
  className?: string;
  height?: number | string;
  follow?: [number, number] | null;
  onMapClick?: (lat: number, lng: number) => void;
  line?: Array<[number, number]> | null;
  lineDashed?: boolean;
  fitLine?: boolean;
};

const DEFAULT_CENTER: [number, number] = [52.2873, 76.9674];

export function MapView({ center = DEFAULT_CENTER, zoom = 12, markers = [], circle, className = 'map-card', height, follow, onMapClick, line, lineDashed, fitLine }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const circleRef = useRef<L.Circle | null>(null);
  const lineRef = useRef<L.Polyline | null>(null);
  const firstRef = useRef(true);
  const clickRef = useRef(onMapClick);
  clickRef.current = onMapClick;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, {
      center,
      zoom,
      zoomControl: true,
      attributionControl: true,
      scrollWheelZoom: true,
    });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap',
    }).addTo(map);
    mapRef.current = map;
    layerRef.current = L.layerGroup().addTo(map);
    const handleClick = (event: L.LeafletMouseEvent) => {
      clickRef.current?.(event.latlng.lat, event.latlng.lng);
    };
    map.on('click', handleClick);
    firstRef.current = false;
    window.setTimeout(() => map.invalidateSize(), 150);
    return () => {
      map.off('click', handleClick);
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;
    layer.clearLayers();
    for (const item of markers) {
      if (!Number.isFinite(item.lat) || !Number.isFinite(item.lng)) continue;
      const icon = L.divIcon({
        html: `<div class="${item.stale ? 'pin-stale' : ''} ${item.pulse ? 'pulse' : ''}">${item.html}</div>`,
        className: 'kranoff-marker',
        iconSize: [36, 36],
        iconAnchor: [18, 18],
      });
      const marker = L.marker([item.lat, item.lng], { icon, title: item.title || '' }).addTo(layer);
      if (item.popup) marker.bindPopup(item.popup, { closeButton: true, maxWidth: 280 });
      if (item.onClick) marker.on('click', item.onClick);
    }
  }, [markers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (circleRef.current) {
      circleRef.current.remove();
      circleRef.current = null;
    }
    if (circle) {
      circleRef.current = L.circle([circle.lat, circle.lng], {
        radius: circle.radius,
        color: circle.color || '#0b6bcb',
        fillColor: circle.color || '#0b6bcb',
        fillOpacity: 0.08,
        weight: 1.5,
      }).addTo(map);
    }
  }, [circle]);

  useEffect(() => {
    if (!mapRef.current || !follow) return;
    mapRef.current.setView(follow, mapRef.current.getZoom(), { animate: true });
  }, [follow?.[0], follow?.[1]]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (lineRef.current) {
      lineRef.current.remove();
      lineRef.current = null;
    }
    if (line && line.length >= 2) {
      lineRef.current = L.polyline(line, { color: '#0b6bcb', weight: 5, opacity: 0.85, dashArray: lineDashed ? '8 8' : undefined }).addTo(map);
      if (fitLine) map.fitBounds(lineRef.current.getBounds(), { padding: [28, 28] });
    }
  }, [line, lineDashed, fitLine]);

  return <div ref={containerRef} className={className} style={height ? { height } : undefined} />;
}
