// The winter around the building: snow ground, an overcast dusk sky, fog that swallows the far end of the wall, snowfall,
// soft light, and a few lit windows as glows. addWinter(scene, building) gets the building's size in metres and scales
// everything from it. No DOM and no textures: everything is vertex colours, shader patches and numbers, so the test
// builds the scene in Node. The building's own geometry (and its window geometry) belongs to src/building.js; the lit
// windows here are glow points laid on its front face (+z), nothing the building has to know about.
import * as THREE from 'three';

const FOG_COLOR = 0x9aa3b3;     // also the sky at the horizon and the far snow, so the three meet without a seam
const ZENITH_COLOR = 0x4b5368;  // the dusk overhead, a darker slate
const SNOW_COLOR = 0xdfe5ee;

// scene.js stands the camera here: a person's eye just off the left end of the wall, looking along the front (+x). The
// snowfall is laid out around this spot and along that line; if the camera moves, the picture still works, only the
// flakes' density is tuned for here. (The fog is scene.js's to set for its view: it overrides near and far.)
function expectedEye(b) {
  return new THREE.Vector3(-b.width / 2 - 25, 1.7, b.depth / 2 + 38);
}

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Points whose size is in metres per point (attribute aSize) and whose shape is a soft round disc: `power` shapes
// the falloff (high: a hard flake; low: a wide glow).
// With `dimInFog` the points are dimmed by the scene's fog as it is at render time (fog near/far are uniforms, so a
// later change to scene.fog is followed) instead of being blended to its colour: right for additive glows, which
// shine through haze but must go out in it.
function roundPoints(material, power, key, { dimInFog = false, maxPx = 64 } = {}) {
  material.onBeforeCompile = (shader) => {
    if (dimInFog) {
      shader.fragmentShader = shader.fragmentShader.replace(
        '#include <fog_fragment>',
        `#ifdef USE_FOG
           float lit = 1.0 - smoothstep(fogNear, fogFar, vFogDepth);
           gl_FragColor.a *= lit * lit;
         #endif`,
      );
    }
    shader.vertexShader = shader.vertexShader
      .replace('void main() {', 'attribute float aSize;\nvoid main() {')
      .replace('gl_PointSize = size;', 'gl_PointSize = size * aSize;')
      // A point near the eye would swell to a blot: its size on the screen is capped.
      .replace('#include <logdepthbuf_vertex>', `gl_PointSize = min(gl_PointSize, ${maxPx.toFixed(1)});\n#include <logdepthbuf_vertex>`);
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <map_particle_fragment>',
      `#include <map_particle_fragment>
       float r = length(gl_PointCoord - 0.5) * 2.0;
       if (r > 1.0) discard;
       diffuseColor.a *= pow(1.0 - r, ${power.toFixed(2)});`,
    );
  };
  material.customProgramCacheKey = () => key;
  return material;
}

function sky(radius) {
  const geometry = new THREE.SphereGeometry(radius, 48, 32);
  const pos = geometry.attributes.position;
  const horizon = new THREE.Color(FOG_COLOR);
  const zenith = new THREE.Color(ZENITH_COLOR);
  const colors = new Float32Array(pos.count * 3);
  const c = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const elevation = Math.max(pos.getY(i) / radius, 0);
    // Haze hangs low: up to well above the height the wall's far end reaches, the sky is the fog's own colour, so the
    // fogged-out end of the wall has no silhouette against it.
    c.copy(horizon).lerp(zenith, THREE.MathUtils.smoothstep(elevation, 0.6, 1.0));
    colors.set([c.r, c.g, c.b], i * 3);
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
    vertexColors: true, side: THREE.BackSide, fog: false, depthWrite: false,
  }));
  mesh.renderOrder = -1;
  return mesh;
}

