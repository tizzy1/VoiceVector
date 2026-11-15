import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
async function compile(path,imports={}) {
 let {outputText}=ts.transpileModule(await readFile(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}});
 outputText=outputText.replace(/import ['"]\.\/forecast.css['"];?/,'');
 for(const [name,url] of Object.entries(imports))outputText=outputText.replaceAll('"'+name+'"',JSON.stringify(url)).replaceAll("'"+name+"'",JSON.stringify(url));
 return 'data:text/javascript;base64,'+Buffer.from(outputText).toString('base64');
}
const scene=await compile('../src/viz/sceneData.ts');
const logic=await compile('../src/viz/forecasts.ts',{'./sceneData':scene});
const {forecastStatus,visibleForecasts,forecastGeometry,forecastHasLoss,ForecastCrossings,forecastTime}=await import(logic);
const {ForecastPanel}=await import(await compile('../src/viz/ForecastPanel.tsx',{'./forecasts':logic,'react':import.meta.resolve('react'),'react/jsx-runtime':import.meta.resolve('react/jsx-runtime')}));
const f=(changes={})=>({pair:['A','B'],p_conflict:.625,t_conflict_s:80,min_sep_nm:1.8,min_sep_ft:300,rollouts:64,horizon_s:120,computed_t:10,samples:[],...changes});
const state=(forecasts=[f()],changes={})=>({t:10,forecasts,forecasts_available:true,forecasts_computed_t:10,alerts:[],aircraft:[],...changes});
test('only fresh pairs over threshold are sorted, deduplicated and capped to five',()=>{
 const rows=[f({p_conflict:.14}),...Array.from({length:7},(_,i)=>f({pair:[String(i),'B'],p_conflict:(i+2)/10})),f({p_conflict:NaN})];
 assert.deepEqual(visibleForecasts(state(rows)).map(r=>r.pair[0]),['6','5','4','3','2']);
 assert.equal(visibleForecasts(state([f(),f({pair:['B','A']})])).length,1);
 assert.equal(visibleForecasts(state([f()],{t:16})).length,0);
 assert.equal(forecastStatus(null),'disconnected');
 assert.equal(forecastStatus(state([],{forecasts_available:false})),'unavailable');
 assert.equal(forecastStatus(state([],{forecasts_computed_t:null})),'waiting');
});
test('panel preserves server measurements and separates loss alerts from probabilities',()=>{
 let s=state();
 let html=renderToStaticMarkup(createElement(ForecastPanel,{state:s}));
 assert.match(html,/63%/);assert.match(html,/1:20/);assert.match(html,/1.8 NM/);assert.match(html,/300 ft/);
 assert.match(html,/ADVISORY/);assert.doesNotMatch(html,/LOSS OF SEPARATION/);
 s.alerts=[{kind:'STCA_LOS',callsigns:['B','A']}];
 assert.equal(forecastHasLoss(f(),s),true);
 assert.match(renderToStaticMarkup(createElement(ForecastPanel,{state:s})),/LOSS OF SEPARATION/);
 assert.match(renderToStaticMarkup(createElement(ForecastPanel,{state:state([])})),/No conflicts forecast/);
 assert.match(renderToStaticMarkup(createElement(ForecastPanel,{state:state([],{t:20})})),/stale/);
 assert.equal(forecastTime(119.8),'2:00');assert.equal(forecastTime(null),'—');
});
test('3D fans swap lat/lon and convert feet to metres without inventing missing altitude',()=>{
 const sample={callsign:'A',points:[[51,-.2,7000],[51.01,-.19,6000]]};
 const data=forecastGeometry(state([f({samples:[sample,{callsign:'B',points:[[51,-.21,7300],[51.01,-.18,6300]]}]})]));
 assert.equal(data.paths.length,2);assert.equal(data.markers.length,1);
 assert.deepEqual(data.paths[0].path[0],[-.2,51,7000*.3048]);assert.equal(data.highRisk.has('A'),true);
 assert.equal(forecastGeometry(state([f()])).paths.length,0);
 assert.equal(forecastGeometry(state([f({samples:[{callsign:'A',points:[[1,2],[2,3]]}]})])).paths.length,0);
 assert.equal(forecastGeometry(null).markers.length,0);
});
test('threshold feed emits once per crossing and clears on reset or disconnect',()=>{
 const tracker=new ForecastCrossings();
 assert.equal(tracker.update(state([f({p_conflict:.49})])).length,0);
 assert.equal(tracker.update(state()).length,1);
 assert.equal(tracker.update(state()).length,0);
 tracker.update(state([]));assert.equal(tracker.update(state()).length,1);
 tracker.update(null);assert.equal(tracker.update(state()).length,1);
 assert.equal(tracker.update(state([f({computed_t:0})],{t:0,forecasts_computed_t:0})).length,1);
});
