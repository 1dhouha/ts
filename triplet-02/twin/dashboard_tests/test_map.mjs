import assert from 'node:assert/strict';
const module=await import('../src/city_dashboard/web/map.js');
assert.equal(typeof module.pointAlong,'function','D06 needs shared road-polyline interpolation');
assert.deepEqual(module.pointAlong([[0,0],[10,0],[10,10]],0),[0,0]);
assert.deepEqual(module.pointAlong([[0,0],[10,0],[10,10]],.5),[10,0]);
assert.deepEqual(module.pointAlong([[10,10],[10,0],[0,0]],1),[0,0]);
console.log('Map path interpolation endpoints and distance passed');
