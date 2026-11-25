/**
 * The on-device photo editor's logic, for any part of the app that edits a
 * picture (the private chat now; the gallery and the family chat next). It
 * knows nothing about chats, encryption or the server: a Blob comes in, an
 * edited Blob plus its EditRecipe come out, and nothing leaves the phone.
 */
export * from './recipe';
export { FILTERS, filterDef, lookFor, type FilterDef, type Look } from './filters';
export { ASPECTS, aspectRatio, fitCrop, type Frame } from './geometry';
export { applyRecipe, outputSize, type EditFrame, type Limits } from './apply';
export { decodePicture, PhotoEditError, MAX_FRAMES, MAX_TOTAL_PIXELS, STILL_LIMITS, type Decoded } from './decode';
export { exportEdit, needsExport, type EditResult, type Progress } from './encode';
export { editedFile, editedFileName } from './files';
