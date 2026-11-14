export interface LabelBox { x: number; y: number; width: number; height: number }
export function overlaps(a: LabelBox, b: LabelBox): boolean {
  return a.x < b.x + b.width + 4 && a.x + a.width + 4 > b.x
    && a.y < b.y + b.height + 4 && a.y + a.height + 4 > b.y;
}

/** Deterministic nearby slots for readable three-line data blocks. */
export function placeTag(x: number, y: number, width: number, viewportWidth: number,
  viewportHeight: number, occupied: readonly LabelBox[]): LabelBox {
  const candidates: LabelBox[] = [];
  for (const dy of [-30, 26, -86, 82, -142, 138, -198, 194]) {
    for (const dx of [16, -width - 16]) candidates.push({
      x: Math.max(2, Math.min(x + dx, viewportWidth - width - 2)),
      y: Math.max(2, Math.min(y + dy, viewportHeight - 50)), width, height: 48,
    });
  }
  return candidates.find(box => !occupied.some(other => overlaps(box, other))) ?? candidates[0];
}
