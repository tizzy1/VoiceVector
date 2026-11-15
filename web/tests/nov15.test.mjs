import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
async function compile(path,imports={}) {
 let {outputText}=ts.transpileModule(await readFile(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}});
 for(const [name,url] of Object.entries(imports))outputText=outputText.replaceAll('"'+name+'"',JSON.stringify(url)).replaceAll("'"+name+"'",JSON.stringify(url));
 return 'data:text/javascript;base64,'+Buffer.from(outputText).toString('base64');
}
const geometry=await compile('../src/radar/geometry.ts');
const {parseStrip,stripValue,displayStrip,stripLevel,orderedTraffic}=await import(await compile('../src/ui/stripValues.ts',{'../radar/geometry':geometry}));
const {updateTraces,traceDomain}=await import(await compile('../src/ui/traces.ts'));
test('strip levels keep CFL feet separate from RFL and preserve leading-zero squawks',()=>{
 assert.equal(parseStrip('CFL','F80'),8000);assert.equal(parseStrip('CFL','A40'),4000);
 assert.equal(parseStrip('RFL','FL350'),350);assert.equal(parseStrip('ASSR','0421'),'0421');
 for(const [c,v] of [['ASSR','9999'],['AHDG','361'],['ASPD','50'],['CFL','hello']])assert.throws(()=>parseStrip(c,v));
 const ac={cleared_alt:8000,selected_level:8000,strip:{cfl:7000,clr:false}};
 assert.equal(stripValue(ac,'CFL','26L'),7000);assert.equal(stripLevel(ac),7000);
 assert.equal(displayStrip(stripValue(ac,'CLR','26L'),'CLR'),'—');assert.equal(stripLevel({cleared_alt:8000}),null);
});
test('arrival sequence uses supplied track distance and departure queue order is retained',()=>{
 const ac=[{callsign:'B',flow:'ARR',distance_to_touchdown_nm:20},{callsign:'A',flow:'ARR',distance_to_touchdown_nm:8},{callsign:'Z',flow:'DEP'},{callsign:'C',flow:'DEP'}];
 assert.deepEqual(orderedTraffic(ac,false,'',false).map(a=>a.callsign),['A','B']);
 assert.deepEqual(orderedTraffic(ac,true,'',false).map(a=>a.callsign),['Z','C']);
 assert.deepEqual(orderedTraffic(ac,true,'',true).map(a=>a.callsign),['C','Z']);
});
test('trace updates retain five transmissions, lock finals and preserve negative speech start',()=>{
 const trace={tx_id:'1',speaker:'CTRL',stages:[{t0_ms:-2000,t1_ms:0}],total_ms:600,final:false};
 let history=updateTraces([],trace);history=updateTraces(history,{...trace,final:true});
 assert.equal(updateTraces(history,{...trace,total_ms:900})[0].total_ms,600);
 assert.deepEqual(traceDomain(history[0]),[-2000,600]);
 for(let i=2;i<=7;i++)history=updateTraces(history,{...trace,tx_id:String(i)});
 assert.equal(history.length,5);assert.equal(history[0].tx_id,'3');
 assert.deepEqual(updateTraces(history,{...trace,total_ms:NaN}),history);
});
