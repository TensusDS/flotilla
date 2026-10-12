// The scene is two parts with one owner each: the building (src/building.js) and the winter around it
// (src/winter.js). This file only puts them together and stands the camera on the ground in front of the building.
import * as THREE from 'three';
import { addBuilding } from './building.js';
import { addWinter } from './winter.js';

export function buildScene(aspect) {
  const scene = new THREE.Scene();
  const building = addBuilding(scene);   // { width, height, depth } in metres; it stands on y = 0, centred on x
  addWinter(scene, building);
  const camera = new THREE.PerspectiveCamera(38, aspect, 1, 60000);
  // A person's eye, far enough to see the whole height, off to one side so the wall runs away into the distance.
  const back = building.height * 2.2 + 60;
  camera.position.set(-building.width * 0.18, 1.7, building.depth / 2 + back);
  camera.lookAt(building.width * 0.08, building.height * 0.42, 0);
  return { scene, camera, building };
}
