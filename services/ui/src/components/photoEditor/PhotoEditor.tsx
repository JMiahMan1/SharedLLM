import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { Crop, FlipHorizontal2, FlipVertical2, Glasses, Loader2, Minus, Pencil, Plus, RotateCcw, RotateCw, SendHorizontal, SlidersHorizontal, Smile, Trash2, Type, Undo2, Wand2, X } from 'lucide-react';
import { applyRecipe, type EditFrame } from '../../lib/photoEdit/apply';
import { decodePicture, PhotoEditError, type Decoded } from '../../lib/photoEdit/decode';
import { exportEdit, type EditResult } from '../../lib/photoEdit/encode';
import { ASPECTS, aspectRatio, downscale, fitCrop, fitScale, normaliseCrop, orient, type Frame } from '../../lib/photoEdit/geometry';
import {
  addStroke,
  clampScale,
  COLORS,
  contrastOn,
  drawingOf,
  EMOJI,
  FONTS,
  fontFor,
  newOverlayId,
  overBin,
  overlaySize,
  pinchChange,
  raiseOverlay,
  removeOverlay,
  replaceOverlay,
  stickerDataUrl,
  STICKERS,
  thinPoints,
  undoStroke,
} from '../../lib/photoEdit/overlays';
import { emptyRecipe, isIdentity, MAX_OVERLAYS, MAX_TEXT, NO_ADJUSTMENTS, type Adjustments, type AspectId, type CropRect, type EditRecipe, type PlacedOverlay, type Point, type Rotation, type Stroke, type TextOverlay } from '../../lib/photoEdit/recipe';
import { drawStrokes } from '../../lib/photoEdit/render';
import { lensById, lensDataUrl, lensPlacement, LENSES, type PhotoLens } from '../../lib/photoEdit/lenses';
import { FaceFindError, findFaces, type FoundFace } from '../../lib/photoEdit/faceDetect';
import CropBox from './CropBox';
import FilterStrip from './FilterStrip';

export type { EditResult } from '../../lib/photoEdit/encode';

type Tool = 'filters' | 'adjust' | 'crop' | 'lenses' | 'text' | 'stickers' | 'draw';

const TOOLS: ReadonlyArray<{ id: Tool; label: string; Icon: typeof Wand2 }> = [
  { id: 'filters', label: 'Filters', Icon: Wand2 },
  { id: 'adjust', label: 'Adjust', Icon: SlidersHorizontal },
  { id: 'crop', label: 'Crop', Icon: Crop },
  { id: 'lenses', label: 'Lenses', Icon: Glasses },
  { id: 'text', label: 'Text', Icon: Type },
  { id: 'stickers', label: 'Stickers', Icon: Smile },
  { id: 'draw', label: 'Draw', Icon: Pencil },
];

const SLIDERS: ReadonlyArray<{ key: keyof Adjustments; label: string; min: number }> = [
  { key: 'brightness', label: 'Brightness', min: -100 },
  { key: 'contrast', label: 'Contrast', min: -100 },
  { key: 'saturation', label: 'Saturation', min: -100 },
  { key: 'warmth', label: 'Warmth', min: -100 },
  { key: 'vignette', label: 'Vignette', min: 0 },
];

const PEN_SIZES: ReadonlyArray<{ width: number; label: string }> = [
  { width: 0.008, label: 'Thin' },
  { width: 0.016, label: 'Medium' },
  { width: 0.032, label: 'Thick' },
];

/** The preview is drawn from a copy this big at most: quick enough to redraw on every slider step. */
const PREVIEW_SIDE = 1080;
const THUMB_SIDE = 112;

export interface PhotoEditorProps {
  /** The picture to edit. It is never changed; the result carries it back as `original`. */
  source: Blob;
  /** Start from an earlier edit of the same original (to change it, or to show what was done). */
  initialRecipe?: EditRecipe;
  /** The largest result the caller can take, in bytes. */
  maxBytes: number;
  /** The finishing button's words ("Send", "Save"). */
  doneLabel?: string;
  /** The edited picture and its recipe. A throw is shown in the editor, which stays open. */
  onDone: (result: EditResult) => Promise<void> | void;
  /** Offered as "Send as is" (the caller's words in `asIsLabel`): the original, no edit. */
  onAsIs?: () => Promise<void> | void;
  asIsLabel?: string;
  onCancel: () => void;
}

function fit(w: number, h: number, maxW: number, maxH: number): { width: number; height: number } {
  if (w <= 0 || h <= 0 || maxW <= 0 || maxH <= 0) return { width: 0, height: 0 };
  const s = Math.min(maxW / w, maxH / h);
  return { width: Math.floor(w * s), height: Math.floor(h * s) };
}

function shrinkTo(frame: Frame, side: number): Frame {
  const s = fitScale(frame.width, frame.height, side, side * side);
  return s < 1 ? downscale(frame, Math.max(1, Math.round(frame.width * s)), Math.max(1, Math.round(frame.height * s))) : frame;
}

/** Wipe a canvas so no picture stays in the page. */
function blank(canvas: HTMLCanvasElement | null) {
  if (!canvas) return;
  canvas.width = 0;
  canvas.height = 0;
}

interface Gesture {
  id: string;
  pointers: Map<number, Point>;
  start: { overlay: PlacedOverlay; pointers: Map<number, Point> };
  moved: boolean;
}

