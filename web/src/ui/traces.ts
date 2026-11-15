import type { PipelineTrace } from '../protocol';
export function updateTraces(previous:readonly PipelineTrace[],trace:PipelineTrace):PipelineTrace[] {
 if(!trace.tx_id || !Number.isFinite(trace.total_ms) || trace.total_ms<0 || !Array.isArray(trace.stages)
  || trace.stages.some(s=>!Number.isFinite(s.t0_ms)||!Number.isFinite(s.t1_ms)||s.t1_ms<s.t0_ms)) return [...previous];
 const index=previous.findIndex(t=>t.tx_id===trace.tx_id);
 if(index>=0 && previous[index].final) return [...previous];
 return index<0?[...previous,trace].slice(-5):previous.map((t,i)=>i===index?trace:t);
}
export function traceDomain(trace:PipelineTrace):[number,number] {
 return [Math.min(0,...trace.stages.map(s=>s.t0_ms)), Math.max(250,trace.total_ms,...trace.stages.map(s=>s.t1_ms))];
}
