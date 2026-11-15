import { useEffect, useRef, useState } from 'react';
import type { AircraftPublic, ControlMsg, StateMsg } from '../protocol';
import type { Airspace } from '../radar/Radar';
import { arrivalColumns, departureColumns, displayStrip, orderedTraffic, parseStrip, stripField, stripValue } from './stripValues';
import './flight-lists.css';
interface Props {state:StateMsg|null; data:Airspace|null; selected:string; onSelect:(s:string)=>void; enabled:boolean; send:(m:ControlMsg)=>boolean}
function Cell({ac,column,props}:{ac:AircraftPublic;column:string;props:Props}) {
 const [editing,setEditing]=useState(false),[draft,setDraft]=useState(''),[error,setError]=useState('');
 const field=stripField(column,ac.flow==='DEP'),value=column==='DIST'?ac.distance_to_touchdown_nm:stripValue(ac,column,props.state?.runway??'26L');
 const commit=(raw=draft)=>{try{
  const next=parseStrip(column,raw);
  if(props.send({type:'control',action:'edit_strip',params:{callsign:ac.callsign,field,value:next}})) {setEditing(false);setError('');}
 }catch(reason){setError(String((reason as Error).message));}};
 const options=column==='FR'?['I','V']:column==='RWY'?Object.keys(props.data?.runways??{}):column==='STS'?['STAR','HOLD','VECT','APP','EST','TWR','SID','LON','QUE']:
  column==='SID'||column==='STAR'?Object.keys(props.data?.procedures[column][String(ac.strip?.rwy??props.state?.runway??'26L')]??{}):null;
 if(!field) return <td>{displayStrip(value,column)}</td>;
 const label=`${ac.callsign} ${column}`;
 return <td className="strip-edit-cell">{editing?<div onClick={e=>e.stopPropagation()}>
  {options?<select aria-label={label} autoFocus value={draft} onChange={e=>{setDraft(e.target.value);commit(e.target.value);}}
   onKeyDown={e=>{if(e.key==='Escape'){e.stopPropagation();setEditing(false);}if(e.key==='Enter')commit();}}>
   <option value="">—</option>{[...new Set([...options,...(draft?[draft]:[])])].map(v=><option key={v}>{v}</option>)}
  </select>:<input aria-label={label} autoFocus value={draft} list={column==='DCT'?'strip-fixes':column==='CFL'||column==='RFL'?'strip-levels':undefined}
   onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();commit();}if(e.key==='Escape'){e.stopPropagation();setEditing(false);}}} />}
  <button aria-label={`Save ${label}`} onClick={()=>commit()}>✓</button><button aria-label={`Cancel ${label}`} onClick={()=>setEditing(false)}>×</button>
  {error&&<span className="strip-error" role="alert">{error}</span>}
 </div>:<button aria-label={`Edit ${label}`} disabled={!props.enabled || ac.flight_rules==null}
  title={ac.flight_rules==null?'Awaiting simulator flight-plan and strip data':'Controller record only; Enter saves, Escape cancels'}
  onClick={e=>{e.stopPropagation();props.onSelect(ac.callsign);if(column==='CLR'){
   props.send({type:'control',action:'edit_strip',params:{callsign:ac.callsign,field,value:!value}});
  }else{setDraft(value==null?'':String(column==='CFL'&&typeof value==='number'?value:column==='RFL'&&typeof value==='number'?`F${value}`:value));setEditing(true);setError('');}}}>
  {displayStrip(value,column)}</button>}</td>;
}
function List({props,dep}:{props:Props;dep:boolean}) {
 const [collapsed,setCollapsed]=useState(false),[closed,setClosed]=useState(false),[filter,setFilter]=useState(false),[query,setQuery]=useState(''),[sort,setSort]=useState(false);
 const host=useRef<HTMLDivElement>(null);
 const aircraft=orderedTraffic(props.state?.aircraft??[],dep,query,sort),columns=dep?departureColumns:arrivalColumns;
 useEffect(()=>{
  if(props.selected && !props.selected.toUpperCase().includes(query.trim().toUpperCase()))setQuery('');
 },[props.selected]);
 useEffect(()=>{
  const row=host.current?.querySelector<HTMLElement>('[aria-selected="true"]');
  const scroller=host.current?.querySelector<HTMLElement>('.strip-scroll');
  if(row&&scroller) {const r=row.getBoundingClientRect(),s=scroller.getBoundingClientRect();
   if(r.bottom>s.bottom)scroller.scrollTop+=r.bottom-s.bottom;
   if(r.top<s.top+24)scroller.scrollTop+=r.top-s.top-24;}
 },[props.selected,query,collapsed,closed]);
 const title=dep?'Departure List':'Arrival List';
 return <section className={`strip-list${closed?' strip-closed':''}`} aria-label={title}>
  <div className="strip-title"><button title="Show all" onClick={()=>{setQuery('');setFilter(false);setSort(false);}}>0</button>
   <button aria-label={`Filter ${title}`} aria-pressed={filter} onClick={()=>setFilter(v=>!v)}>F</button>
   <button aria-label={`Sort ${title} by callsign`} aria-pressed={sort} onClick={()=>setSort(v=>!v)}>S</button>
   <strong>{title}</strong><span>{aircraft.length}</span>
   <button aria-label={`${collapsed?'Expand':'Collapse'} ${title}`} onClick={()=>{setCollapsed(v=>!v);setClosed(false);}}>{collapsed?'▼':'▲'}</button>
   <button aria-label={`${closed?'Restore':'Close'} ${title}`} onClick={()=>setClosed(v=>!v)}>{closed?'↺':'×'}</button>
  </div>
  {filter&&!closed&&<input aria-label={`Filter callsigns in ${title}`} placeholder="Filter callsign" value={query} onChange={e=>setQuery(e.target.value)} />}
  {!closed&&!collapsed&&<div ref={host} className="strip-table-host" style={{height:26+Math.min(5,Math.max(1,aircraft.length))*24}}><div className="strip-scroll"><table><thead><tr>{columns.map(c=><th key={c}>{c}</th>)}</tr></thead>
   <tbody>{aircraft.map(ac=><tr key={ac.callsign} aria-selected={props.selected===ac.callsign} className={ac.transmitting?'strip-transmitting':''}
    onClick={()=>props.onSelect(ac.callsign)}>{columns.map(column=>column==='ACID'?<td key={column}><button className="strip-acid" onClick={()=>props.onSelect(ac.callsign)}>{ac.callsign}</button></td>:<Cell key={column} ac={ac} column={column} props={props}/>)}</tr>)}</tbody></table>
   {!aircraft.length&&<p>{props.state?'No matching aircraft.':'Waiting for simulator.'}</p>}
  </div></div>}
 </section>;
}
export function FlightLists(props:Props){return <div className="flight-lists">
 <List props={props} dep/><List props={props} dep={false}/>
 {(props.state?.aircraft.some(ac=>ac.flight_rules==null))&&<small>Awaiting simulator flight-plan / strip fields · editing enables when supplied.</small>}
 <datalist id="strip-fixes">{Object.keys(props.data?.fixes??{}).map(v=><option key={v} value={v}/>)}</datalist>
 <datalist id="strip-levels">{Array.from({length:43},(_,i)=><option key={i} value={`F${(i+2)*10}`}/>)}</datalist>
 </div>}
