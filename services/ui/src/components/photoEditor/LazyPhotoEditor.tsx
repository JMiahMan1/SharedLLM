import { lazy, Suspense } from 'react';
import { Loader2 } from 'lucide-react';

/** The editor and its encoder load only when a picture is first edited. */
const PhotoEditor = lazy(() => import('./PhotoEditor'));

function EditorLoading() {
  return (
    <div className="fixed inset-0 z-[95] flex items-center justify-center bg-black" role="status" aria-label="Opening the editor">
      <Loader2 size={28} className="animate-spin text-white/70" aria-hidden />
    </div>
  );
}

/** The editor, loaded when first shown. Same props as PhotoEditor. */
export default function LazyPhotoEditor(props: Parameters<typeof PhotoEditor>[0]) {
  return (
    <Suspense fallback={<EditorLoading />}>
      <PhotoEditor {...props} />
    </Suspense>
  );
}
