import { useEffect, useRef, useState } from "react";
import type { AircraftPublic, StateMsg } from "../protocol";
import { headingLabel, levelLabel, project, scaleFor, zoomAt } from "./geometry";
import type { Point, View } from "./geometry";
import { MotionBuffer, vectorEnd } from "./motion";
import "./radar.css";

type Segments = [Point, Point][];
export interface Airspace {
  video_map: { airspace: Record<string, Segments>; centrelines: Record<string, Segments> };
  runways: Record<string, { threshold: { xy_nm: Point } }>;
  fixes: Record<string, { xy_nm: Point }>;
  airspace: { name: string; type: string; segments: Segments }[];
  sectors: { lines: Record<string, Point[]> };
  rma: Record<string, Segments>;
  procedures: Record<"SID" | "STAR",
    Record<string, Record<string, { route: string[]; resolved: boolean }>>>;
}
const LAYERS = ["CTR", "CTA", "TMA", "Sectors", "RMA", "Fixes", "STAR", "SID",
  "Centreline", "Rings"] as const;
type Layer = typeof LAYERS[number];
const INITIAL: View = { x: 5, y: -10, range: 40 };

export function Radar({ state, data, selected, onSelect }: {
  state: StateMsg | null; data: Airspace | null;
  selected: string; onSelect: (callsign: string) => void;
}) {
  const motion = useRef(new MotionBuffer());
  const latestState = useRef(state);
  const [vectorMinutes, setVectorMinutes] = useState(1);
  const canvas = useRef<HTMLCanvasElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const background = useRef<HTMLCanvasElement | null>(null);
  const hits = useRef<{ callsign: string; x: number; y: number; width: number }[]>([]);
  const drag = useRef<{ x: number; y: number; view: View; moved: boolean } | null>(null);
  const [view, setView] = useState<View>(INITIAL);
  const [size, setSize] = useState({ width: 1, height: 1 });
  const [layers, setLayers] = useState<Set<Layer>>(
    () => new Set(["CTR", "CTA", "Sectors", "Fixes", "Centreline", "Rings"]),
  );
  const aircraft = state?.aircraft.find(a => a.callsign === selected);
  const runway = state?.runway ?? "26L";

  useEffect(() => {
    latestState.current = state;
    motion.current.update(state, performance.now());
  }, [state]);

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(viewport.current!);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const element = viewport.current!;
    function wheel(event: WheelEvent) {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      const point: Point = [event.clientX - rect.left, event.clientY - rect.top];
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? size.height : 1);
      setView(current => zoomAt(current, point, Math.exp(Math.max(-1, Math.min(1, delta * .002))),
        size.width, size.height));
    }
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [size]);

  useEffect(() => {
    const image = document.createElement("canvas");
    const ratio = devicePixelRatio || 1;
    image.width = size.width * ratio; image.height = size.height * ratio;
    const ctx = image.getContext("2d")!;
    ctx.scale(ratio, ratio);
    const xy = (p: Point) => project(p, view, size.width, size.height);
    const line = (points: Point[], color = "#2b3a40") => {
      if (points.length < 2) return;
      ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.beginPath();
      ctx.moveTo(...xy(points[0]));
      for (const point of points.slice(1)) ctx.lineTo(...xy(point));
      ctx.stroke();
    };
    ctx.font = "10px ui-monospace, monospace";
    ctx.fillStyle = "#60777f";
    if (layers.has("Rings")) {
      const step = view.range <= 20 ? 5 : 10;
      for (let radius = step; radius <= view.range * 2; radius += step) {
        ctx.strokeStyle = "#223138"; ctx.beginPath();
        ctx.arc(...xy([0, 0]), radius * scaleFor(size.width, size.height, view.range),
          0, Math.PI * 2);
        ctx.stroke();
      }
    }
    for (const area of data?.airspace ?? []) {
      if (layers.has(area.type as Layer)) for (const segment of area.segments) line(segment);
    }
    if (layers.has("Sectors")) {
      for (const points of Object.values(data?.sectors.lines ?? {})) line(points, "#3b5059");
    }
    if (layers.has("RMA")) {
      for (const segments of Object.values(data?.rma ?? {})) for (const s of segments) line(s);
    }
    for (const kind of ["STAR", "SID"] as const) {
      if (layers.has(kind)) {
        for (const procedure of Object.values(data?.procedures[kind][runway] ?? {})) {
          let previous: Point | undefined;
          for (const name of procedure.route) {
            const point = data?.fixes[name]?.xy_nm;
            if (point && previous) line([previous, point], kind === "STAR" ? "#344f58" : "#524b36");
            previous = point;
          }
        }
      }
    }
    if (layers.has("Centreline")) {
      for (const [name, segments] of Object.entries(data?.video_map.centrelines ?? {})) {
        if (name.includes(runway)) for (const s of segments) line(s, "#536971");
      }
    }
    if (layers.has("Fixes")) {
      for (const [name, fix] of Object.entries(data?.fixes ?? {})) {
        const [x, y] = xy(fix.xy_nm);
        if (x > 0 && x < size.width - 45 && y > 12 && y < size.height) {
          ctx.fillText(`△ ${name}`, x, y);
        }
      }
    }
    const a = data?.runways[runway]?.threshold.xy_nm;
    const b = data?.runways[runway === "26L" ? "08R" : "26L"]?.threshold.xy_nm;
    if (a && b) {
      ctx.lineWidth = 3; ctx.strokeStyle = "#a7bbc3"; ctx.beginPath();
      ctx.moveTo(...xy(a)); ctx.lineTo(...xy(b)); ctx.stroke();
    }
    background.current = image;
  }, [data, view, size, layers, runway]);

  useEffect(() => {
    const element = canvas.current!;
    const ctx = element.getContext("2d")!;
    const ratio = devicePixelRatio || 1;
    element.width = size.width * ratio; element.height = size.height * ratio;
    let frame = 0;
    function draw(now: number) {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, element.width, element.height);
      if (background.current) ctx.drawImage(background.current, 0, 0);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.font = "12px ui-monospace, monospace";
      hits.current = [];
      for (const ac of motion.current.sample(now)) {
        const [x, y] = project([ac.x, ac.y], view, size.width, size.height);
        if (x < -10 || y < -10 || x > size.width + 10 || y > size.height + 10) continue;
        const alert = latestState.current?.alerts.find(a => a.callsigns.includes(ac.callsign));
        const color = alert ? (alert.kind === "WAKE" ? "#f2b544" : "#ff5a52")
          : ac.emergency ? "#f2b544" : ac.callsign === selected ? "#4fd1e8"
          : ac.transmitting ? "#7cf0b4" : !ac.in_jurisdiction ? "#7c8b90" : "#e8eef0";
        ctx.fillStyle = color; ctx.strokeStyle = color; ctx.lineWidth = 1;
        for (const point of ac.history) {
          const pixel = project([point.x, point.y], view, size.width, size.height);
          ctx.globalAlpha = .4;
          ctx.beginPath(); ctx.arc(...pixel, 1.5, 0, Math.PI * 2); ctx.fill();
        }
        ctx.globalAlpha = 1;
        if (vectorMinutes > 0) {
          const end = project(vectorEnd(ac, vectorMinutes), view, size.width, size.height);
          ctx.globalAlpha = .6;
          ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(...end); ctx.stroke();
          ctx.globalAlpha = 1;
        }
        ctx.fillRect(x - 3, y - 3, 6, 6);
        if (ac.callsign === selected) ctx.strokeRect(x - 7, y - 7, 14, 14);
        const trend = ac.vs > 100 ? "↑" : ac.vs < -100 ? "↓" : "→";
        const assigned = [
          ac.actype,
          ac.tgt_hdg == null ? "" : `H${headingLabel(ac.tgt_hdg)}`,
          ac.tgt_ias == null ? "" : `S${Math.round(ac.tgt_ias)}`,
        ].filter(Boolean).join(" ");
        const lines = [ac.callsign,
          `${levelLabel(ac.alt)}${trend}${levelLabel(ac.cleared_alt)} ${Math.round(ac.gs)}kt`, assigned];
        const width = Math.max(...lines.map(text => ctx.measureText(text).width));
        const labelX = Math.max(4, Math.min(x + 16, size.width - width - 4));
        const labelY = Math.max(14, Math.min(y - 18, size.height - 36));
        ctx.beginPath(); ctx.moveTo(x + 3, y); ctx.lineTo(labelX - 3, labelY + 5); ctx.stroke();
        ctx.fillStyle = "#0e1417";
        ctx.fillRect(labelX - 2, labelY - 12, width + 4, 48);
        ctx.fillStyle = color;
        lines.forEach((text, index) => ctx.fillText(text, labelX, labelY + index * 16));
        hits.current.push({ callsign: ac.callsign, x: labelX, y: labelY, width });
        hits.current.push({ callsign: ac.callsign, x: x - 8, y: y - 8, width: 16 });
    }
    frame = requestAnimationFrame(draw);
    }
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [view, size, selected, layers, data, runway, vectorMinutes]);

  function hit(clientX: number, clientY: number) {
    const rect = viewport.current!.getBoundingClientRect();
    const x = clientX - rect.left, y = clientY - rect.top;
    return hits.current.find(h => x >= h.x - 3 && x <= h.x + h.width + 3
      && y >= h.y - 13 && y <= h.y + 35)?.callsign ?? "";
  }
  function centre() { setView(INITIAL); }
  function centreAircraft(ac: AircraftPublic) { setView(v => ({ ...v, x: ac.x, y: ac.y })); }
  return <div className="radar">
    <div className="radar-toolbar" aria-label="Radar controls">
      <label>Range <select aria-label="Radar range" value={String(view.range)}
        onChange={e => setView(v => ({ ...v, range: Number(e.target.value) }))}>
        {![10, 20, 40, 80].includes(view.range) &&
          <option value={view.range}>{view.range.toFixed(1)} NM</option>}
        {[10, 20, 40, 80].map(range => <option key={range} value={range}>{range} NM</option>)}
      </select></label>
      <button onClick={centre}>Centre airport</button>
      <label>Aircraft <select aria-label="Select aircraft" value={selected}
        onChange={e => onSelect(e.target.value)}>
        <option value="">Select aircraft</option>
        {state?.aircraft.map(ac => <option key={ac.callsign}>{ac.callsign}</option>)}
      </select></label>
      <button disabled={!aircraft} onClick={() => aircraft && centreAircraft(aircraft)}>
        Centre selected</button>
      <label>Vector <select aria-label="Speed vector duration" value={vectorMinutes}
        onChange={e => setVectorMinutes(Number(e.target.value))}>
        <option value={0}>Off</option>
        {[1, 2, 5].map(minutes => <option key={minutes} value={minutes}>{minutes} min</option>)}
      </select></label>
      <details><summary>Map layers</summary><div className="radar-layers">
        {LAYERS.map(layer => <label key={layer}><input type="checkbox"
          checked={layers.has(layer)} onChange={() => setLayers(current => {
            const next = new Set(current);
            if (next.has(layer)) next.delete(layer); else next.add(layer);
            return next;
          })} />{layer}</label>)}
      </div></details>
    </div>
    <div className="radar-viewport" ref={viewport} tabIndex={0} role="application"
      aria-label="Gatwick radar. Drag to pan, scroll to zoom, arrows to move, C to centre."
      onKeyDown={e => {
        const step = view.range / 8;
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "+", "=", "-", "c", "C"].includes(e.key)) {
          e.preventDefault();
          if (e.key.toLowerCase() === "c") centre();
          else setView(v => ({
            x: v.x + (e.key === "ArrowRight" ? step : e.key === "ArrowLeft" ? -step : 0),
            y: v.y + (e.key === "ArrowUp" ? step : e.key === "ArrowDown" ? -step : 0),
            range: Math.max(5, Math.min(100, v.range *
              (e.key === "-" ? 1.2 : e.key === "+" || e.key === "=" ? 1 / 1.2 : 1))),
          }));
        }
      }}
      onPointerDown={e => {
        if (e.button !== 0) return;
        e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, y: e.clientY, view, moved: false };
      }}
      onPointerMove={e => {
        const start = drag.current;
        if (!start) return;
        const dx = e.clientX - start.x, dy = e.clientY - start.y;
        if (Math.hypot(dx, dy) > 3) start.moved = true;
        if (start.moved) {
          const scale = scaleFor(size.width, size.height, start.view.range);
          setView({ ...start.view, x: start.view.x - dx / scale, y: start.view.y + dy / scale });
        }
      }}
      onPointerUp={e => {
        if (drag.current && !drag.current.moved) onSelect(hit(e.clientX, e.clientY));
        drag.current = null;
      }}
      onPointerCancel={() => { drag.current = null; }}
      onLostPointerCapture={() => { drag.current = null; }}
      onDoubleClick={e => {
        const cs = hit(e.clientX, e.clientY);
        const ac = state?.aircraft.find(a => a.callsign === cs);
        if (ac) centreAircraft(ac);
      }}>
      <canvas ref={canvas} aria-label="Aircraft tracks and airspace map" />
      <span className="radar-help">Drag to pan · Scroll to zoom · Range {view.range.toFixed(1)} NM</span>
    </div>
    <div className="radar-selection" aria-live="polite">
      {aircraft ? `${aircraft.spoken_plain} · ${aircraft.plain_status} · Heading ${headingLabel(aircraft.hdg)}`
        : "Select a track or choose an aircraft above to inspect it."}
    </div>
  </div>;
}
