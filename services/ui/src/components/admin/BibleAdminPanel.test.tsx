import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleAdminPanel from './BibleAdminPanel';
import type { BibleEditionInfo, BibleImportsResponse, BibleProviderEstimate } from '../../types/api';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({
  getBibleImports: vi.fn(),
  getBibleProviderTranslations: vi.fn(),
  getBibleProviderEstimate: vi.fn(),
  runBibleImport: vi.fn(),
  uploadBibleImport: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

const NKJV = {
  code: 'nkjv',
  name: 'New King James Version',
  language: 'en',
  license_class: 'licensed',
  rights_holder: 'Thomas Nelson',
  installed: true,
  verse_count: 31102,
  imported_at: '2026-01-01T00:00:00',
  editions: 1,
  primary: true,
  provider: '',
  note: 'Copyrighted. Do not redistribute.',
};

const ESV = {
  code: 'esv',
  name: 'English Standard Version',
  language: 'en',
  license_class: 'licensed',
  rights_holder: 'Crossway',
  installed: false,
  verse_count: null,
  imported_at: null,
  editions: 0,
  primary: false,
  provider: 'api.bible',
  note: 'ESV is copyrighted, so its text is not bundled. Install it from the api.bible provider.',
};

const EDITION: BibleEditionInfo = {
  code: 'nkjv-tmn',
  version: 'nkjv',
  name: 'NKJV Study Bible',
  publisher: 'Thomas Nelson',
  language: 'en',
  license_class: 'licensed',
  rights_holder: 'Thomas Nelson',
  installed: true,
  note_count: 44336,
  note_kinds: ['commentary', 'footnote'],
  note: '',
};

const PROVIDER_OK = {
  code: 'api.bible',
  title: 'api.bible online translations',
  base_url: 'https://api.bible',
  requires: 'bible_api_key',
  configured: true,
  reason: '',
  note: 'Translations are fetched on demand and cached like any other.',
};

const PROVIDER_OFF = {
  ...PROVIDER_OK,
  configured: false,
  reason: 'api.bible online translations needs the bible_api_key setting.',
};

function catalogue(over: Partial<BibleImportsResponse> = {}): BibleImportsResponse {
  return {
    default_version: 'nkjv',
    primary: 'nkjv',
    versions: [NKJV, ESV],
    editions: [EDITION],
    providers: [PROVIDER_OK],
    kinds: ['json', 'pdf', 'epub'],
    import_dir: '/data/bible',
    import_dir_error: '',
    runs: [],
    ...over,
  };
}

function renderPanel(body: BibleImportsResponse | Error = catalogue()) {
  if (body instanceof Error) apiMock.getBibleImports.mockRejectedValue(body);
  else apiMock.getBibleImports.mockResolvedValue(body);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BibleAdminPanel />
    </QueryClientProvider>,
  );
}

describe('BibleAdminPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.getBibleImports.mockResolvedValue(catalogue());
    apiMock.runBibleImport.mockResolvedValue({
      source: '/data/bible/esv.pdf',
      kind: 'pdf',
      code: 'esv',
      name: 'English Standard Version',
      provider: '',
      provider_id: '',
      status: 'succeeded',
      message: 'Installed English Standard Version: 31,102 verses across 66 books.',
      verse_count: 31102,
      book_count: 66,
      note_count: null,
      log: ['Exodus: 40 chapters, 153 verses'],
      duration_ms: 812,
      created_at: '2026-01-02T00:00:00',
    });
  });

  it('lists what is installed and marks the primary translation', async () => {
    renderPanel();
    expect(await screen.findByTestId('bible-admin')).toBeTruthy();
    const row = await screen.findByTestId('bible-admin-version-nkjv');
    expect(row.textContent).toContain('New King James Version');
    expect(row.textContent).toContain('Primary');
    expect(row.textContent).toContain('31,102 verses');
    expect(row.textContent).toContain('NKJV Study Bible');
    expect(row.textContent).toContain('44,336 notes');
    expect(screen.getByTestId('bible-admin-version-esv').textContent).toContain('Not installed');
  });

  it('explains an uninstalled translation using its provider', async () => {
    renderPanel();
    expect(await screen.findByTestId('bible-admin-version-esv')).toHaveTextContent(
      /Install it from the api\.bible provider/,
    );
  });

  it('says what to set when no import directory is configured', async () => {
    renderPanel(catalogue({ import_dir: '', import_dir_error: 'BIBLE_IMPORT_DIR is not configured.' }));
    expect(await screen.findByTestId('bible-admin-import-dir-error')).toHaveTextContent(
      'BIBLE_IMPORT_DIR is not configured.',
    );
  });

  it('names the setting an unconfigured provider needs instead of listing it', async () => {
    renderPanel(catalogue({ providers: [PROVIDER_OFF] }));
    const card = await screen.findByTestId('bible-admin-provider-api.bible');
    expect(card.textContent).toContain('needs the bible_api_key setting');
    expect(screen.queryByTestId('bible-admin-provider-open-api.bible')).toBeNull();
  });

  it('lists a provider’s translations and installs the chosen one', async () => {
    apiMock.getBibleProviderTranslations.mockResolvedValue({
      provider: PROVIDER_OK,
      translations: [
        { id: 'ESV', name: 'English Standard Version', language: 'eng', license_class: 'licensed', rights_holder: 'Crossway', note: '' },
      ],
      count: 1,
    });
    renderPanel();
    const card = await screen.findByTestId('bible-admin-provider-api.bible');
    await userEvent.click(within(card).getByTestId('bible-admin-provider-open-api.bible'));
    expect(await within(card).findByText('English Standard Version')).toBeTruthy();
    await userEvent.click(within(card).getByText('Install'));
    await waitFor(() =>
      expect(apiMock.runBibleImport).toHaveBeenCalledWith({
        code: 'esv',
        kind: 'json',
        name: 'English Standard Version',
        provider: 'api.bible',
        provider_id: 'ESV',
      }),
    );
  });

  it('reports a provider that cannot be reached in its own words', async () => {
    apiMock.getBibleProviderTranslations.mockRejectedValue(new Error('api.bible returned HTML, not JSON'));
    renderPanel();
    await userEvent.click(await screen.findByTestId('bible-admin-provider-open-api.bible'));
    expect(await screen.findByTestId('bible-admin-provider-error-api.bible')).toHaveTextContent(
      'api.bible returned HTML, not JSON',
    );
  });

  it('installs from a path already on the server', async () => {
    renderPanel();
    await userEvent.type(await screen.findByTestId('bible-admin-path-code'), 'esv');
    await userEvent.type(screen.getByTestId('bible-admin-path-file'), '/data/bible/esv.pdf');
    await userEvent.click(screen.getByTestId('bible-admin-path-go'));
    await waitFor(() =>
      expect(apiMock.runBibleImport).toHaveBeenCalledWith({
        code: 'esv',
        kind: 'pdf',
        source_path: '/data/bible/esv.pdf',
      }),
    );
  });

  it('keeps the install button disabled until a path and a code are both given', async () => {
    renderPanel();
    const go = await screen.findByTestId('bible-admin-path-go');
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByTestId('bible-admin-path-code'), 'esv');
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByTestId('bible-admin-path-file'), '/data/bible/esv.pdf');
    expect(go).not.toBeDisabled();
  });

  it('uploads a file with its code and asks for the study notes', async () => {
    apiMock.uploadBibleImport.mockResolvedValue({
      source: 'nkjv.epub',
      kind: 'epub',
      code: 'nkjv',
      name: '',
      provider: '',
      provider_id: '',
      status: 'succeeded',
      message: 'Installed NKJV Study Bible: 31,102 verses, 44,336 study notes.',
      verse_count: 31102,
      book_count: 66,
      note_count: 44336,
      log: ['Uploaded nkjv.epub'],
      duration_ms: 65000,
      created_at: '2026-01-03T00:00:00',
    });
    renderPanel();
    const input = (await screen.findByTestId('bible-admin-upload-file')) as HTMLInputElement;
    const file = new File(['PK'], 'nkjv.epub', { type: 'application/epub+zip' });
    await userEvent.upload(input, file);
    await userEvent.type(screen.getByTestId('bible-admin-upload-code'), 'nkjv');
    await userEvent.type(screen.getByTestId('bible-admin-upload-edition'), 'nkjv-tmn');
    await userEvent.click(screen.getByTestId('bible-admin-upload-notes'));
    await userEvent.click(screen.getByTestId('bible-admin-upload-go'));
    await waitFor(() => expect(apiMock.uploadBibleImport).toHaveBeenCalled());
    const form = apiMock.uploadBibleImport.mock.calls[0][0] as FormData;
    expect(form.get('code')).toBe('nkjv');
    expect(form.get('kind')).toBe('epub');
    expect(form.get('edition')).toBe('nkjv-tmn');
    expect(form.get('import_notes')).toBe('true');
    expect((form.get('file') as File).name).toBe('nkjv.epub');
  });

  it('will not upload before a file and a code are chosen', async () => {
    renderPanel();
    expect(await screen.findByTestId('bible-admin-upload-go')).toBeDisabled();
  });

  it('shows the importer’s log after a successful install', async () => {
    renderPanel();
    await userEvent.type(await screen.findByTestId('bible-admin-path-code'), 'esv');
    await userEvent.type(screen.getByTestId('bible-admin-path-file'), '/data/bible/esv.pdf');
    await userEvent.click(screen.getByTestId('bible-admin-path-go'));
    const notice = await screen.findByTestId('bible-admin-notice');
    expect(notice.textContent).toContain('31,102 verses across 66 books');
    expect(screen.getByTestId('bible-admin-log').textContent).toContain('Exodus: 40 chapters');
  });

  it('shows a refusal in full and does not call it a success', async () => {
    apiMock.runBibleImport.mockResolvedValue({
      source: '/data/bible/nkjv.pdf',
      kind: 'pdf',
      code: 'nkjv',
      name: '',
      provider: '',
      provider_id: '',
      status: 'failed',
      message: 'Exodus is missing from that PDF.',
      verse_count: null,
      book_count: null,
      note_count: null,
      log: ['Refused: Exodus is missing from that PDF.'],
      duration_ms: 900,
      created_at: '2026-01-04T00:00:00',
    });
    renderPanel();
    await userEvent.type(await screen.findByTestId('bible-admin-path-code'), 'nkjv');
    await userEvent.type(screen.getByTestId('bible-admin-path-file'), '/data/bible/nkjv.pdf');
    await userEvent.click(screen.getByTestId('bible-admin-path-go'));
    expect(await screen.findByTestId('bible-admin-notice')).toHaveTextContent('Exodus is missing');
    expect(screen.getByTestId('bible-admin-log')).toHaveTextContent('Refused:');
  });

  it('surfaces an upstream error message rather than a bare failure', async () => {
    apiMock.runBibleImport.mockRejectedValue(new Error('BIBLE_IMPORT_DIR is not configured.'));
    renderPanel();
    await userEvent.type(await screen.findByTestId('bible-admin-path-code'), 'esv');
    await userEvent.type(screen.getByTestId('bible-admin-path-file'), '/data/bible/esv.pdf');
    await userEvent.click(screen.getByTestId('bible-admin-path-go'));
    expect(await screen.findByTestId('bible-admin-notice')).toHaveTextContent(
      'BIBLE_IMPORT_DIR is not configured.',
    );
  });

  it('shows the import history newest first', async () => {
    renderPanel(
      catalogue({
        runs: [
          { source: 'a.epub', kind: 'epub', code: 'nkjv', name: '', provider: '', provider_id: '', status: 'failed', message: 'Refused: Psalms is missing', verse_count: null, book_count: null, note_count: null, log: [], duration_ms: 10, created_at: '2026-01-02T00:00:00' },
          { source: 'b.json', kind: 'json', code: 'kjv', name: '', provider: '', provider_id: '', status: 'succeeded', message: 'Installed King James Version', verse_count: 31102, book_count: 66, note_count: null, log: [], duration_ms: 20, created_at: '2026-01-01T00:00:00' },
        ],
      }),
    );
    await waitFor(() => expect(screen.getAllByTestId('bible-admin-run-failed')).toHaveLength(1));
    expect(screen.getAllByTestId('bible-admin-run-succeeded')).toHaveLength(1);
    expect(screen.getByTestId('bible-admin-run-failed').textContent).toContain('Refused: Psalms is missing');
  });

  it('offers a retry when the catalogue cannot be read', async () => {
    renderPanel(new Error('Bible service unavailable'));
    expect(await screen.findByText('Bible service unavailable')).toBeTruthy();
    expect(screen.getByText('Try again')).toBeTruthy();
  });
});
const ESTIMATE_BASE: BibleProviderEstimate = {
  translation_id: 'ESV',
  name: 'English Standard Version',
  books: 66,
  chapters: 1189,
  cached: 509,
  remaining: 680,
  calls: 680,
  cache_directory: '/data/bible/provider-cache/api.bible/ESV-abc123',
  budget: 1200,
  within_budget: true,
  cache_enabled: true,
};

