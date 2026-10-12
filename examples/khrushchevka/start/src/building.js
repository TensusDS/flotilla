// The building. A placeholder: one grey box. Whoever builds it replaces this file and keeps the contract -
// addBuilding(scene) adds the building standing on y = 0, centred on x = 0, its front facing +z, and returns its
// size in metres. No DOM here: the test builds the scene in Node.
import * as THREE from 'three';

export function addBuilding(scene) {
  const size = { width: 80, height: 15, depth: 12 };
  const box = new THREE.Mesh(
    new THREE.BoxGeometry(size.width, size.height, size.depth),
    new THREE.MeshLambertMaterial({ color: 0x9a9a94 }),
  );
  box.position.y = size.height / 2;
  scene.add(box);
  return size;
}
