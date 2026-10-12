// The scene is two parts with one owner each: the building (src/building.js) and the winter around it
// (src/winter.js). This file only puts them together and stands the camera on the ground in front of the building.
import * as THREE from 'three';
import { addBuilding } from './building.js';
import { addWinter } from './winter.js';

const FOV = 66;   // degrees, vertical

export function buildScene(aspect) {
  const scene = new THREE.Scene();
  const building = addBuilding(scene);   // { width, height, depth } in metres; it stands on y = 0, centred on x
  addWinter(scene, building);
  const camera = new THREE.PerspectiveCamera(FOV, aspect, 1, 60000);
  // A person's eye, standing in the snow just off one end of the wall, looking along the front face: the near storeys
  // are big enough to read (windows, balconies, an entrance door) and the wall runs away into the fog.
  const end = -building.width / 2;
  camera.position.set(end - 25, 1.7, building.depth / 2 + 38);
  camera.lookAt(end + 420, building.height * 0.3, 0);
  // winter.js lays its fog out for a camera far in front of the wall; from here the wall's far end is its length away,
  // so the fog starts a little past the near storeys and is thick before that end.
  scene.fog.near = 120;
  scene.fog.far = building.width * 0.85;
  return { scene, camera, building };
}
