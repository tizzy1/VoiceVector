import type { AircraftPublic } from '../protocol';
import { levelLabel } from '../radar/geometry';
export const departureColumns = ['FR','RWY','ACID','TYPE','W','ADEP','ADES','SID','CFL','ASSR','RFL','CLR'];
export const arrivalColumns = ['FR','RWY','ACID','TYPE','W','ADEP','ADES','STAR','CFL','AHDG','ASPD','DCT','ASSR','RFL','DIST','STS'];
const fields: Record<string,string> = {FR:'fr',RWY:'rwy',ADES:'ades',SID:'sid',STAR:'star',CFL:'cfl',ASSR:'assr',RFL:'rfl',CLR:'clr',AHDG:'ahdg',ASPD:'aspd',DCT:'dct',STS:'sts'};
export const stripField = (column:string, departure:boolean) => column === 'ADES' && !departure ? undefined : fields[column];
export function stripValue(ac: AircraftPublic, column:string, runway:string): unknown {
 const field = fields[column];
 if(field && Object.hasOwn(ac.strip ?? {},field)) return ac.strip![field];
 return ({FR:ac.flight_rules,RWY:runway,ACID:ac.callsign,TYPE:ac.actype,W:ac.wake,ADEP:ac.adep,ADES:ac.ades,
  SID:ac.route_name,STAR:ac.route_name,ASSR:ac.assigned_squawk,RFL:ac.rfl} as Record<string,unknown>)[column];
}
export function displayStrip(value:unknown,column:string):string {
 if(value == null || value === '') return '—';
 if(column === 'CFL' && typeof value === 'number') return levelLabel(value);
 if(column === 'RFL' && typeof value === 'number') return `FL${String(value).padStart(3,'0')}`;
 if(column === 'DIST' && typeof value === 'number') return value.toFixed(1);
 if(column === 'CLR') return value ? '✓' : '—';
 return String(value);
}
export function parseStrip(column:string, raw:string):unknown {
 const value=raw.trim().toUpperCase();
 if(!value || value==='-') return null;
 if(column==='CFL' || column==='RFL') {
  const match=/^(A|FL|F)?(\d{1,5})$/.exec(value);
  if(!match) throw Error('Use A40, F80 or a level in feet.');
  const n=Number(match[2]), ft=match[1]==='F'||match[1]==='FL' ? n*100 : n<1000?n*100:n;
  if(ft<1000 || ft>45000) throw Error('Level must be 1,000–45,000 ft.');
  return column==='RFL'?ft/100:ft;
 }
 if(column==='ASSR' && !/^[0-7]{4}$/.test(value)) throw Error('Squawk must be four octal digits.');
 if(column==='ADES' && !/^[A-Z]{4}$/.test(value)) throw Error('Use a four-letter ICAO airport.');
 if(column==='AHDG' || column==='ASPD') {
  const n=Number(value), min=column==='AHDG'?1:100,max=column==='AHDG'?360:350;
  if(!/^\d+$/.test(value) || n<min || n>max) throw Error(`Enter ${min}–${max}.`);
  return n;
 }
 return value;
}
export function stripLevel(ac:AircraftPublic): number | null {
 const value=ac.strip?.cfl;
 return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
export function orderedTraffic(aircraft:readonly AircraftPublic[],dep:boolean,query:string,sort:boolean) {
 const list=aircraft.filter(ac=>(ac.flow==='DEP')===dep && ac.phase!=='LANDED' && ac.phase!=='HANDED_OFF'
  && ac.callsign.toUpperCase().includes(query.trim().toUpperCase()));
 if(sort) list.sort((a,b)=>a.callsign.localeCompare(b.callsign));
 // Arrival DIST is a simulator measurement; never replace it with straight-line distance.
 else if(!dep) list.sort((a,b)=>Number(a.distance_to_touchdown_nm ?? Infinity)-Number(b.distance_to_touchdown_nm ?? Infinity));
 return list;
}