// Snow on the ground: flat, lit like the rest, with soft hollows and drifts painted by noise in world space.
function ground(radius) {
  const material = new THREE.MeshLambertMaterial({ color: SNOW_COLOR });
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace('void main() {', 'varying vec3 vWp;\nvoid main() {')
      .replace('#include <project_vertex>', '#include <project_vertex>\n vWp = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    shader.fragmentShader = shader.fragmentShader
      .replace('void main() {', `varying vec3 vWp;
        // A hash without sin(): sin of a large argument loses its digits and the noise breaks into blocks.
        float h21(vec2 p) {
          vec3 q = fract(vec3(p.xyx) * 0.1031);
          q += dot(q, q.yzx + 33.33);
          return fract((q.x + q.y) * q.z);
        }
        float vnoise(vec2 p) {
          vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
          return mix(mix(h21(i), h21(i + vec2(1, 0)), f.x), mix(h21(i + vec2(0, 1)), h21(i + vec2(1, 1)), f.x), f.y);
        }
        void main() {`)
      .replace('#include <color_fragment>', `#include <color_fragment>
        vec2 w = vWp.xz;
        // The fine octaves fade with distance (they would only alias), to their mean.
        float away = length(w - cameraPosition.xz);
        float fine = 1.0 - smoothstep(40.0, 200.0, away);
        float mid = 1.0 - smoothstep(400.0, 2500.0, away);
        float n = 0.45 * vnoise(w / 260.0) + 0.3 * mix(0.5, vnoise(w / 38.0), mid)
          + 0.17 * mix(0.5, vnoise(w / 5.0), fine) + 0.08 * mix(0.5, vnoise(w / 0.9), fine);
        diffuseColor.rgb *= mix(vec3(0.80, 0.86, 1.0), vec3(1.04, 1.03, 1.0), smoothstep(0.25, 0.8, n));`);
  };
  material.customProgramCacheKey = () => 'winter-ground';
  const mesh = new THREE.Mesh(new THREE.CircleGeometry(radius, 96), material);
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = -0.02;
  return mesh;
}

