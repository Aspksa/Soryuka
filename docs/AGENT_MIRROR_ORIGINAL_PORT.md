# Сорюка — 3D-движок карты памяти

## Задача и происхождение движка

В главной Сорюки используется настоящая WebGL-сцена с туманом, источниками света,
светящимися узлами и физикой графа. Движок адаптирован из открытого исходника,
указанного ниже, а **название ассистента остаётся только Сорюка**.

Source studied and ported:
- [gnufoo/agent-mirror / index.html](https://github.com/gnufoo/agent-mirror/blob/main/index.html) — the original default **LIVE 3D** scene
- [gnufoo/agent-mirror / README.md](https://github.com/gnufoo/agent-mirror/blob/main/README.md)

The previous custom 2D Canvas projection is no longer the default. It remains
as historical source code only. Do not confuse it with the new WebGL engine.

## Реализованный 3D-движок Сорюки

| Piece | Implementation on main homepage |
|---|---|
| Rendering | `THREE.WebGLRenderer`, true GPU-accelerated 3D |
| Depth fog | **`THREE.FogExp2(0x0a0a0f, 0.0008)`** |
| Camera | `THREE.PerspectiveCamera(55)`, OrbitControls, smooth damping |
| Lighting | `THREE.AmbientLight` and two `THREE.DirectionalLight` sources |
| Nodes | `THREE.SphereGeometry`, `THREE.MeshStandardMaterial`, emissive/gloss properties |
| Halo | Original procedural radial glow texture + additive sprite blending |
| Labels | Original CanvasTexture sprites floating in 3D space |
| Physics | `d3-force-3d` 3-dimensional force simulation with springs, charge, center, collisions |
| Edges | `THREE.LineSegments` with dynamic per-vertex RGB buffers |
| Interaction | THREE.Raycaster hover/click, orbit, pan, zoom, highlight neighbors |
| Access animation | Animated emissive pulses, temporary point lights and glowing connection edges |
| Heat map | Node access counts gradually change the color and scale |
| Particle trails | `THREE.CatmullRomCurve3` paths, particles and point lights |
| Debug | Original click-to-pulse debug mode, a **visual test** not actual AI cognition |
| Event feed | Original dark event sidebar, now fed by actual SQLite operations |
| WebSocket | Local same-origin `ws` bridge to Python API, no second AI API |

The original color palette, dark visual layout, fog values, and camera defaults
are kept. Labels were adapted to Russian and the assistant was renamed Soryuka.
Two original CDN scripts, Google font loading, and OpenClaw server dependencies
are **removed**, replaced with local assets/SQLite data.

## No new AI brain or memory database

`core/mirror_graph.py` already serves:
- `GET /api/mirror/graph`: actual local SQLite graph and canon files;
- `GET /api/mirror/activity`: incremental audit and message metadata.

The new `ui/mirror-original.js` contains the original WebGL renderer
adapted through `adaptGraph()` to match Soryuka node IDs, link types and categories.
Soryuka's real graph comes from SQLite, not invented markdown demo data.

The existing Node UI server now serves **only static files and a WebSocket relay**
(`/mirror-ws`) that reads Python's protected API. It does not invent facts,
generate LLM answers or receive the Cloud.ru key in browser JavaScript.
WebSocket clients require loopback + same-origin and are limited to eight.
The relay runs while the single Node frontend is running and streams real events
at roughly 1.8-second intervals.

## Portable first install

Pinned local packages are defined in `ui/package.json`:

- `three@0.160.0` — exact original renderer version
- `d3-force-3d@3.0.6` — original force-directed physics family
- `ws@8.23.0` — a security-patched WebSocket library

When `START-SORYUKA.bat` starts, `launch.py` checks for these
files beneath `ui/node_modules/`. If absent, it runs npm **locally** using
`--ignore-scripts --no-audit --no-fund --omit=dev`. That first bootstrap needs
internet and a Node.js installation that includes npm.

After initialization, keep `ui/node_modules/` alongside the portable project
on the external SSD to run **offline** without installing packages globally.
No Docker, Ollama, Windows services, CDN requests or external WebGL data uploads.

**Important:** Cloud.ru chat still needs network access. The 3D graph, SQLite
memory, old panels and cached embeddings operate locally.

## Настройки 3D-сцены

На главной странице нажмите **«⚙ Настройки графики»**. Ползунки изменяют работающую WebGL-сцену **без перезапуска, потери узлов или облачных запросов**:

| Ползунок | Диапазон | Оригинал | Реальный эффект |
| --- | ---: | ---: | --- |
| Туман | 0–0.003, шаг 0.00005 | 0.0008 | `scene.fog.density` у настоящего `THREE.FogExp2` |
| Освещение | 20–200%, шаг 10% | 100% | Масштаб яркости ambient и обоих directional lights |
| Скорость | 0.25–2.0×, шаг 0.05 | 1.0× | Виртуальное время импульсов/частиц и реальные шаги симуляции `d3-force-3d` |

**Сохранение:** каждый ползунок мгновенно обновляет сцену и браузерную резервную копию (`localStorage`). Через короткую задержку значения записываются в **существующую SQLite** проекта через защищённый API. После повторного запуска, даже если порт изменился, страница загружает сохранённые значения из SQLite. Если локальный Python API недоступен, ползунки продолжают действовать, используя браузерную копию; статус сохранения показывается под кнопкой сброса. При сбросе восстанавливаются оригинальные значения и они записываются в SQLite.

Новые маршруты (уже действующая токенная защита и origin-проверки): `GET /api/mirror/settings`, `POST /api/mirror/settings` (тело `{"settings":{"fog":0.0008,"light":1,"speed":1}}`). Все значения проверяются на допустимые диапазоны и типы; неподходящие отклоняются без изменения базы. Эти настройки не меняют канон, память пользователя или модель Cloud.ru.

Библиотек, Docker и новых процессов это не требует.

## Где находится чат Сорюки?

- `/`: **трёхмерная карта памяти Сорюки** с её SQLite и графическими настройками.
- `/console.html`: unchanged older chat/Academy/settings/personality UX.
- The retired `ui/mirror.js` 2D imitation was removed after confirming no runtime imports; its old revisions remain in Git history.
- `ui/mirror-original.js`: current Three.js WebGL scene with Soryuka data, adaptive FPS and recorded SQLite routes.

## Privacy and truthful visualization

- Graph nodes are actual memory records, confirmed links, event records and
  real character files. Empty memory yields only genuine canon and category nodes.
- The screen can show read/write/search events only when corresponding Python SQLite
  events occur; debug pulses are explicitly marked as a visualization mode.
- No raw private chat text or API token is included in WebSocket event messages.
  Graph node labels may contain the owner's local personal notes and are only
  served over local `127.0.0.1`.
- A glowing graph **does not reveal hidden model reasoning**, objective consciousness
  or genuine emotional experience.

## License and provenance

The original repository README declares MIT while package metadata says ISC.
This PR preserves upstream authorship/attribution and the MIT notice as stated
in the source README. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
Source code copied from the original is explicitly identified as a modified port.

## Validation

The CI workflow runs on **Windows and Ubuntu**:

```powershell
npm install --prefix ui --ignore-scripts --no-audit --no-fund --omit=dev
python -m unittest discover -s tests -p "test_*.py" -v
python character/living_personality_runtime.py
node --check ui/server.mjs
node --check ui/mirror-original.js
```

Integration tests assert that the real WebGL/physics/fog source is used,
vendor assets load from localhost, the WS relay emits a real graph snapshot,
and wrong-origin WS connections are refused.

**Limitation:** CI checks code/HTTP transport but does not visually judge the
rendering on a user's GPU. For final visual QA, launch Soryuka on Windows
and inspect the animated graph in a Chromium-based browser.