function estimate(over: Partial<BibleProviderEstimate> = {}): BibleProviderEstimate {
  return { ...ESTIMATE_BASE, ...over };
}

describe('what a provider import will cost', () => {
  beforeEach(() => {
    apiMock.getBibleImports.mockResolvedValue(catalogue());
    apiMock.getBibleProviderTranslations.mockResolvedValue({
      provider: PROVIDER_OK,
      translations: [
        { id: 'ESV', name: 'English Standard Version', language: 'eng', license_class: 'licensed', rights_holder: 'Crossway', note: '' },
      ],
      count: 1,
    });
  });

  async function openProvider() {
    renderPanel();
    await userEvent.click(await screen.findByTestId('bible-admin-provider-open-api.bible'));
    await screen.findByTestId('bible-admin-cost-ESV');
  }

  it('asks nothing about cost until the question is asked', async () => {
    apiMock.getBibleProviderEstimate.mockResolvedValue(estimate());
    await openProvider();
    expect(apiMock.getBibleProviderEstimate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId('bible-admin-cost-ESV'));
    expect(await screen.findByText(/680 of 1,189 chapters are not cached/)).toBeTruthy();
    expect(apiMock.getBibleProviderEstimate).toHaveBeenCalledWith('api.bible', 'ESV');
  });

  it('says a fully cached translation costs nothing', async () => {
    apiMock.getBibleProviderEstimate.mockResolvedValue(
      estimate({ cached: 1189, remaining: 0, calls: 0 }),
    );
    await openProvider();
    await userEvent.click(screen.getByTestId('bible-admin-cost-ESV'));
    expect(await screen.findByText(/Already cached: all 1,189 chapters are on disk/)).toBeTruthy();
  });

  it('warns that caching is off, because then nothing is kept', async () => {
    apiMock.getBibleProviderEstimate.mockResolvedValue(
      estimate({ cache_enabled: false, cache_warning: 'BIBLE_PROVIDER_CACHE is not set, so nothing will be kept.' }),
    );
    await openProvider();
    await userEvent.click(screen.getByTestId('bible-admin-cost-ESV'));
    expect(await screen.findByText(/BIBLE_PROVIDER_CACHE is not set/)).toBeTruthy();
  });

  it('disables Install and says so when the run would blow the budget', async () => {
    apiMock.getBibleProviderEstimate.mockResolvedValue(estimate({ budget: 500, within_budget: false }));
    await openProvider();
    await userEvent.click(screen.getByTestId('bible-admin-cost-ESV'));
    expect(await screen.findByText(/The limit for one run is 500, so this would be refused/)).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByText('Install').closest('button')).toBeDisabled(),
    );
  });

  it('says there is no limit when none is configured', async () => {
    apiMock.getBibleProviderEstimate.mockResolvedValue(estimate({ budget: null, within_budget: true }));
    await openProvider();
    await userEvent.click(screen.getByTestId('bible-admin-cost-ESV'));
    expect(await screen.findByText(/There is no request limit configured/)).toBeTruthy();
  });

  it('offers a one-book test that installs nothing', async () => {
    apiMock.getBibleProviderEstimate.mockResolvedValue(estimate());
    await openProvider();
    expect(screen.queryByTestId('bible-admin-dry-run-ESV')).toBeNull();
    await userEvent.click(screen.getByTestId('bible-admin-cost-ESV'));
    await userEvent.click(await screen.findByTestId('bible-admin-dry-run-ESV'));
    await waitFor(() =>
      expect(apiMock.runBibleImport).toHaveBeenCalledWith({
        code: 'esv',
        kind: 'json',
        name: 'English Standard Version',
        provider: 'api.bible',
        provider_id: 'ESV',
        dry_run: true,
      }),
    );
  });
});
