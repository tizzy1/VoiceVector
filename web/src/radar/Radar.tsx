import { useEffect, useRef } from "react";
import type { StateMsg } from "../protocol";

type Point = [number, number];
export interface Airspace {
  video_map: { airspace: Record<string, [Point, Point][]> };
  runways: Record<string, { threshold: { xy_nm: Point } }>;
  fixes: Record<string, { xy_nm: Point }>;
}

export function Radar({ state, data }: { state: StateMsg | null; data: Airspace | null }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current!;
    const context = element.getContext("2d")!;
    const draw = () => {
      const { width, height } = element.getBoundingClientRect();
      const ratio = devicePixelRatio || 1;
      element.width = width * ratio;
      element.height = height * ratio;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      const scale = Math.min(width, height) / 64;
      const project = ([x, y]: Point): Point => [width / 2 + x * scale, height / 3 - y * scale];
      context.lineWidth = 1;
      context.strokeStyle = "#223138";
      for (const radius of [5, 10, 20, 30]) {
        const origin = project([0, 0]);
        context.beginPath();
        context.arc(...origin, radius * scale, 0, Math.PI * 2);
        context.stroke();
      }
      context.strokeStyle = "#2b3a40";
      for (const segments of Object.values(data?.video_map.airspace ?? {})) {
        for (const [a, b] of segments) {
          context.beginPath(); context.moveTo(...project(a));
          context.lineTo(...project(b)); context.stroke();
        }
      }
      context.font = "10px ui-monospace, monospace";
      context.fillStyle = "#647a83";
      for (const name of ["WILLO", "TIMBA", "HOLLY", "SFD"]) {
        const fix = data?.fixes[name];
        if (fix) {
          const [x, y] = project(fix.xy_nm);
          context.fillText(`△ ${name}`, x, y);
        }
      }
      const a = data?.runways["26L"]?.threshold.xy_nm;
      const b = data?.runways["08R"]?.threshold.xy_nm;
      if (a && b) {
        context.strokeStyle = "#8da4ac"; context.lineWidth = 3;
        context.beginPath(); context.moveTo(...project(a));
        context.lineTo(...project(b)); context.stroke();
      }
      for (const aircraft of state?.aircraft ?? []) {
        const [x, y] = project([aircraft.x, aircraft.y]);
        context.fillStyle = aircraft.transmitting ? "#7cf0b4" : "#e8eef0";
        context.fillRect(x - 3, y - 3, 6, 6);
        context.fillText(aircraft.callsign, x + 13, y - 8);
        context.fillText(
          `${Math.round(aircraft.alt / 100).toString().padStart(3, "0")} → ${Math.round(aircraft.cleared_alt / 100)}  ${Math.round(aircraft.gs)}kt`,
          x + 13, y + 7,
        );
        const radians = aircraft.track * Math.PI / 180;
        context.lineWidth = 1; context.strokeStyle = "#71868d";
        context.beginPath(); context.moveTo(x, y);
        context.lineTo(x + Math.sin(radians) * 24, y - Math.cos(radians) * 24);
        context.stroke();
      }
    };
    const observer = new ResizeObserver(draw);
    observer.observe(element);
    draw();
    return () => observer.disconnect();
  }, [state, data]);
  return <canvas ref={canvas} aria-label="Gatwick radar scope" />;
}
