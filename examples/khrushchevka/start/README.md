# khrushchevka

One picture: a Soviet five-storey panel block - a khrushchevka - grown to the size of a megastructure, in winter.
The storeys keep their real proportions; there are just far too many of them, and the wall runs past the horizon.

The scene is three.js, drawn once by headless Chromium and saved as a PNG.

## The parts

| file | what it owns |
|---|---|
| `src/building.js` | the building: `addBuilding(scene)` adds it standing on `y = 0`, centred on `x = 0`, front facing `+z`, and returns `{ width, height, depth }` in metres |
| `src/winter.js` | everything around it: `addWinter(scene, building)` adds the ground, the sky, the light, the weather |
| `src/scene.js` | puts the two together and stands the camera in front; change it only when the picture needs another view |

Both parts must build without a browser: the test builds the scene in Node, so no `document`, no canvas textures.

## Test

    npm test

## Render

On a rented GPU, through flotilla's rig (about a minute, the picture comes back into `out/`):

    flotilla rig run --get out -- bash tools/render.sh

On this machine, on the CPU (slow on a big scene):

    bash tools/render.sh

A picture you hand over or show is one rendered on the GPU. Look at it before you say it is done.
