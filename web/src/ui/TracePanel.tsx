import { useState } from 'react';
import type { PipelineTrace } from '../protocol';
import { traceDomain } from './traces';
import './trace.css';
export function TracePanel({traces,audience,onClose}:{traces:readonly PipelineTrace[];audience:boolean;onClose:()=>void}) {
 const [selected,setSelected]=useState('');
 const trace=(!audience && traces.find(t=>t.tx_id===selected)) || traces.at(-1);
 const domain=trace?traceDomain(trace):[0,250],span=domain[1]-domain[0];
 const ticks=Array.from({length:Math.min(240,Math.floor(span/250)+1)},(_,i)=>Math.ceil(domain[0]/250)*250+i*250).filter(v=>v<=domain[1]);
 const parse=trace?.stages.find(s=>s.name==='parse');
 const detail=parse?.detail.path;
 const blocked=trace?.stages.some(s=>s.name==='blocked');
 const released=trace?.final || trace?.stages.some(s=>s.name==='reaction_hold'||s.name==='playback');
 return <section className={`trace-panel ${audience?'trace-audience':'trace-controller'}`} aria-label="Pipeline trace">
  <header><strong>PIPELINE <small>SERVER MEASUREMENTS</small></strong><button aria-label="Close pipeline" onClick={onClose}>×</button></header>
  {!trace?<p role="status">Waiting for pipeline traces. The voice pipeline has not supplied measurements yet.</p>:<>
   <div className="trace-heading"><span>{trace.speaker} → {trace.reply_callsign??'—'} · {trace.stages.length} stages · {typeof detail==='string'?`${detail} path`:'path not supplied'}</span>
    <b>{blocked?'Blocked':released?`${trace.total_ms.toFixed(0)} ms`:'Pending release'} <small>{!blocked&&'to first audio release'}</small></b>
    <span>{trace.final?'Final':'Provisional'} · TX {trace.tx_id}</span></div>
   {!audience&&<nav aria-label="Trace history">{traces.map(t=><button key={t.tx_id} aria-pressed={t.tx_id===trace.tx_id} onClick={()=>setSelected(t.tx_id)}>{t.reply_callsign??t.speaker} · {t.tx_id.slice(-6)}</button>)}</nav>}
   <div className="trace-waterfall" role="img" aria-label="Measured pipeline stage durations in milliseconds">
    <svg viewBox={`0 0 620 ${42+trace.stages.length*30}`}>
     {ticks.map(t=><g key={t}><line x1={180+(t-domain[0])/span*430} x2={180+(t-domain[0])/span*430} y1="22" y2={35+trace.stages.length*30} stroke="#2b3a40"/>
      {Math.abs(t/250)%Math.max(1,Math.ceil(ticks.length/7))===0&&<text x={180+(t-domain[0])/span*430} y="14" textAnchor="middle" className="trace-tick">{t}</text>}</g>)}
     {trace.stages.map((s,i)=><g key={`${s.name}:${i}`} className={`trace-stage trace-${s.category}`}>
      <title>{s.name}: {(s.t1_ms-s.t0_ms).toFixed(1)} ms · {s.status} · {JSON.stringify(s.detail)}</title>
      <text x="3" y={43+i*30}>{s.name.replaceAll('_',' ')}</text>
      <rect x={180+(s.t0_ms-domain[0])/span*430} y={28+i*30} width={Math.max(2,(s.t1_ms-s.t0_ms)/span*430)} height="19" rx="2"/>
      <text className="trace-duration" x="607" y={43+i*30} textAnchor="end">{(s.t1_ms-s.t0_ms).toFixed(0)} ms</text>
     </g>)}
    </svg>
   </div>
   <details><summary>Stage details</summary>{trace.stages.map((s,i)=><p key={i}><b>{s.name}</b> · {s.status} · {s.t0_ms} → {s.t1_ms} ms<br/>{JSON.stringify(s.detail)}</p>)}</details>
   <p className="trace-note">Times relative to PTT release or pilot compose start. Speech may begin before zero.</p>
  </>}
 </section>;
}
