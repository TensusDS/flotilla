// The building: a khrushchevka (five-storey panel block) scaled up to a megastructure - the same storey, the same
// entrance section, repeated until it is hundreds of storeys tall and kilometres wide. Contract: addBuilding(scene)
// adds the building standing on y = 0, centred on x = 0, its front facing +z, and returns its size in metres.
// No DOM, no canvas textures: the test builds the scene in Node.
//
// How it stays cheap: the wall is one box. Everything on it comes from one "storey of one entrance section" -
// a handful of small merged geometries, drawn as instances, one InstancedMesh per material. Windows are instances
// of their own so each can be lit or dark.
import * as THREE from 'three';

const STOREY = 2.7;          // m, floor to floor
const SECTION = 12;          // m, one entrance section (a "podyezd") across the front
const DEPTH = 12;            // m, the block's depth
const FLOORS = 300;          // storeys
const SECTIONS = 250;        // entrance sections along the wall
const LIT = 0.1;             // share of windows with the light on

const COLORS = {
  wall: 0xb9b5aa, plinth: 0x8c8a84, seam: 0x77746d, frame: 0xe6e4dc, parapet: 0xa29e93,
  slab: 0xc4c0b5, door: 0x3a2f28, canopy: 0x8a867d, roof: 0x4d4b48, dark: 0x1b2836,
};

// A box of the given size whose centre is at (x, y, z), as a geometry ready to merge.
function box(w, h, d, x, y, z) {
  const g = new THREE.BoxGeometry(w, h, d);
  g.translate(x, y, z);
  return g;
}

// Windows of one storey of one section on the front (+z) wall, x from the section's centre, y from its floor:
// [centre x, centre y, width, height].
const WINDOWS = [
  [-4.2, 1.45, 1.5, 1.3],   // room
  [0, 1.6, 0.7, 1.0],       // the stair
  [2.5, 1.45, 1.1, 1.3],    // kitchen
  [4.7, 1.45, 1.5, 1.3],    // room
];
const FACE = DEPTH / 2;     // z of the wall's front face
const BALCONY_X = [-4.2, 4.7];

// The page serves only three's build, not its examples, so the merge is written out: boxes share their attributes.
function mergeGeometries(parts) {
  const out = new THREE.BufferGeometry();
  const total = (name) => parts.reduce((n, g) => n + g.getAttribute(name).array.length, 0);
  for (const name of ['position', 'normal', 'uv']) {
    const array = new Float32Array(total(name));
    const itemSize = parts[0].getAttribute(name).itemSize;
    let at = 0;
    for (const g of parts) { array.set(g.getAttribute(name).array, at); at += g.getAttribute(name).array.length; }
    out.setAttribute(name, new THREE.BufferAttribute(array, itemSize));
  }
  const index = [];
  let base = 0;
  for (const g of parts) {
    for (const i of g.index.array) index.push(base + i);
    base += g.getAttribute('position').count;
  }
  out.setIndex(index);
  return out;
}

function merged(parts, color) {
  return { geometry: mergeGeometries(parts), material: new THREE.MeshLambertMaterial({ color }) };
}

function addInstances(scene, { geometry, material }, offsets) {
  const count = offsets.length / 3;
  if (!count) return;
  const mesh = new THREE.InstancedMesh(geometry, material, count);
  const m = mesh.instanceMatrix.array;
  for (let i = 0; i < count; i++) {
    const o = i * 16;
    m[o] = m[o + 5] = m[o + 10] = m[o + 15] = 1;
    m[o + 12] = offsets[i * 3];
    m[o + 13] = offsets[i * 3 + 1];
    m[o + 14] = offsets[i * 3 + 2];
  }
  mesh.instanceMatrix.needsUpdate = true;
  mesh.computeBoundingSphere();
  scene.add(mesh);
}

