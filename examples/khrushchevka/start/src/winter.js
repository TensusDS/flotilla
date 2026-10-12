// The winter around the building. A placeholder: a flat grey sky and one light. Whoever builds it replaces this file
// and keeps the contract - addWinter(scene, building) gets the building's size in metres and adds the ground, the
// sky, the light and the weather. No DOM here: the test builds the scene in Node.
import * as THREE from 'three';

export function addWinter(scene, building) {
  scene.background = new THREE.Color(0x8d939b);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x666666, 2.2));
}
