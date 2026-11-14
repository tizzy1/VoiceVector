export type PresenterKey = "1" | "4" | "8" | "d" | "j";

export function presenterKey(event: {
  key: string; repeat: boolean; ctrlKey: boolean; altKey: boolean; metaKey: boolean;
  isComposing: boolean; defaultPrevented: boolean;
}, editing: boolean): PresenterKey | null {
  if (editing || event.repeat || event.ctrlKey || event.altKey || event.metaKey
    || event.isComposing || event.defaultPrevented) return null;
  const key = event.key.toLowerCase();
  return key === "1" || key === "4" || key === "8" || key === "d" || key === "j" ? key : null;
}
