export type Point = [number, number];
export interface View { x: number; y: number; range: number }

export function scaleFor(width: number, height: number, range: number) {
  return Math.min(width, height) / (2 * range);
}

export function project(point: Point, view: View, width: number, height: number): Point {
  const scale = scaleFor(width, height, view.range);
  return [width / 2 + (point[0] - view.x) * scale,
    height / 2 - (point[1] - view.y) * scale];
}

export function unproject(point: Point, view: View, width: number, height: number): Point {
  const scale = scaleFor(width, height, view.range);
  return [view.x + (point[0] - width / 2) / scale,
    view.y - (point[1] - height / 2) / scale];
}

export function zoomAt(view: View, point: Point, factor: number, width: number, height: number): View {
  const before = unproject(point, view, width, height);
  const next = { ...view, range: Math.max(5, Math.min(100, view.range * factor)) };
  const after = unproject(point, next, width, height);
  return { ...next, x: next.x + before[0] - after[0], y: next.y + before[1] - after[1] };
}

export function levelLabel(altitude: number) {
  const value = Math.round(altitude / 100).toString().padStart(3, "0");
  return altitude <= 6000 ? `A${value}` : `FL${value}`;
}

export function headingLabel(heading: number) {
  return (Math.round(heading) % 360 || 360).toString().padStart(3, "0");
}

// Heading bug maths (15 Nov, Sanju for Bhavin): screen drag -> heading, shortest turn.
export function bugHeading(dx: number, dy: number): number {
  const deg = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360;   // screen: x right, y down, north up
  const rounded = Math.round(deg / 5) * 5 % 360;
  return rounded === 0 ? 360 : rounded;
}

export function bugTurn(current: number, heading: number): "L" | "R" {
  const diff = ((heading - current + 540) % 360) - 180;
  return diff < 0 ? "L" : "R";
}

