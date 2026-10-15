import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, FileUp, Loader2, ScrollText } from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';

const SOURCE_URL = 'https://github.com/STEPBible/STEPBible-Data/tree/master/Translators%20Amalgamated%20OT%2BNT';

/**
 * Install a word-by-word interlinear from STEPBible's openly licensed tagged
 * texts. Readers see it for every verse it covers; elsewhere the verse menu
 * opens Blue Letter Bible's interlinear instead.
 */
export default function InterlinearAdminSection() {
  const client = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const [log, setLog] = useState<string[]>([]);
  const coverage = useQuery({ queryKey: ['bible-interlinear-coverage'], queryFn: () => api.getBibleInterlinearCoverage() });

  const upload = useMutation({
    // One file at a time: each is a whole testament section, and the report
    // for each says which books it replaced.
    mutationFn: async (picked: File[]) => {
      const lines: string[] = [];
      for (const file of picked) {
        try {
          const report = await api.uploadBibleInterlinear(file);
          lines.push(`${file.name}: ${report.message}`);
        } catch (e) {
          lines.push(`${file.name}: ${errorText(e, 'import failed')}`);
        }
        setLog([...lines]);
      }
      return lines;
    },
    onSettled: () => {
      setFiles([]);
      void client.invalidateQueries({ queryKey: ['bible-interlinear-coverage'] });
    },
  });

  const books = coverage.data?.books ?? [];
  const greek = books.filter((b) => b.language === 'greek').length;
  const hebrew = books.filter((b) => b.language === 'hebrew').length;

  return (
    <section className="space-y-2" data-testid="bible-admin-interlinear">
      <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
        <ScrollText size={13} /> Interlinear (Greek &amp; Hebrew)
      </h3>
      <div className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2.5 space-y-2">
        <p className="text-xs text-slate-300" data-testid="bible-admin-interlinear-coverage">
          {coverage.isError
            ? `Could not read the interlinear: ${errorText(coverage.error)}`
            : books.length
              ? (
                <span className="flex items-center gap-1.5">
                  <CheckCircle2 size={12} className="text-emerald-300" />
                  {coverage.data!.verse_count.toLocaleString()} verses · {greek} NT and {hebrew} OT books
                </span>
              )
              : 'None installed. Verses open Blue Letter Bible’s interlinear until one is.'}
        </p>
        <p className="text-[11px] text-slate-500">
          Upload the TAGNT (New Testament) and TAHOT (Old Testament) .txt files from{' '}
          <a href={SOURCE_URL} target="_blank" rel="noopener noreferrer" className="text-sky-300 hover:text-sky-200">
            STEPBible-Data
          </a>{' '}
          (CC BY 4.0). Six files cover the whole Bible; re-uploading one replaces those books.
        </p>
        <input
          type="file"
          accept=".txt,.tsv"
          multiple
          aria-label="Interlinear files"
          data-testid="bible-admin-interlinear-file"
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
          className="block w-full min-h-11 text-xs text-slate-300 file:mr-3 file:min-h-11 file:rounded-lg file:border-0 file:bg-white/10 file:px-3 file:text-xs file:text-slate-200"
        />
        <button
          type="button"
          onClick={() => {
            setLog([]);
            upload.mutate(files);
          }}
          disabled={upload.isPending || files.length === 0}
          data-testid="bible-admin-interlinear-go"
          className="w-full min-h-11 rounded-xl text-xs font-medium bg-amber-500/20 text-amber-200 hover:bg-amber-500/30 disabled:opacity-50 flex items-center justify-center gap-1.5"
        >
          {upload.isPending ? <Loader2 size={13} className="animate-spin" /> : <FileUp size={13} />}
          {upload.isPending ? 'Importing…' : files.length > 1 ? `Import ${files.length} files` : 'Import'}
        </button>
        {log.length > 0 && (
          <pre className="max-h-40 overflow-auto text-[10px] leading-relaxed whitespace-pre-wrap text-slate-400" data-testid="bible-admin-interlinear-log">
            {log.join('\n')}
          </pre>
        )}
      </div>
    </section>
  );
}
