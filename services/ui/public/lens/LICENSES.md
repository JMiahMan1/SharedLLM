# Face detector files

`blaze_face_short_range.tflite` is MediaPipe's BlazeFace short-range face
detector, published by Google under the Apache License 2.0:

- https://github.com/google-ai-edge/mediapipe
- https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite

`vision_wasm_internal.js` and `vision_wasm_internal.wasm` are copied here at
build time from `node_modules/@mediapipe/tasks-vision/wasm` (same license) by
`scripts/copy-lens-assets.mjs`. They are not committed.

All of it runs on the phone. Pictures are never sent anywhere to find a face.
