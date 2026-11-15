import type { CoachMsg } from "../protocol";

export function mergeCoach(previous: CoachMsg | null, incoming: CoachMsg): CoachMsg {
  // the rules message comes first; the same tx_id again carries Claude's tip
  if (previous && previous.tx_id === incoming.tx_id) return { ...previous, ...incoming, tip: incoming.tip ?? previous.tip };
  return incoming;
}