// A small deterministic generator, so the picture is the same every time.
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function addBuilding(scene) {
  const width = SECTIONS * SECTION;
  const height = FLOORS * STOREY;
  const rand = rng(1957);

  // The wall, the plinth, the roof: a few big boxes.
  const body = merged([box(width, height, DEPTH, 0, height / 2, 0)], COLORS.wall);
  const plinth = merged([box(width + 0.4, 0.5, DEPTH + 0.4, 0, 0.25, 0)], COLORS.plinth);
  const roof = merged([
    box(width + 0.4, 0.6, DEPTH + 0.4, 0, height + 0.3, 0),               // the parapet
    box(width * 0.98, 1.2, DEPTH * 0.6, 0, height + 1.2, -DEPTH * 0.1),   // the roof's own bulk
  ], COLORS.roof);
  for (const part of [body, plinth, roof]) scene.add(new THREE.Mesh(part.geometry, part.material));

  // One storey of one section, in pieces that share a material.
  const seams = merged([
    box(SECTION, 0.1, 0.06, 0, 0.05, FACE),                                    // the floor joint
    ...[-6, -2.4, 3.5].map((x) => box(0.08, STOREY, 0.06, x, STOREY / 2, FACE)),   // the panel joints
  ], COLORS.seam);
  const frames = merged(WINDOWS.map(([x, y, w, h]) => box(w + 0.22, h + 0.22, 0.12, x, y, FACE)), COLORS.frame);
  const slabs = merged(BALCONY_X.map((x) => box(2.4, 0.14, 1.0, x, 0.05, FACE + 0.5)), COLORS.slab);
  const parapets = merged(BALCONY_X.flatMap((x) => [
    box(2.4, 0.95, 0.08, x, 0.6, FACE + 1.0),
    box(0.08, 0.95, 1.0, x - 1.16, 0.6, FACE + 0.5),
    box(0.08, 0.95, 1.0, x + 1.16, 0.6, FACE + 0.5),
  ]), COLORS.parapet);
  // One glass geometry per window, so each window is lit or dark on its own.
  const glass = WINDOWS.map(([x, y, w, h]) => box(w, h, 0.06, x, y, FACE + 0.08));
  // The ground storey: an entrance in place of the stair window, under a canopy, on two steps.
  const entrance = merged([
    box(1.5, 2.1, 0.1, 0, 1.05, FACE + 0.02),                          // the door
    box(0.3, 0.1, 0.1, 0, 2.5, FACE + 0.02),                                 // the sign
  ], COLORS.door);
  const canopy = merged([
    box(2.6, 0.14, 1.3, 0, 2.62, FACE + 0.65),
    box(2.2, 0.3, 1.0, 0, 0.15, FACE + 0.5),
  ], COLORS.canopy);

  const off = { seams: [], frames: [], slabs: [], parapets: [], entrances: [] };
  const dark = glass.map(() => []);
  const lit = glass.map(() => []);
  for (let s = 0; s < SECTIONS; s++) {
    const x = (s - (SECTIONS - 1) / 2) * SECTION;
    off.entrances.push(x, 0, 0);
    for (let f = 0; f < FLOORS; f++) {
      const y = f * STOREY;
      off.seams.push(x, y, 0);
      off.frames.push(x, y, 0);
      for (let w = 0; w < WINDOWS.length; w++) {
        if (f === 0 && w === 1) continue;   // the entrance stands where the stair window would be
        (rand() < LIT ? lit[w] : dark[w]).push(x, y, 0);
      }
      if (f > 0) { off.slabs.push(x, y, 0); off.parapets.push(x, y, 0); }
    }
  }

  addInstances(scene, seams, off.seams);
  addInstances(scene, frames, off.frames);
  addInstances(scene, slabs, off.slabs);
  addInstances(scene, parapets, off.parapets);
  const darkMaterial = new THREE.MeshLambertMaterial({ color: COLORS.dark });
  const litMaterial = new THREE.MeshBasicMaterial({ color: 0xffd48a });   // lit windows are the light, not lit by it
  glass.forEach((geometry, w) => {
    addInstances(scene, { geometry, material: darkMaterial }, dark[w]);
    addInstances(scene, { geometry, material: litMaterial }, lit[w]);
  });
  addInstances(scene, entrance, off.entrances);
  addInstances(scene, canopy, off.entrances);

  return { width, height, depth: DEPTH };
}
