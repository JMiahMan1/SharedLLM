// Collect files (and folder trees) the user picked or dropped, and send them
// to a workspace in batches. Works for every workspace, git or Nextcloud.

export interface UploadItem {
  file: File;
  /** Path relative to the upload target, e.g. "photos/2024/a.jpg". */
  path: string;
}

export interface UploadSelection {
  items: UploadItem[];
  /** Every folder in the selection, including empty ones. */
  dirs: string[];
  /** Files left out because they sit inside a .git folder (the server refuses them). */
  ignored: number;
}

const isGitPath = (path: string) => path.split('/').includes('.git');

function addParentDirs(path: string, dirs: Set<string>) {
  const parts = path.split('/');
  for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join('/'));
}

/**
 * From an <input type="file" multiple> or <input webkitdirectory>.
 * A folder picker sets webkitRelativePath ("myfolder/sub/a.txt") on every file
 * in the tree, at any depth. It cannot report EMPTY folders - only drag and
 * drop can.
 */
export function selectionFromFileList(files: FileList | File[]): UploadSelection {
  const items: UploadItem[] = [];
  const dirs = new Set<string>();
  let ignored = 0;
  for (const file of Array.from(files)) {
    const path = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    if (isGitPath(path)) {
      ignored++;
      continue;
    }
    items.push({ file, path });
    addParentDirs(path, dirs);
  }
  return { items, dirs: [...dirs].sort(), ignored };
}

// Minimal typings for the File and Directory Entries API (drag and drop).
interface FsEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
}
interface FsFileEntry extends FsEntry {
  file(ok: (f: File) => void, err: (e: unknown) => void): void;
}
interface FsDirectoryEntry extends FsEntry {
  createReader(): { readEntries(ok: (entries: FsEntry[]) => void, err: (e: unknown) => void): void };
}

async function readAllEntries(dir: FsDirectoryEntry): Promise<FsEntry[]> {
  // readEntries returns at most ~100 entries per call; keep calling until it
  // returns none, or large folders silently lose files.
  const reader = dir.createReader();
  const all: FsEntry[] = [];
  for (;;) {
    const batch = await new Promise<FsEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (batch.length === 0) return all;
    all.push(...batch);
  }
}

async function walkEntry(entry: FsEntry, prefix: string, out: UploadSelection): Promise<void> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.name === '.git') {
    out.ignored++;
    return;
  }
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => (entry as FsFileEntry).file(resolve, reject));
    out.items.push({ file, path });
  } else if (entry.isDirectory) {
    out.dirs.push(path);
    for (const child of await readAllEntries(entry as FsDirectoryEntry)) {
      await walkEntry(child, path, out);
    }
  }
}

/** From a drop event: files and whole folder trees, recursively, empty folders included. */
export async function selectionFromDataTransfer(dt: DataTransfer): Promise<UploadSelection> {
  const out: UploadSelection = { items: [], dirs: [], ignored: 0 };
  // Grab every entry synchronously: the DataTransfer is cleared once the
  // drop handler yields.
  const entries: FsEntry[] = [];
  const looseFiles: File[] = [];
  for (const item of Array.from(dt.items ?? [])) {
    if (item.kind !== 'file') continue;
    const entry = (item as DataTransferItem & { webkitGetAsEntry?: () => FsEntry | null }).webkitGetAsEntry?.();
    if (entry) entries.push(entry);
    else {
      const f = item.getAsFile();
      if (f) looseFiles.push(f);
    }
  }
  if (entries.length === 0 && looseFiles.length === 0 && dt.files) looseFiles.push(...Array.from(dt.files));
  for (const entry of entries) await walkEntry(entry, '', out);
  for (const file of looseFiles) out.items.push({ file, path: file.name });
  out.dirs.sort();
  return out;
}

export interface UploadBatch {
  items: UploadItem[];
  dirs: string[];
}

/** Split into requests the server accepts (it caps files per request and total bytes). */
export function batchSelection(
  sel: UploadSelection,
  maxFiles = 200,
  maxBytes = 64 * 1024 * 1024,
): UploadBatch[] {
  const batches: UploadBatch[] = [];
  let current: UploadBatch = { items: [], dirs: sel.dirs };
  let bytes = 0;
  for (const item of sel.items) {
    const full = current.items.length >= maxFiles || (current.items.length > 0 && bytes + item.file.size > maxBytes);
    if (full) {
      batches.push(current);
      current = { items: [], dirs: [] };
      bytes = 0;
    }
    current.items.push(item);
    bytes += item.file.size;
  }
  if (current.items.length > 0 || current.dirs.length > 0) batches.push(current);
  return batches;
}

export interface UploadOutcome {
  uploaded: number;
  skipped: number;
  createdDirs: number;
  bytes: number;
  errors: { relative_path: string; error: string }[];
}

export interface UploadBatchResult {
  uploaded?: { size: number }[];
  skipped?: unknown[];
  created_dirs?: string[];
  errors?: { relative_path: string; error: string }[];
}

/** Send every batch in order, reporting progress as (bytes sent, total bytes). */
export async function uploadSelection(
  sel: UploadSelection,
  send: (batch: UploadBatch, onBytes: (sent: number) => void) => Promise<UploadBatchResult>,
  onProgress?: (sent: number, total: number) => void,
): Promise<UploadOutcome> {
  const total = sel.items.reduce((n, i) => n + i.file.size, 0);
  const outcome: UploadOutcome = { uploaded: 0, skipped: 0, createdDirs: 0, bytes: 0, errors: [] };
  let done = 0;
  for (const batch of batchSelection(sel)) {
    const batchBytes = batch.items.reduce((n, i) => n + i.file.size, 0);
    const res = await send(batch, (sent) => onProgress?.(done + Math.min(sent, batchBytes), total));
    done += batchBytes;
    onProgress?.(done, total);
    outcome.uploaded += res.uploaded?.length ?? 0;
    outcome.bytes += (res.uploaded ?? []).reduce((n, u) => n + (u.size ?? 0), 0);
    outcome.skipped += res.skipped?.length ?? 0;
    outcome.createdDirs += res.created_dirs?.length ?? 0;
    outcome.errors.push(...(res.errors ?? []));
  }
  return outcome;
}
