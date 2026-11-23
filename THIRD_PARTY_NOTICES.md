# Attribution and third-party notices

## Original Agent Mirror 3D visualization

Source: https://github.com/gnufoo/agent-mirror

The Soryuka main page now ports the **original default `index.html` WebGL renderer**,
with local adaptations for Soryuka SQLite and WebSocket events. Unlike the earlier
2D imitation, this includes real Three.js fog, physically lit 3D spheres,
`d3-force-3d`, animated light pulses, cross-reference highlighting and particle trails.

The original project's README states the **MIT License**, while its `package.json`
metadata states ISC. Both are permissive, but they are inconsistent. The original
attribution and MIT notice are retained here. The upstream authors retain their
rights. Re-evaluate against upstream licensing if distributing beyond private use.

MIT License

Copyright (c) the original contributors to gnufoo/agent-mirror

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Locally installed browser runtime dependencies

- Three.js **0.160.0**, MIT: https://github.com/mrdoob/three.js
- d3-force-3d **3.0.6**, MIT: https://github.com/vasturiano/d3-force-3d
- ws **8.23.0**, MIT: https://github.com/websockets/ws

All three packages are installed only into `ui/node_modules/` by npm and are
not rewritten or redistributed as Soryuka-authored code. Their original license
and notice files remain in their installed packages.
