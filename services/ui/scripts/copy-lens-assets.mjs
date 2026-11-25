#!/usr/bin/env node
// Copies the face detector's WebAssembly code into the app's own static files.
//
// The lens tool runs MediaPipe's face detector on the phone, so the app must
// serve the WebAssembly that runs it itself: nothing about a picture, and no
// request for a model, ever leaves the household. The code comes from the
// installed @mediapipe/tasks-vision package (pinned in package.json) and is
// copied into public/lens/ before every build, so it always matches the
// version the JavaScript is compiled against.
//
// The model itself (a quarter of a megabyte) is committed next to it, so a
// build needs no network at all.
import fs from 'fs'
import path from 'path'

const SRC = path.resolve('node_modules/@mediapipe/tasks-vision/wasm')
const OUT = path.resolve('public/lens')
const FILES = ['vision_wasm_internal.js', 'vision_wasm_internal.wasm']
const MODEL = 'blaze_face_short_range.tflite'

fs.mkdirSync(OUT, { recursive: true })

let copied = 0
for (const name of FILES) {
  const from = path.join(SRC, name)
  if (!fs.existsSync(from)) {
    console.warn(`[lens-assets] ${name} is not in @mediapipe/tasks-vision: run npm install, or the lens tool will say face finding is unavailable and lenses can still be placed by hand.`)
    continue
  }
  fs.copyFileSync(from, path.join(OUT, name))
  copied += 1
}

if (!fs.existsSync(path.join(OUT, MODEL))) {
  console.warn(`[lens-assets] ${MODEL} is missing from public/lens: the lens tool will say face finding is unavailable and lenses can still be placed by hand.`)
}

if (copied === FILES.length) {
  const bytes = FILES.reduce((n, f) => n + fs.statSync(path.join(OUT, f)).size, 0)
  console.log(`[lens-assets] face detector ready in public/lens (${Math.round(bytes / 1024 / 1024)} MB)`)
}