// Flakes between the camera and the wall, denser and finer near the eye, coarser (about a constant few pixels) far
// away so the fog's depth is full of snow too. A flake is sized by its distance from the eye so it reads at any scale.
function snowfall(b, eye) {
  const rand = rng(7);
  const layers = [
    // count, length along the view (+x), half-width across it, height, flake size in metres, pixel-scale of far flakes
    { n: 7000, dx: 130, hz: 60, hy: 40, base: 0.07, far: 0.0007 },
    { n: 20000, dx: 600, hz: 350, hy: 220, base: 0.3, far: 0.0010 },
    { n: 32000, dx: b.width * 0.85, hz: b.width * 0.45, hy: Math.max(b.height * 0.75, 200), base: 0.5, far: 0.0013 },
  ];
  const total = layers.reduce((s, l) => s + l.n, 0);
  const position = new Float32Array(total * 3);
  const size = new Float32Array(total);
  let k = 0;
  for (const l of layers) {
    for (let i = 0; i < l.n; i++, k++) {
      const x = eye.x + rand() * l.dx;
      const y = rand() * l.hy;
      const z = eye.z + (rand() * 2 - 1) * l.hz;
      position.set([x, y, z], k * 3);
      const d = Math.hypot(x - eye.x, y - eye.y, z - eye.z);
      size[k] = Math.max(l.base, d * l.far) * (0.6 + rand() * 0.9);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(position, 3));
  geometry.setAttribute('aSize', new THREE.BufferAttribute(size, 1));
  const material = roundPoints(new THREE.PointsMaterial({
    color: 0xf4f7fc, size: 1, sizeAttenuation: true, transparent: true, opacity: 0.8, depthWrite: false,
  }), 0.7, 'winter-snow', { maxPx: 7 });
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  return points;
}

// A few lit windows at dusk: warm glow points on the building's own window positions, a hard small core in a soft
// halo each. The fog dims them as it stands at render time (see roundPoints), so the far ones go out in the haze.
function litWindows(b) {
  const rand = rng(1984);
  const count = 360;
  // The building's own window layout (src/building.js): sections of 12 m, storeys of 2.7 m; per storey of a section,
  // each window's [x from the section's centre, y above the floor]. No stair window on the ground floor (the door).
  const section = 12;
  const storey = 2.7;
  const sections = Math.round(b.width / section);
  const storeys = Math.round(b.height / storey);
  const windows = [[-4.2, 1.45], [0, 1.6], [2.5, 1.45], [4.7, 1.45]];
  const warm = [0xffb454, 0xffc978, 0xffa040, 0xffd9a0].map((h) => new THREE.Color(h));
  const kept = [];
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  for (let i = 0; i < count; i++) {
    // Clustered: a lit window draws its neighbours' on, a stretch of a floor now and then, a stack of floors.
    const around = kept.length && rand() < 0.35 ? kept[Math.floor(rand() * kept.length)] : null;
    const s = around ? clamp(around.s + Math.round((rand() * 2 - 1) * 2), 0, sections - 1) : Math.floor(rand() * sections);
    const f = around ? clamp(around.f + Math.round((rand() * 2 - 1) * 3), 0, storeys - 1) : Math.floor(rand() * storeys);
    const k = Math.floor(rand() * windows.length);
    if (f === 0 && k === 1) continue;
    kept.push({
      s, f,
      x: (s - (sections - 1) / 2) * section + windows[k][0],
      y: f * storey + windows[k][1],
      c: warm[Math.floor(rand() * warm.length)], k: 0.45 + rand() * 0.55,
    });
  }
  const position = new Float32Array(kept.length * 3);
  const color = new Float32Array(kept.length * 3);
  const coreSize = new Float32Array(kept.length);
  const haloSize = new Float32Array(kept.length);
  const halo = Math.max(2.5, b.height * 0.015);
  kept.forEach((w, i) => {
    // A metre in front of the face, clear of the balconies, so none hides a glow.
    const p = new THREE.Vector3(w.x, w.y, b.depth / 2 + 1.2);
    position.set([p.x, p.y, p.z], i * 3);
    const g = 1.6;
    color.set([w.c.r * w.k * g, w.c.g * w.k * g, w.c.b * w.k * g], i * 3);
    coreSize[i] = Math.max(1.6, halo * 0.22);
    haloSize[i] = halo;
  });
  const make = (sizes, power, key, blending, maxPx) => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(position, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(color, 3));
    geometry.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1));
    const m = roundPoints(new THREE.PointsMaterial({
      size: 1, sizeAttenuation: true, vertexColors: true, transparent: true, fog: true,
      depthWrite: false, blending,
    }), power, key, { dimInFog: true, maxPx });
    const points = new THREE.Points(geometry, m);
    points.frustumCulled = false;
    return points;
  };
  const group = new THREE.Group();
  group.add(make(haloSize, 2.2, 'winter-halo', THREE.AdditiveBlending, 11));
  group.add(make(coreSize, 0.6, 'winter-core', THREE.AdditiveBlending, 4));
  return group;
}

export function addWinter(scene, building) {
  const eye = expectedEye(building);
  // Clear to a little past the near storeys, thick by the wall's far end. scene.js sets its own near and far for its
  // camera; these are the same idea for the camera it has.
  const fog = new THREE.Fog(FOG_COLOR, 120, building.width * 0.85);
  scene.fog = fog;

  scene.add(sky(Math.min(45000, Math.max(fog.far * 4, 3000))));
  scene.add(ground(Math.min(44000, fog.far * 3)));

  // Overcast dusk: a cool sky light, snow bounce from below, and one very low warm-ish key from the side for form,
  // no shadows (the weather is soft and the picture is one frame).
  scene.add(new THREE.HemisphereLight(0xc4cee2, 0xe6ebf4, 2.7));
  const key = new THREE.DirectionalLight(0xffe0c0, 0.9);
  key.position.set(building.width * 0.6, building.height * 0.25, building.depth * 2 + building.height * 0.8);
  key.target.position.set(0, building.height * 0.3, 0);
  scene.add(key, key.target);

  scene.add(snowfall(building, eye));
  scene.add(litWindows(building));
}