/**
 * The on-device photo editor: filters, adjustments, crop and turn, words,
 * emoji, stickers and drawing, for stills and (where the phone can read every
 * frame) animated GIFs and WebPs. Everything happens in this page: nothing is
 * sent anywhere while editing, and the original is handed back untouched with
 * the edit and its recipe. It knows nothing about who the picture is for;
 * the caller decides what to do with the result.
 */
export default function PhotoEditor({ source, initialRecipe, maxBytes, doneLabel = 'Send', onDone, onAsIs, asIsLabel = 'Send as is', onCancel }: PhotoEditorProps) {
  const [decoded, setDecoded] = useState<Decoded | null>(null);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState<{ done: number; total: number } | null>(null);
  const [recipe, setRecipe] = useState<EditRecipe>(() => initialRecipe ?? emptyRecipe());
  const [tool, setTool] = useState<Tool>('filters');
  const [selected, setSelected] = useState<string | null>(null);
  const [textDraft, setTextDraft] = useState('');
  const [textStyle, setTextStyle] = useState<Pick<TextOverlay, 'font' | 'color' | 'pill'>>({ font: 'sans', color: '#ffffff', pill: true });
  const [pen, setPen] = useState<{ color: string; width: number }>({ color: '#ffffff', width: PEN_SIZES[1].width });
  const [busy, setBusy] = useState<{ label: string } | null>(null);
  const [error, setError] = useState('');
  const [stage, setStage] = useState({ width: 0, height: 0 });
  const [binHot, setBinHot] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [liveStroke, setLiveStroke] = useState<Stroke | null>(null);
  const [faces, setFaces] = useState<FoundFace[] | null>(null);
  const [lensLooking, setLensLooking] = useState(false);
  const [lensNote, setLensNote] = useState('');

  const stageRef = useRef<HTMLDivElement | null>(null);
  const previewRef = useRef<HTMLCanvasElement | null>(null);
  const drawRef = useRef<HTMLCanvasElement | null>(null);
  const binRef = useRef<HTMLDivElement | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const strokeRef = useRef<{ id: number; stroke: Stroke } | null>(null);
  const textInputRef = useRef<HTMLTextAreaElement | null>(null);

  // Read the picture once.
  useEffect(() => {
    const controller = new AbortController();
    decodePicture(source, { signal: controller.signal, onProgress: (done, total) => setLoading({ done, total }) })
      .then((d) => {
        if (controller.signal.aborted) return;
        setDecoded(d);
        setLoading(null);
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setLoading(null);
        setLoadError(e instanceof PhotoEditError ? e.message : 'This picture cannot be edited on this phone. You can still send it as it is.');
      });
    return () => controller.abort();
  }, [source]);

  // The stage's size, to fit the picture in it.
  useLayoutEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = () => setStage({ width: el.clientWidth, height: el.clientHeight });
    measure();
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    observer?.observe(el);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, []);

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) {
        event.stopPropagation();
        onCancel();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [onCancel, busy]);

  const previewSource = useMemo<EditFrame | null>(() => {
    if (!decoded) return null;
    const first = decoded.frames[0];
    return { ...shrinkTo(first, PREVIEW_SIDE), duration: 0 };
  }, [decoded]);

  /** The picture turned and flipped (the crop tool shows all of it). */
  const oriented = useMemo(() => (previewSource ? orient(previewSource, recipe.rotate, recipe.flipH, recipe.flipV) : null), [previewSource, recipe.rotate, recipe.flipH, recipe.flipV]);
  const thumb = useMemo(() => (oriented ? shrinkTo(oriented, THUMB_SIDE) : null), [oriented]);

  const cropping = tool === 'crop';
  const shown = useMemo<Frame | null>(() => {
    if (!previewSource) return null;
    const [out] = applyRecipe([previewSource], { ...recipe, overlays: [], crop: cropping ? null : recipe.crop });
    return out;
    // Overlays are drawn by the page, not into the picture: they are left out on purpose.
  }, [previewSource, recipe.filter, recipe.adjust, recipe.rotate, recipe.flipH, recipe.flipV, recipe.crop, cropping]); // eslint-disable-line react-hooks/exhaustive-deps

  // Cropping leaves room round the picture for the corner handles, which sit half outside it.
  const margin = cropping ? 56 : 16;
  const box = useMemo(() => (shown ? fit(shown.width, shown.height, stage.width - margin, stage.height - margin) : { width: 0, height: 0 }), [shown, stage, margin]);

  useEffect(() => {
    const canvas = previewRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || !shown) return;
    canvas.width = shown.width;
    canvas.height = shown.height;
    ctx.putImageData(new ImageData(new Uint8ClampedArray(shown.data), shown.width, shown.height), 0, 0);
  }, [shown]);

  // Drawn lines, at the screen's own resolution.
  const drawing = drawingOf(recipe);
  useEffect(() => {
    const canvas = drawRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || !box.width) return;
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    canvas.width = Math.round(box.width * dpr);
    canvas.height = Math.round(box.height * dpr);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawStrokes(ctx, [...(drawing?.strokes ?? []), ...(liveStroke ? [liveStroke] : [])], canvas.width, canvas.height);
  }, [drawing, liveStroke, box.width, box.height]);

  useEffect(() => {
    const preview = previewRef.current;
    const draw = drawRef.current;
    return () => {
      blank(preview);
      blank(draw);
    };
  }, []);

  const update = useCallback((change: (r: EditRecipe) => EditRecipe) => setRecipe((r) => change(r)), []);
  const selectedOverlay = recipe.overlays.find((o) => o.id === selected && o.kind !== 'drawing') as PlacedOverlay | undefined;
  const selectedText = selectedOverlay?.kind === 'text' ? selectedOverlay : null;

  // --- Crop and turn ---------------------------------------------------------------

  const ow = oriented?.width ?? 1;
  const oh = oriented?.height ?? 1;
  const setAspect = (aspect: AspectId) => {
    setFaces(null);
    update((r) => ({ ...r, aspect, crop: normaliseCrop(fitCrop(aspectRatio(aspect), ow, oh)) }));
  };
  const turn = (by: 90 | -90) => {
    setFaces(null);
    update((r) => {
      const rotate = (((r.rotate + by) % 360) + 360) % 360 as Rotation;
      // A quarter turn swaps the sides: start the crop again in the chosen shape.
      return { ...r, rotate, crop: normaliseCrop(fitCrop(aspectRatio(r.aspect), oh, ow)) };
    });
  };
  const flip = (axis: 'h' | 'v') => {
    setFaces(null);
    update((r) => ({
      ...r,
      flipH: axis === 'h' ? !r.flipH : r.flipH,
      flipV: axis === 'v' ? !r.flipV : r.flipV,
      crop: r.crop && (axis === 'h' ? { ...r.crop, x: 1 - r.crop.x - r.crop.w } : { ...r.crop, y: 1 - r.crop.y - r.crop.h }),
    }));
  };
  const cropRect: CropRect = recipe.crop ?? { x: 0, y: 0, w: 1, h: 1 };

  // --- Overlays --------------------------------------------------------------------

  const place = (
    o: Omit<PlacedOverlay, 'id' | 'x' | 'y' | 'scale' | 'rotation'>,
    at: { x: number; y: number; scale: number; rotation: number } = { x: 0.5, y: 0.5, scale: 1, rotation: 0 },
  ) => {
    if (recipe.overlays.length >= MAX_OVERLAYS) {
      setError('That is as many as one picture can carry.');
      return;
    }
    const id = newOverlayId();
    update((r) => ({ ...r, overlays: [...r.overlays, { id, ...at, ...o } as PlacedOverlay] }));
    setSelected(id);
  };

  const addText = () => {
    const text = textDraft.trim() ? textDraft.slice(0, MAX_TEXT) : '';
    if (!text) {
      textInputRef.current?.focus();
      return;
    }
    place({ kind: 'text', text, ...textStyle } as Omit<TextOverlay, 'id' | 'x' | 'y' | 'scale' | 'rotation'>);
    setTextDraft('');
  };

  const changeSelected = (change: (o: PlacedOverlay) => PlacedOverlay) => {
    if (!selected) return;
    update((r) => replaceOverlay(r, selected, (o) => (o.kind === 'drawing' ? o : change(o))));
  };

  const setTextStyleBoth = (style: Partial<Pick<TextOverlay, 'font' | 'color' | 'pill'>>) => {
    setTextStyle((s) => ({ ...s, ...style }));
    if (selectedText) changeSelected((o) => ({ ...o, ...style }) as PlacedOverlay);
  };

  const deleteSelected = () => {
    if (!selected) return;
    update((r) => removeOverlay(r, selected));
    setSelected(null);
  };

  /**
   * A clean copy of the picture as it will be, for face finding: the same
   * turn, crop and size, but without the colour work, which can only make a
   * face harder to find.
   */
  const faceFrame = useCallback((): EditFrame | null => {
    if (!previewSource) return null;
    const [clean] = applyRecipe([previewSource], { ...recipe, filter: null, adjust: { ...NO_ADJUSTMENTS }, overlays: [], crop: cropping ? null : recipe.crop });
    return clean;
  }, [previewSource, recipe.filter, recipe.rotate, recipe.flipH, recipe.flipV, recipe.crop, cropping]); // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * Put a lens on. The faces are found once and remembered for the next lens,
   * so trying three pairs of glasses on one picture reads the picture once.
   * When a face cannot be found (a picture of a pet, a detector this phone
   * cannot run) the lens still lands in the middle, where it can be dragged
   * onto whatever should wear it.
   */
  const addLens = async (lens: PhotoLens) => {
    const frame = faceFrame();
    if (!frame) return;
    setLensNote('');
    let found = faces;
    let note = '';
    if (!found) {
      setLensLooking(true);
      try {
        found = await findFaces(frame);
        setFaces(found);
      } catch (e) {
        note = e instanceof FaceFindError ? e.message : 'Face finding did not work on this phone. Drag the lens onto the face.';
        found = [];
        setFaces([]);
      } finally {
        setLensLooking(false);
      }
    }
    if (!found.length) {
      place({ kind: 'lens', lens: lens.id } as never);
      setLensNote(note || 'No face found: drag the lens onto the face.');
      return;
    }
    for (const face of found) place({ kind: 'lens', lens: lens.id } as never, lensPlacement(lens, face, frame));
    setLensNote(note || (found.length === 1 ? 'Found one face.' : `Found ${found.length} faces.`));
  };

  const stagePoint = (event: PointerEvent<HTMLElement>): Point => [event.clientX, event.clientY];

  const restart = (g: Gesture) => {
    const o = recipe.overlays.find((x) => x.id === g.id);
    if (o && o.kind !== 'drawing') g.start = { overlay: o, pointers: new Map(g.pointers) };
  };

  const onStageDown = (event: PointerEvent<HTMLDivElement>) => {
    if (busy || !box.width) return;
    if (tool === 'draw') {
      const rect = drawRef.current?.getBoundingClientRect();
      if (!rect) return;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      const p: Point = [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
      const stroke = { color: pen.color, width: pen.width, points: [p] };
      strokeRef.current = { id: event.pointerId, stroke };
      setLiveStroke(stroke);
      return;
    }
    if (cropping) return;
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-overlay-id]');
    const g = gesture.current;
    if (target) {
      const id = target.dataset.overlayId!;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      setSelected(id);
      if (g && g.id === id) {
        g.pointers.set(event.pointerId, stagePoint(event));
        restart(g);
        return;
      }
      const o = recipe.overlays.find((x) => x.id === id);
      if (!o || o.kind === 'drawing') return;
      update((r) => raiseOverlay(r, id));
      const pointers = new Map([[event.pointerId, stagePoint(event)]]);
      gesture.current = { id, pointers, start: { overlay: o, pointers: new Map(pointers) }, moved: false };
      return;
    }
    if (g && g.pointers.size === 1) {
      // A second finger anywhere: pinch the overlay the first one holds.
      event.currentTarget.setPointerCapture?.(event.pointerId);
      g.pointers.set(event.pointerId, stagePoint(event));
      restart(g);
      return;
    }
    setSelected(null);
  };

  const onStageMove = (event: PointerEvent<HTMLDivElement>) => {
    const s = strokeRef.current;
    if (s && s.id === event.pointerId) {
      const rect = drawRef.current?.getBoundingClientRect();
      if (!rect) return;
      const p: Point = [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
      s.stroke = { ...s.stroke, points: [...s.stroke.points, p] };
      setLiveStroke(s.stroke);
      return;
    }
    const g = gesture.current;
    if (!g || !g.pointers.has(event.pointerId) || !box.width) return;
    g.pointers.set(event.pointerId, stagePoint(event));
    const start = g.start;
    const [first] = [...g.pointers.entries()];
    let next: PlacedOverlay;
    if (g.pointers.size >= 2) {
      const ids = [...g.pointers.keys()].slice(0, 2);
      const change = pinchChange(
        { a: start.pointers.get(ids[0]) ?? g.pointers.get(ids[0])!, b: start.pointers.get(ids[1]) ?? g.pointers.get(ids[1])! },
        { a: g.pointers.get(ids[0])!, b: g.pointers.get(ids[1])! },
      );
      next = {
        ...start.overlay,
        scale: clampScale(start.overlay.scale * change.scale),
        rotation: start.overlay.rotation + change.rotation,
        x: start.overlay.x + change.dx / box.width,
        y: start.overlay.y + change.dy / box.height,
      };
    } else {
      const from = start.pointers.get(first[0]) ?? first[1];
      const dx = first[1][0] - from[0];
      const dy = first[1][1] - from[1];
      if (!g.moved && Math.hypot(dx, dy) < 4) return;
      next = { ...start.overlay, x: start.overlay.x + dx / box.width, y: start.overlay.y + dy / box.height };
    }
    if (!g.moved) {
      g.moved = true;
      setDragging(true);
    }
    const bin = binRef.current?.getBoundingClientRect() ?? null;
    setBinHot(g.pointers.size === 1 && overBin(first[1][0], first[1][1], bin));
    update((r) => replaceOverlay(r, g.id, () => next));
  };

  const onStageUp = (event: PointerEvent<HTMLDivElement>) => {
    const s = strokeRef.current;
    if (s && s.id === event.pointerId) {
      strokeRef.current = null;
      setLiveStroke(null);
      update((r) => addStroke(r, { ...s.stroke, points: thinPoints(s.stroke.points) }));
      return;
    }
    const g = gesture.current;
    if (!g || !g.pointers.has(event.pointerId)) return;
    const at = stagePoint(event);
    g.pointers.delete(event.pointerId);
    if (g.pointers.size === 0) {
      const bin = binRef.current?.getBoundingClientRect() ?? null;
      if (g.moved && event.type === 'pointerup' && overBin(at[0], at[1], bin)) {
        update((r) => removeOverlay(r, g.id));
        setSelected(null);
      }
      gesture.current = null;
      setDragging(false);
      setBinHot(false);
      return;
    }
    restart(g);
  };

  const overlayKeys = (o: PlacedOverlay) => (event: KeyboardEvent<HTMLElement>) => {
    const step = 0.02;
    const moves: Record<string, Partial<PlacedOverlay>> = {
      ArrowLeft: { x: o.x - step },
      ArrowRight: { x: o.x + step },
      ArrowUp: { y: o.y - step },
      ArrowDown: { y: o.y + step },
      '+': { scale: clampScale(o.scale * 1.15) },
      '=': { scale: clampScale(o.scale * 1.15) },
      '-': { scale: clampScale(o.scale / 1.15) },
      r: { rotation: o.rotation + 15 },
      R: { rotation: o.rotation - 15 },
    };
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      update((r) => removeOverlay(r, o.id));
      setSelected(null);
      return;
    }
    const change = moves[event.key];
    if (!change) return;
    event.preventDefault();
    setSelected(o.id);
    update((r) => replaceOverlay(r, o.id, (x) => ({ ...x, ...change }) as PlacedOverlay));
  };

  // --- Finishing -------------------------------------------------------------------

  const finish = async () => {
    if (!decoded || busy) return;
    setError('');
    setBusy({ label: 'Making the picture' });
    try {
      let result: EditResult;
      if (isIdentity(recipe)) {
        result = { blob: source, mime: source.type, width: decoded.sourceWidth, height: decoded.sourceHeight, animated: decoded.animated, recipe: structuredClone(recipe), original: source };
      } else {
        result = await exportEdit(decoded, recipe, source, {
          maxBytes,
          onProgress: (stage, done, total) => {
            if (total > 1) setBusy({ label: stage === 'encode' ? `Making the GIF: frame ${done} of ${total}` : `Editing frame ${done} of ${total}` });
          },
        });
      }
      setBusy({ label: 'Sending' });
      await onDone(result);
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : 'Could not finish the picture.');
    } finally {
      setBusy(null);
    }
  };

  const asIs = async () => {
    if (!onAsIs || busy) return;
    setError('');
    setBusy({ label: 'Sending' });
    try {
      await onAsIs();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : 'Could not send the picture.');
    } finally {
      setBusy(null);
    }
  };

  // --- Page --------------------------------------------------------------------------

  const notice = decoded?.firstFrameOnly
    ? `This phone can only edit the first frame: the edited picture will be still.${onAsIs ? ` ${asIsLabel} keeps it moving.` : ''}`
    : decoded?.animated
      ? `Animated: your edit goes on all ${decoded.frames.length} frames${decoded.droppedFrames ? ` (the first ${decoded.frames.length}; ${decoded.droppedFrames} more are left out)` : ''}, sent as a GIF.`
      : '';

  const chip = (active: boolean) =>
    `flex min-h-11 shrink-0 items-center justify-center gap-1.5 rounded-full px-3 text-sm font-semibold ${active ? 'bg-white text-slate-900' : 'bg-white/10 text-white'}`;
  const iconButton = 'flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/10 text-white disabled:opacity-40';

  return (
    <div className="fixed inset-0 z-[95] flex flex-col bg-black text-white" role="dialog" aria-modal="true" aria-label="Edit picture" data-testid="photo-editor">
      <div className="flex shrink-0 items-center gap-2 px-3 pt-[max(0.75rem,var(--safe-top))]">
        <button type="button" onClick={onCancel} disabled={!!busy} aria-label="Close the editor" className={iconButton} data-testid="photo-editor-close">
          <X size={20} aria-hidden />
        </button>
        <p className="hidden min-w-0 flex-1 truncate text-center text-sm text-slate-300 sm:block">Edited on this phone</p>
        <span className="flex-1 sm:hidden" aria-hidden />
        {onAsIs && (
          <button type="button" onClick={() => void asIs()} disabled={!!busy} className="min-h-11 shrink-0 rounded-full bg-white/10 px-3 text-sm font-semibold text-white disabled:opacity-40" data-testid="photo-editor-as-is">
            {asIsLabel}
          </button>
        )}
        <button
          type="button"
          onClick={() => void finish()}
          disabled={!decoded || !!busy}
          className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-full bg-rose-600 px-4 text-sm font-semibold text-white disabled:opacity-40"
          data-testid="photo-editor-done"
        >
          {busy ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <SendHorizontal size={16} aria-hidden />} {doneLabel}
        </button>
      </div>
      {notice && (
        <p className="mx-3 mt-2 shrink-0 rounded-lg bg-white/10 px-3 py-1.5 text-center text-xs text-slate-200" data-testid="photo-editor-notice">
          {notice}
        </p>
      )}

      <div
        ref={stageRef}
        className="relative min-h-0 flex-1 touch-none select-none overflow-hidden"
        onPointerDown={onStageDown}
        onPointerMove={onStageMove}
        onPointerUp={onStageUp}
        onPointerCancel={onStageUp}
        data-testid="photo-editor-stage"
      >
        {shown && box.width > 0 && (
          <div
            className={`absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 ${cropping ? '' : 'overflow-hidden'}`}
            style={{ width: box.width, height: box.height }}
            data-testid="photo-editor-picture"
          >
            <canvas ref={previewRef} className="absolute inset-0 h-full w-full" data-testid="photo-editor-preview" aria-label="The picture, as it will look" role="img" />
            <canvas
              ref={drawRef}
              className={`absolute inset-0 h-full w-full ${cropping ? 'hidden' : ''} ${tool === 'draw' ? 'cursor-crosshair' : 'pointer-events-none'}`}
              data-testid="photo-editor-drawing"
              aria-hidden
            />
            {!cropping &&
              recipe.overlays.map((o) => {
                if (o.kind === 'drawing') return null;
                const size = overlaySize(o, box.width);
                const f = o.kind === 'text' ? fontFor(o.font) : null;
                const lens = o.kind === 'lens' ? lensById(o.lens) : null;
                const origin = lens?.origin ?? ([0.5, 0.5] as Point);
                const label =
                  o.kind === 'text'
                    ? `Words: ${o.text}`
                    : o.kind === 'emoji'
                      ? `Emoji ${o.emoji}`
                      : o.kind === 'lens'
                        ? `${lens?.label ?? 'Face'} lens`
                        : `${STICKERS.find((s) => s.id === o.sticker)?.label ?? 'Sticker'} sticker`;
                return (
                  <div
                    key={o.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`${label}. Drag to move, pinch to resize and turn, drag to the bin to remove.`}
                    aria-pressed={selected === o.id}
                    data-overlay-id={o.id}
                    data-testid="photo-editor-overlay"
                    onKeyDown={overlayKeys(o)}
                    onFocus={() => setSelected(o.id)}
                    className={`absolute whitespace-pre text-center leading-[1.2] ${tool === 'draw' ? 'pointer-events-none' : 'cursor-grab'} ${selected === o.id ? 'outline-dashed outline-2 outline-offset-4 outline-white/80' : ''}`}
                    style={{
                      left: o.x * box.width,
                      top: o.y * box.height,
                      transform: `translate(-${origin[0] * 100}%, -${origin[1] * 100}%) rotate(${o.rotation}deg)`,
                      ...(o.kind === 'text'
                        ? {
                            fontFamily: f!.family,
                            fontWeight: f!.weight,
                            fontSize: size,
                            color: o.pill ? contrastOn(o.color) : o.color,
                            background: o.pill ? o.color : undefined,
                            padding: o.pill ? `${size * 0.2}px ${size * 0.45}px` : undefined,
                            borderRadius: o.pill ? Math.min(size * 0.6, size) : undefined,
                            textShadow: o.pill ? undefined : `0 0 ${size * 0.12}px ${contrastOn(o.color) === '#111827' ? 'rgba(0,0,0,0.55)' : 'rgba(255,255,255,0.6)'}`,
                          }
                        : o.kind === 'emoji'
                          ? { fontSize: size, lineHeight: 1 }
                          : o.kind === 'lens'
                            ? { width: size, height: size / (lens?.aspect ?? 1) }
                            : { width: size, height: size }),
                    }}
                  >
                    {o.kind === 'text' ? (
                      o.text
                    ) : o.kind === 'emoji' ? (
                      o.emoji
                    ) : (
                      <img src={(o.kind === 'lens' ? lensDataUrl(o.lens) : stickerDataUrl(o.sticker)) ?? ''} alt="" draggable={false} className="h-full w-full" />
                    )}
                  </div>
                );
              })}
            {cropping && oriented && (
              <CropBox rect={cropRect} ratio={aspectRatio(recipe.aspect)} pictureWidth={ow} pictureHeight={oh} boxWidth={box.width} boxHeight={box.height} onChange={(c) => update((r) => ({ ...r, crop: normaliseCrop(c) }))} />
            )}
          </div>
        )}
        {(dragging || binHot) && (
          <div
            ref={binRef}
            className={`absolute bottom-3 left-1/2 flex h-14 w-14 -translate-x-1/2 items-center justify-center rounded-full ${binHot ? 'scale-125 bg-red-600' : 'bg-black/60'} transition-transform`}
            aria-hidden
            data-testid="photo-editor-bin"
          >
            <Trash2 size={22} />
          </div>
        )}
        {!decoded && !loadError && (
          <p className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-slate-200" role="status">
            <Loader2 size={20} className="animate-spin" aria-hidden />
            {loading && loading.total > 1 ? `Reading frame ${loading.done} of ${loading.total}` : 'Opening the picture'}
          </p>
        )}
        {loadError && (
          <p className="absolute inset-x-4 top-1/2 -translate-y-1/2 rounded-xl border border-amber-400/30 bg-amber-500/15 p-3 text-center text-sm text-amber-100" role="alert" data-testid="photo-editor-load-error">
            {loadError}
          </p>
        )}
        {busy && (
          <p className="absolute inset-x-4 bottom-4 flex items-center justify-center gap-2 rounded-xl bg-black/75 p-3 text-center text-sm text-white" role="status" data-testid="photo-editor-busy">
            <Loader2 size={16} className="animate-spin" aria-hidden /> {busy.label}
          </p>
        )}
      </div>

      {error && (
        <p className="mx-3 mb-1 shrink-0 rounded-lg border border-amber-400/30 bg-amber-500/15 p-2 text-center text-sm text-amber-100" role="alert" data-testid="photo-editor-error">
          {error}
        </p>
      )}

      {decoded && (
        <div className="shrink-0 space-y-2 border-t border-white/10 bg-slate-950/95 pt-2" data-testid="photo-editor-tools">
          {selectedOverlay && !cropping && tool !== 'draw' && (
            <div className="flex items-center justify-center gap-2 px-3" role="group" aria-label="The selected item" data-testid="photo-editor-selected">
              <button type="button" className={iconButton} aria-label="Make it smaller" onClick={() => changeSelected((o) => ({ ...o, scale: clampScale(o.scale / 1.2) }))}>
                <Minus size={18} aria-hidden />
              </button>
              <button type="button" className={iconButton} aria-label="Make it bigger" onClick={() => changeSelected((o) => ({ ...o, scale: clampScale(o.scale * 1.2) }))}>
                <Plus size={18} aria-hidden />
              </button>
              <button type="button" className={iconButton} aria-label="Turn it" onClick={() => changeSelected((o) => ({ ...o, rotation: o.rotation + 15 }))}>
                <RotateCw size={18} aria-hidden />
              </button>
              <button type="button" className={`${iconButton} bg-red-600/80`} aria-label="Remove it" onClick={deleteSelected} data-testid="photo-editor-remove">
                <Trash2 size={18} aria-hidden />
              </button>
            </div>
          )}

          {tool === 'filters' && thumb && <FilterStrip thumb={thumb} value={recipe.filter} onChange={(filter) => update((r) => ({ ...r, filter }))} />}

          {tool === 'adjust' && (
            <div className="grid max-h-44 gap-1 overflow-y-auto px-4" data-testid="photo-editor-adjust">
              {SLIDERS.map((s) => (
                <label key={s.key} className="flex min-h-11 items-center gap-3 text-sm">
                  <span className="w-24 shrink-0 text-slate-200">{s.label}</span>
                  <input
                    type="range"
                    min={s.min}
                    max={100}
                    step={1}
                    value={recipe.adjust[s.key]}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      update((r) => ({ ...r, adjust: { ...r.adjust, [s.key]: v } }));
                    }}
                    aria-label={s.label}
                    data-testid={`photo-editor-slider-${s.key}`}
                    className="h-11 min-w-0 flex-1 accent-rose-500"
                  />
                  <span className="w-10 shrink-0 text-right tabular-nums text-slate-300" aria-hidden>
                    {recipe.adjust[s.key]}
                  </span>
                </label>
              ))}
              <button type="button" className={`${chip(false)} justify-self-center`} onClick={() => update((r) => ({ ...r, adjust: { ...NO_ADJUSTMENTS } }))}>
                Reset adjustments
              </button>
            </div>
          )}

          {tool === 'crop' && (
            <div className="space-y-2 px-3" data-testid="photo-editor-crop">
              <div className="flex gap-2 overflow-x-auto" role="group" aria-label="Crop shape">
                {ASPECTS.map((a) => (
                  <button key={a.id} type="button" aria-pressed={recipe.aspect === a.id} className={chip(recipe.aspect === a.id)} onClick={() => setAspect(a.id)} data-testid={`photo-editor-aspect-${a.id}`}>
                    {a.label}
                  </button>
                ))}
              </div>
              <div className="flex justify-center gap-2" role="group" aria-label="Turn and flip">
                <button type="button" className={iconButton} aria-label="Turn left" onClick={() => turn(-90)} data-testid="photo-editor-rotate-left">
                  <RotateCcw size={18} aria-hidden />
                </button>
                <button type="button" className={iconButton} aria-label="Turn right" onClick={() => turn(90)} data-testid="photo-editor-rotate-right">
                  <RotateCw size={18} aria-hidden />
                </button>
                <button type="button" className={iconButton} aria-label="Flip left to right" aria-pressed={recipe.flipH} onClick={() => flip('h')} data-testid="photo-editor-flip-h">
                  <FlipHorizontal2 size={18} aria-hidden />
                </button>
                <button type="button" className={iconButton} aria-label="Flip upside down" aria-pressed={recipe.flipV} onClick={() => flip('v')} data-testid="photo-editor-flip-v">
                  <FlipVertical2 size={18} aria-hidden />
                </button>
                <button type="button" className={chip(false)} onClick={() => update((r) => ({ ...r, rotate: 0, flipH: false, flipV: false, aspect: 'free', crop: null }))}>
                  Reset
                </button>
              </div>
            </div>
          )}

          {tool === 'text' && (
            <div className="space-y-2 px-3" data-testid="photo-editor-text">
              <div className="flex items-end gap-2">
                <textarea
                  ref={textInputRef}
                  rows={1}
                  maxLength={MAX_TEXT}
                  value={selectedText ? selectedText.text : textDraft}
                  onChange={(e) => {
                    const text = e.target.value.slice(0, MAX_TEXT);
                    if (selectedText) {
                      if (text.trim()) changeSelected((o) => ({ ...o, text }) as PlacedOverlay);
                    } else setTextDraft(text);
                  }}
                  placeholder="Type something"
                  aria-label={selectedText ? 'Change the words' : 'Words to add'}
                  data-testid="photo-editor-text-input"
                  className="min-h-11 min-w-0 flex-1 resize-none rounded-xl border border-white/15 bg-white/10 px-3 py-2.5 text-base text-white placeholder:text-slate-400"
                />
                {!selectedText && (
                  <button type="button" onClick={addText} className={chip(true)} data-testid="photo-editor-text-add">
                    Add
                  </button>
                )}
                {selectedText && (
                  <button type="button" onClick={() => setSelected(null)} className={chip(false)}>
                    New
                  </button>
                )}
              </div>
              <div className="flex gap-2 overflow-x-auto" role="group" aria-label="Font">
                {FONTS.map((f) => {
                  const active = (selectedText?.font ?? textStyle.font) === f.id;
                  return (
                    <button key={f.id} type="button" aria-pressed={active} onClick={() => setTextStyleBoth({ font: f.id })} className={chip(active)} style={{ fontFamily: f.family, fontWeight: f.weight }}>
                      {f.label}
                    </button>
                  );
                })}
                <button
                  type="button"
                  aria-pressed={selectedText?.pill ?? textStyle.pill}
                  onClick={() => setTextStyleBoth({ pill: !(selectedText?.pill ?? textStyle.pill) })}
                  className={chip(selectedText?.pill ?? textStyle.pill)}
                  data-testid="photo-editor-text-pill"
                >
                  Background
                </button>
              </div>
              <Swatches value={selectedText?.color ?? textStyle.color} onChange={(color) => setTextStyleBoth({ color })} label="Text colour" />
            </div>
          )}

          {tool === 'lenses' && (
            <div className="max-h-44 space-y-2 overflow-y-auto px-3" data-testid="photo-editor-lenses">
              <div className="flex items-center gap-2">
                <p className="min-w-0 flex-1 text-xs text-slate-300">
                  {lensLooking ? 'Looking for faces' : lensNote || 'A lens goes on every face it finds. Drag, pinch and turn to fit.'}
                </p>
                <button
                  type="button"
                  className={chip(false)}
                  disabled={lensLooking}
                  onClick={() => {
                    setFaces(null);
                    setLensNote('');
                  }}
                  data-testid="photo-editor-lenses-find"
                >
                  {faces ? 'Look again' : 'Find faces'}
                </button>
              </div>
              <div className="flex flex-wrap gap-1" role="group" aria-label="Face lenses">
                {LENSES.map((l) => (
                  <button
                    key={l.id}
                    type="button"
                    aria-label={`Add the ${l.label.toLowerCase()} lens`}
                    onClick={() => void addLens(l)}
                    disabled={lensLooking}
                    className="flex h-12 w-14 items-center justify-center rounded-xl bg-white/10 px-1 disabled:opacity-40"
                    data-testid={`photo-editor-lens-${l.id}`}
                  >
                    <img src={lensDataUrl(l.id) ?? ''} alt="" className="max-h-9 w-full object-contain" draggable={false} />
                  </button>
                ))}
              </div>
            </div>
          )}

          {tool === 'stickers' && (
            <div className="max-h-40 space-y-2 overflow-y-auto px-3" data-testid="photo-editor-stickers">
              <div className="flex flex-wrap gap-1" role="group" aria-label="Stickers">
                {STICKERS.map((s) => (
                  <button key={s.id} type="button" aria-label={`Add a ${s.label.toLowerCase()} sticker`} onClick={() => place({ kind: 'sticker', sticker: s.id } as never)} className="flex h-12 w-12 items-center justify-center rounded-xl bg-white/10" data-testid={`photo-editor-sticker-${s.id}`}>
                    <img src={stickerDataUrl(s.id) ?? ''} alt="" className="h-9 w-9" />
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap gap-1" role="group" aria-label="Emoji">
                {EMOJI.map((e) => (
                  <button key={e} type="button" aria-label={`Add ${e}`} onClick={() => place({ kind: 'emoji', emoji: e } as never)} className="flex h-12 w-12 items-center justify-center rounded-xl bg-white/10 text-2xl">
                    {e}
                  </button>
                ))}
              </div>
            </div>
          )}

          {tool === 'draw' && (
            <div className="space-y-2 px-3" data-testid="photo-editor-draw">
              <div className="flex items-center gap-2">
                <div className="flex gap-2" role="group" aria-label="Pen size">
                  {PEN_SIZES.map((p) => (
                    <button key={p.label} type="button" aria-pressed={pen.width === p.width} aria-label={`${p.label} pen`} onClick={() => setPen((x) => ({ ...x, width: p.width }))} className={`${iconButton} ${pen.width === p.width ? 'ring-2 ring-white' : ''}`}>
                      <span className="rounded-full bg-white" style={{ width: 4 + p.width * 500, height: 4 + p.width * 500 }} aria-hidden />
                    </button>
                  ))}
                </div>
                <span className="flex-1" />
                <button type="button" className={chip(false)} disabled={!drawing?.strokes.length} onClick={() => update(undoStroke)} aria-label="Undo the last line" data-testid="photo-editor-undo">
                  <Undo2 size={16} aria-hidden /> Undo
                </button>
              </div>
              <Swatches value={pen.color} onChange={(color) => setPen((x) => ({ ...x, color }))} label="Pen colour" />
            </div>
          )}

          <nav className="grid grid-cols-7 gap-1 px-1 pb-[max(0.5rem,var(--safe-bottom))]" aria-label="Editing tools">
            {TOOLS.map(({ id, label, Icon }) => (
              <button
                key={id}
                type="button"
                aria-pressed={tool === id}
                onClick={() => {
                  setTool(id);
                  if (id === 'crop' || id === 'draw') setSelected(null);
                }}
                className={`flex min-h-12 min-w-0 flex-col items-center justify-center gap-0.5 rounded-xl text-[11px] font-semibold ${tool === id ? 'bg-white/15 text-white' : 'text-slate-400'}`}
                data-testid={`photo-editor-tool-${id}`}
              >
                <Icon size={18} aria-hidden />
                <span className="max-w-full truncate">{label}</span>
              </button>
            ))}
          </nav>
        </div>
      )}
      {!decoded && (
        <div className="shrink-0 pb-[max(1rem,var(--safe-bottom))]" />
      )}
    </div>
  );
}

function Swatches({ value, onChange, label }: { value: string; onChange: (color: string) => void; label: string }) {
  return (
    <div className="flex gap-1.5 overflow-x-auto pb-1" role="group" aria-label={label}>
      {COLORS.map((c) => (
        <button
          key={c.value}
          type="button"
          aria-label={c.label}
          aria-pressed={value === c.value}
          onClick={() => onChange(c.value)}
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${value === c.value ? 'ring-2 ring-white' : ''}`}
        >
          <span className="h-7 w-7 rounded-full border border-white/40" style={{ background: c.value }} aria-hidden />
        </button>
      ))}
    </div>
  );
}
