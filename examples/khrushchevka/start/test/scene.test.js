import test from 'node:test';
import assert from 'node:assert/strict';
import { buildScene } from '../src/scene.js';

test('the scene builds without a browser and holds something to look at', () => {
  const { scene, camera, building } = buildScene(16 / 9);
  assert.ok(scene.children.length > 0);
  assert.ok(building.width > 0 && building.height > 0 && building.depth > 0);
  assert.ok(camera.position.z > building.depth / 2, 'the camera stands in front of the building');
});
