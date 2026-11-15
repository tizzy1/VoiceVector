import { useEffect, useRef } from "react";
import { Deck, MapView, type Layer, type MapViewState, type PickingInfo } from "@deck.gl/core";
import { LineLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { SimpleMeshLayer } from "@deck.gl/mesh-layers";
import { Map as MapLibreMap, setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import type { AircraftPublic, StateMsg } from "../protocol";
import { MotionBuffer } from "../radar/motion";
import { CameraDirector, aircraftPose, initialPose, overviewPose, type CameraMode, type CameraPose } from "./camera";
import { activeAircraft, aircraftMesh, FlightHistory, modelMatrix, position, wrapSpeech, type SceneAirspace, type Vec3 } from "./sceneData";

interface Props {
  state: StateMsg | null; data: SceneAirspace; selected: string; mode: CameraMode; revision: number;
  speakers: readonly string[]; labels: boolean; trails: boolean; context: boolean;
  onSelect: (callsign: string) => void; onManual: () => void; onFailure: () => void;
  onTarget: (callsign: string) => void;
  speech?: { callsign: string; text: string };
}
interface GroundPath { path: Vec3[]; color: [number, number, number, number] }
const mesh = aircraftMesh();
setWorkerUrl(workerUrl);
const cool = [134, 193, 209, 255] as const, warm = [242, 181, 68, 255] as const;

function groundLayers(data: SceneAirspace, runway: string): Layer[] {
  const paths: GroundPath[] = [];
  for (let n = -60; n <= 60; n += 5) {
    paths.push({ path: [position(n, -60, 0, data), position(n, 60, 0, data)], color: [51, 78, 88, 45] });
    paths.push({ path: [position(-60, n, 0, data), position(60, n, 0, data)], color: [51, 78, 88, 45] });
  }
  for (const radius of [5, 10, 20, 40]) paths.push({
    path: Array.from({ length: 97 }, (_, i) => position(Math.sin(i * Math.PI / 48) * radius,
      Math.cos(i * Math.PI / 48) * radius, 0, data)), color: [88, 115, 122, 100],
  });
  for (const area of data.airspace) for (const segment of area.segments) paths.push({
    path: segment.map(([x, y]) => position(x, y, 0, data)), color: [77, 103, 112, 80],
  });
  for (const kind of ["STAR", "SID"] as const) for (const procedure of Object.values(data.procedures[kind][runway] ?? {})) {
    const path = procedure.route.flatMap(name => data.fixes[name] ? [position(...data.fixes[name].xy_nm, 0, data)] : []);
    if (path.length > 1) paths.push({ path, color: kind === "STAR" ? [82, 149, 168, 95] : [176, 138, 75, 85] });
  }
  const a = data.runways[runway]?.threshold.xy_nm ?? [0, 0];
  const elevation = data.runways[runway]?.elevation_ft ?? 0;
  const b = data.runways[runway === "26L" ? "08R" : "26L"]?.threshold.xy_nm ?? [-1.5, -.3];
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const dx = (b[0] - a[0]) / length, dy = (b[1] - a[1]) / length;
  const polygon = [position(a[0] - dy * .025, a[1] + dx * .025, elevation, data),
    position(b[0] - dy * .025, b[1] + dx * .025, elevation, data),
    position(b[0] + dy * .025, b[1] - dx * .025, elevation, data),
    position(a[0] + dy * .025, a[1] - dx * .025, elevation, data)];
  const slopeHeight = 10 * 1852 * Math.tan(3 * Math.PI / 180) / .3048;
  const ramp = [position(a[0] - dy * .04, a[1] + dx * .04, elevation, data),
    position(a[0] + dy * .04, a[1] - dx * .04, elevation, data),
    position(a[0] - dx * 10 + dy * .4, a[1] - dy * 10 - dx * .4, elevation + slopeHeight, data),
    position(a[0] - dx * 10 - dy * .4, a[1] - dy * 10 + dx * .4, elevation + slopeHeight, data)];
  const marks = ["WILLO", "TIMBA", "GWC"].flatMap(name => data.fixes[name] ? [{ name, point: position(...data.fixes[name].xy_nm, 0, data) }] : []);
  return [
    new PathLayer<GroundPath>({ id: "blueprint-grid", data: paths, getPath: d => d.path, getColor: d => d.color, getWidth: 1, widthUnits: "pixels" }),
    new PolygonLayer({ id: "runway", data: [{ polygon }], getPolygon: d => d.polygon, getFillColor: [117, 146, 155, 210], stroked: true, getLineColor: [211, 226, 231], getLineWidth: 1, lineWidthUnits: "pixels" }),
    new PolygonLayer({ id: "glideslope", data: [{ polygon: ramp }], getPolygon: d => d.polygon, getFillColor: [124, 200, 183, 23], stroked: true, getLineColor: [124, 200, 183, 85], getLineWidth: 1, lineWidthUnits: "pixels" }),
    new TextLayer({ id: "airport-label", data: [{ name: `GATWICK / ${runway}`, point: position((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, elevation, data) }], getPosition: d => d.point, getText: d => d.name, getColor: [183, 204, 210], getSize: 11, getPixelOffset: [0, 16], fontFamily: "JetBrains Mono", characterSet: "auto" }),
    new TextLayer({ id: "fix-labels", data: marks, getPosition: d => d.point, getText: d => d.name, getColor: [113, 144, 155], getSize: 10, fontFamily: "JetBrains Mono" }),
  ];
}

export default function Scene(props: Props) {
  const host = useRef<HTMLDivElement>(null), base = useRef<HTMLDivElement>(null), canvas = useRef<HTMLCanvasElement>(null);
  const input = useRef(props);
  input.current = props;
  useEffect(() => {
    let live = true, frame = 0, map: MapLibreMap | undefined, deck: Deck<MapView> | undefined;
    let lastState: StateMsg | null = null, groundRunway = "", grounds: Layer[] = [], trailLayers: Layer[] = [];
    let overview = { ...initialPose }, overviewRevision = -1, firstState = true, lastTarget = "";
    const motion = new MotionBuffer(), history = new FlightHistory(), camera = new CameraDirector();
    const reduce = matchMedia("(prefers-reduced-motion: reduce)");
    const fail = () => { if (live) input.current.onFailure(); };
    const pick = (info: PickingInfo) => {
      const callsign = info.object?.callsign;
      if (typeof callsign === "string") input.current.onSelect(callsign);
    };
    let renders = 0, fpsAt = performance.now();
    try {
      map = new MapLibreMap({ container: base.current!, interactive: false, attributionControl: false, maxPitch: 75,
        center: [initialPose.longitude, initialPose.latitude], zoom: initialPose.zoom, pitch: initialPose.pitch,
        bearing: initialPose.bearing, style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#111518" } }] } });
      deck = new Deck<MapView>({ canvas: canvas.current!, parent: host.current!, views: new MapView({ id: "airspace", farZMultiplier: 10 }),
        initialViewState: { ...initialPose, maxPitch: 75, minZoom: 7, maxZoom: 16 },
        controller: { dragRotate: true, touchRotate: true, keyboard: false }, useDevicePixels: Math.min(devicePixelRatio, 1.5),
        deviceProps: { type: "webgl" }, pickingRadius: 9,
        getCursor: ({ isDragging, isHovering }) => isDragging ? "grabbing" : isHovering ? "pointer" : "grab",
        onClick: pick, onError: fail,
        onAfterRender: () => {
          renders++;
          const now = performance.now();
          if (now - fpsAt >= 1000) {
            if (host.current) host.current.dataset.renderFps = String(Math.round(renders * 1000 / (now - fpsAt)));
            renders = 0; fpsAt = now;
          }
        },
        onViewStateChange: ({ viewState, interactionState }) => {
          if (interactionState.isDragging || interactionState.isZooming || interactionState.isRotating) {
            const next = viewState as MapViewState;
            camera.manual({ ...camera.pose, ...next, pitch: next.pitch ?? 55, bearing: next.bearing ?? 0,
              position: next.position ?? camera.pose.position });
            input.current.onManual();
          }
        },
      });
    } catch { fail(); }
    const observer = new ResizeObserver(([entry]) => {
      map?.resize(); deck?.setProps({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(host.current!);
    function draw(now: number) {
      if (!live || !deck) return;
      const p = input.current, state = p.state;
      if (state !== lastState) {
        const rewound = state && lastState && state.t < lastState.t;
        motion.update(state, now); history.update(state);
        if (rewound || !state) { firstState = true; overviewRevision = -1; }
        lastState = state;
        if (state) trailLayers = [new LineLayer({ id: "flight-trails", data: history.segments(state, p.data),
          getSourcePosition: d => d.start, getTargetPosition: d => d.end, getColor: d => d.color, getWidth: 1.5 })];
      }
      const aircraft = activeAircraft(motion.sample(now));
      if (state && (firstState || (p.mode === "overview" && p.revision !== overviewRevision))) {
        overview = overviewPose(aircraft, p.data, host.current!.clientWidth, host.current!.clientHeight);
        overviewRevision = p.revision; firstState = false;
      }
      const runway = state?.runway ?? "26L";
      if (runway !== groundRunway) { groundRunway = runway; grounds = groundLayers(p.data, runway); }
      const speakers = [...p.speakers, ...aircraft.filter(ac => ac.transmitting).map(ac => ac.callsign)];
      const targetId = p.mode === "radio" ? camera.radioTarget(speakers, aircraft, now)
        : p.mode === "departure" ? aircraft.find(ac => ac.flow === "DEP")?.callsign ?? "" : p.selected;
      const target = aircraft.find(ac => ac.callsign === targetId);
      if (lastTarget !== (target?.callsign ?? "")) { lastTarget = target?.callsign ?? ""; p.onTarget(lastTarget); }
      let pose: CameraPose = camera.pose;
      if (p.mode !== "free") pose = camera.step(target && p.mode !== "overview"
        ? aircraftPose(target, p.data, p.mode === "chase" || p.mode === "departure") : overview,
      `${p.mode}:${targetId}:${p.revision}`, now, reduce.matches);
      const point = (ac: AircraftPublic) => position(ac.x, ac.y, ac.alt, p.data);
      const color = (ac: AircraftPublic): [number, number, number, number] => ac.callsign === p.selected
        ? [107, 224, 242, 255] : speakers.includes(ac.callsign) ? [124, 240, 180, 255]
          : [...(ac.flow === "DEP" ? warm : cool)];
      const pixelsToMetres = 156543.03392 * Math.cos(pose.latitude * Math.PI / 180) / Math.pow(2, pose.zoom);
      const matrix = (ac: AircraftPublic) => modelMatrix(ac.hdg,
        Math.max(1, pixelsToMetres * (ac.callsign === p.selected && p.mode !== "overview" ? 92 : 31) / 41));
      const labels = [...aircraft].sort((a, b) => Number(b.callsign === p.selected) - Number(a.callsign === p.selected)
        || Math.hypot(a.x - (target?.x ?? 0), a.y - (target?.y ?? 0)) - Math.hypot(b.x - (target?.x ?? 0), b.y - (target?.y ?? 0))).slice(0, 10);
      const layers: Layer[] = [
        ...(p.context ? grounds : grounds.filter(layer => layer.id === "runway" || layer.id === "airport-label")),
        ...(p.trails ? trailLayers : []),
        new LineLayer<AircraftPublic>({ id: "altitude-stems", data: aircraft, getSourcePosition: ac => position(ac.x, ac.y, 0, p.data), getTargetPosition: point, getColor: ac => [...color(ac).slice(0, 3), ac.callsign === p.selected ? 140 : 45] as [number, number, number, number], getWidth: 1 }),
        new ScatterplotLayer<AircraftPublic>({ id: "ground-fixes", data: aircraft, getPosition: ac => position(ac.x, ac.y, 0, p.data), getRadius: 3, radiusUnits: "pixels", stroked: true, filled: false, getLineColor: ac => [...color(ac).slice(0, 3), 100] as [number, number, number, number], lineWidthMinPixels: 1 }),
        new SimpleMeshLayer<AircraftPublic>({ id: "aircraft-bodies", data: aircraft, mesh, getPosition: point, getTransformMatrix: matrix, getColor: ac => ac.callsign === p.selected ? [47, 76, 85] : [32, 46, 53], material: { ambient: 1, diffuse: 0, shininess: 0 }, pickable: true }),
        new SimpleMeshLayer<AircraftPublic>({ id: "aircraft-blueprints", data: aircraft, mesh, wireframe: true, getPosition: point, getTransformMatrix: matrix, getColor: color, material: { ambient: 1, diffuse: 0, shininess: 0 }, pickable: true }),
        new ScatterplotLayer<AircraftPublic>({ id: "selection-ring", data: aircraft.filter(ac => ac.callsign === p.selected || speakers.includes(ac.callsign)), getPosition: point, getRadius: ac => ac.callsign === p.selected && p.mode !== "overview" ? 66 : 26, radiusUnits: "pixels", stroked: true, filled: false, getLineColor: color, getLineWidth: 1, lineWidthUnits: "pixels", billboard: true, pickable: true }),
        ...(p.labels ? [new TextLayer<AircraftPublic>({ id: "aircraft-labels", data: labels, getPosition: point,
          getText: ac => aircraft.length > 12 && ac.callsign !== p.selected && !speakers.includes(ac.callsign) ? ac.callsign
            : `${ac.callsign}${speakers.includes(ac.callsign) ? "  / ON AIR" : ""}\n${Math.round(ac.alt).toLocaleString("en-GB")} ft · ${Math.round(ac.gs)} kt`,
          getColor: color, getSize: 11, getPixelOffset: ac => [0, ac.callsign === p.selected && p.mode !== "overview" ? 66 : 28],
          fontFamily: "JetBrains Mono", characterSet: "auto", background: true, getBackgroundColor: [17, 21, 24, 210], backgroundPadding: [5, 4], pickable: true })] : []),
        ...(p.speech?.text ? [new TextLayer<AircraftPublic>({ id: "pilot-speech", data: aircraft.filter(ac => ac.callsign === p.speech?.callsign),
          getPosition: point, getText: () => wrapSpeech(p.speech?.text ?? ""),
          getColor: [196, 232, 209], getSize: 11, getPixelOffset: [0, -65], getAlignmentBaseline: "bottom",
          fontFamily: "Inter", characterSet: "auto", background: true, getBackgroundColor: [17, 30, 23, 235], backgroundPadding: [8, 6] })] : []),
      ];
      deck.setProps({ viewState: { ...pose, maxPitch: 75, minZoom: 7, maxZoom: 16 }, layers });
      map?.jumpTo({ center: [pose.longitude, pose.latitude], zoom: pose.zoom, pitch: pose.pitch, bearing: pose.bearing });
      frame = requestAnimationFrame(draw);
    }
    frame = requestAnimationFrame(draw);
    return () => { live = false; cancelAnimationFrame(frame); observer.disconnect(); deck?.finalize(); map?.remove(); };
  }, [props.data]);
  return <div className="viz-scene" ref={host} onContextMenu={event => event.preventDefault()}>
    <div className="viz-basemap" ref={base} aria-hidden="true" />
    <canvas ref={canvas} className="viz-canvas" aria-label="3D Gatwick airspace. Click aircraft to lock. Drag to pan, right-drag to orbit and scroll to zoom." role="application" tabIndex={0} />
  </div>;
}
