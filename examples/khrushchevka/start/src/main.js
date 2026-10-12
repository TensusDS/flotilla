// The page: one frame of the scene, then a flag the renderer script waits for.
import * as THREE from 'three';
import { buildScene } from './scene.js';

try {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.setSize(window.innerWidth, window.innerHeight);
  document.body.appendChild(renderer.domElement);
  const { scene, camera } = buildScene(window.innerWidth / window.innerHeight);
  renderer.render(scene, camera);
  window.__rendered = true;
} catch (err) {
  window.__error = String(err && err.stack || err);
}
