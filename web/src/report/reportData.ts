import type { Finding } from "../protocol";

export const categories = [
  ["A_SAFETY", "A · Safety"], ["B_INSTRUCTION", "B · Instruction legality"],
  ["C_PHRASEOLOGY", "C · Phraseology"], ["D_READBACK", "D · Readback monitoring"],
  ["E_COMPLIANCE", "E · Pilot compliance"], ["F_EFFICIENCY", "F · Efficiency"],
] as const;

export function groupFindings(findings: readonly Finding[]) {
  const groups = categories.map(([key, label]) => ({ key: String(key), label: String(label),
    findings: findings.filter(finding => finding.category === key) }));
  const other = findings.filter(finding => !categories.some(([key]) => finding.category === key));
  if (other.length) groups.push({ key: "OTHER", label: "Other findings", findings: other });
  return groups;
}

export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function valueText(value: unknown): string {
  if (value == null || (typeof value === "number" && !Number.isFinite(value))) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function seconds(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${value.toFixed(2)} s` : "—";
}

export function simTime(value: number): string {
  const total = Math.max(0, Math.floor(value));
  return `${Math.floor(total / 60).toString().padStart(2, "0")}:${(total % 60).toString().padStart(2, "0")}`;
}
