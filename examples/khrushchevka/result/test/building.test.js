import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { addBuilding } from '../src/building.js';

test('the building is a megastructure that stands on y = 0, centred on x = 0', () => {
  const scene = new THREE.Scene();
  const size = addBuilding(scene);
  assert.ok(size.height / 2.7 >= 100, 'hundreds of storeys');
  assert.ok(size.width >= 1000, 'kilometres wide');
  const box = new THREE.Box3().setFromObject(scene);
  assert.ok(Math.abs(box.min.y) < 0.01);
  assert.ok(Math.abs(box.min.x + box.max.x) < 1);
  assert.ok(box.max.z <= size.depth / 2 + 2 && box.max.z > size.depth / 2, 'the front is +z');
});
