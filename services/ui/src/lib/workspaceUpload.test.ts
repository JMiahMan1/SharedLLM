import { describe, expect, it, vi } from 'vitest';
import {
  batchSelection,
  selectionFromDataTransfer,
  selectionFromFileList,
  uploadSelection,
  type UploadSelection,
} from './workspaceUpload';

function fileAt(path: string, body = 'x'): File {
  const f = new File([body], path.split('/').pop()!);
  Object.defineProperty(f, 'webkitRelativePath', { value: path });
  return f;
}

// Fake File and Directory Entries API tree.
type Node = { name: string; body?: string; children?: Node[] };
function entryFor(node: Node): unknown {
  if (node.children) {
    // Hand out children in pages of 2, like browsers page readEntries (~100).
    return {
      isFile: false,
      isDirectory: true,
      name: node.name,
      createReader() {
        let i = 0;
        return {
          readEntries(ok: (e: unknown[]) => void) {
            const page = node.children!.slice(i, i + 2).map(entryFor);
            i += 2;
            ok(page);
          },
        };
      },
    };
  }
  return {
    isFile: true,
    isDirectory: false,
    name: node.name,
    file(ok: (f: File) => void) {
      ok(new File([node.body ?? ''], node.name));
    },
  };
}

function dataTransferOf(...roots: Node[]): DataTransfer {
  return {
    items: roots.map((r) => ({ kind: 'file', webkitGetAsEntry: () => entryFor(r), getAsFile: () => null })),
    files: [],
  } as unknown as DataTransfer;
}

describe('selectionFromFileList', () => {
  it('keeps every nested path from a folder pick and lists its folders', () => {
    const sel = selectionFromFileList([fileAt('proj/a.txt'), fileAt('proj/src/deep/b.ts')]);
    expect(sel.items.map((i) => i.path)).toEqual(['proj/a.txt', 'proj/src/deep/b.ts']);
    expect(sel.dirs).toEqual(['proj', 'proj/src', 'proj/src/deep']);
  });

  it('uses the plain name for a multi-file pick', () => {
    const sel = selectionFromFileList([new File(['1'], 'one.txt'), new File(['2'], 'two.txt')]);
    expect(sel.items.map((i) => i.path)).toEqual(['one.txt', 'two.txt']);
    expect(sel.dirs).toEqual([]);
  });

  it('leaves .git out and counts it', () => {
    const sel = selectionFromFileList([fileAt('repo/.git/HEAD'), fileAt('repo/main.py')]);
    expect(sel.items.map((i) => i.path)).toEqual(['repo/main.py']);
    expect(sel.ignored).toBe(1);
  });
});

describe('selectionFromDataTransfer', () => {
  it('walks dropped folders recursively, past the readEntries page size, keeping empty folders', async () => {
    const sel = await selectionFromDataTransfer(
      dataTransferOf(
        {
          name: 'site',
          children: [
            { name: 'index.html' },
            { name: 'a.css' },
            { name: 'b.css' },
            { name: 'img', children: [{ name: 'logo.png' }, { name: 'empty', children: [] }] },
            { name: '.git', children: [{ name: 'HEAD' }] },
          ],
        },
        { name: 'loose.txt' },
      ),
    );
    expect(sel.items.map((i) => i.path).sort()).toEqual([
      'loose.txt',
      'site/a.css',
      'site/b.css',
      'site/img/logo.png',
      'site/index.html',
    ]);
    expect(sel.dirs).toEqual(['site', 'site/img', 'site/img/empty']);
    expect(sel.ignored).toBe(1);
  });
});

describe('batching and upload', () => {
  const sel = (n: number, size: number): UploadSelection => ({
    items: Array.from({ length: n }, (_, i) => ({ file: new File(['x'.repeat(size)], `f${i}`), path: `d/f${i}` })),
    dirs: ['d', 'd/empty'],
    ignored: 0,
  });

  it('splits by file count and by bytes, sending folders with the first batch', () => {
    const byCount = batchSelection(sel(5, 1), 2, 1000);
    expect(byCount.map((b) => b.items.length)).toEqual([2, 2, 1]);
    expect(byCount[0].dirs).toEqual(['d', 'd/empty']);
    expect(byCount[1].dirs).toEqual([]);
    expect(batchSelection(sel(3, 10), 100, 15).map((b) => b.items.length)).toEqual([1, 1, 1]);
  });

  it('a folders-only selection is still one request', () => {
    expect(batchSelection({ items: [], dirs: ['empty'], ignored: 0 })).toHaveLength(1);
  });

  it('adds up results and reports progress to 100%', async () => {
    const send = vi.fn(async (batch: { items: { file: File }[]; dirs: string[] }, onBytes: (n: number) => void) => {
      onBytes(1);
      return {
        uploaded: batch.items.map((i) => ({ size: i.file.size })),
        created_dirs: batch.dirs,
        errors: batch.items.length === 1 ? [{ relative_path: 'd/f4', error: 'boom' }] : [],
      };
    });
    const progress: [number, number][] = [];
    const out = await uploadSelection(sel(201, 2), send, (s, t) => progress.push([s, t]));
    expect(send).toHaveBeenCalledTimes(2);
    expect(out.uploaded).toBe(201);
    expect(out.bytes).toBe(402);
    expect(out.createdDirs).toBe(2);
    expect(out.errors).toHaveLength(1);
    expect(progress.at(-1)).toEqual([402, 402]);
  });
});
