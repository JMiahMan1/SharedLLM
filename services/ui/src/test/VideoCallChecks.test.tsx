import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import ConcernsCard from '../components/member/ConcernsCard';
import VideoCallsCard from '../components/guardian/VideoCallsCard';
import { api } from '../services/api';
import { captureProblem, parseList, videoCallAlertText, videoCallLine, withVideoCalls } from '../lib/videoCalls';
import type { GuardianConcern, ScreenTimeStatus, VideoCallState } from '../types/api';

const auth = vi.hoisted(() => ({
  user: { username: 'parent1', is_minor: false, family_role: 'father' } as {
    username: string;
    is_minor: boolean;
    is_admin?: boolean;
    family_role?: string;
  },
}));
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getScreenTimeStatus: vi.fn(),
      setScreenRules: vi.fn(),
      getMemberDevices: vi.fn(),
      getGuardianConcerns: vi.fn(),
      getGuardianGuidance: vi.fn(),
    },
  };
});

const VIDEO: VideoCallState = {
  scan: true, interval_s: 45, max_frames: 20, corroborate_frames: 2, block: false,
  apps: ['com.whatsapp', 'us.zoom.videomeetings'], sites: ['facetime.apple.com', 'meet.google.com'],
  custom_apps: false, custom_sites: false, labels: { 'com.whatsapp': 'WhatsApp', 'us.zoom.videomeetings': 'Zoom' },
};

function status(video: Partial<VideoCallState> = {}): ScreenTimeStatus {
  return {
    username: 'kid1', day: '2026-11-17', can_manage: true, technical: true, comms: true,
    rules: { photo_scan: true, daily_limit_min: 120, paused: true },
    video_calls: { ...VIDEO, ...video },
  } as unknown as ScreenTimeStatus;
}

const FRAME: GuardianConcern = {
  id: 'v1', category: 'sexual', label: 'Sexual content', level: 'severe', excerpt: 'Possible nudity',
  feature: 'video_call', action: 'video_call_frame', item: 'Chrome (facetime.apple.com)', has_image: true,
  at: '2026-11-17T15:42:00-07:00',
  video_call: {
    app: 'Chrome', site: 'facetime.apple.com', label: 'Chrome (facetime.apple.com)',
    call_started_at: '2026-11-17T15:41:00-07:00', frame_index: 0, call_id: 'call-a',
  },
  alert: { state: 'unsure', confirmed_frames: 1, needed: 2 },
  review: { by: 'vision', verdict: 'unsure', reason: 'skin visible', second_check: { confirmed: false, reason: 'ordinary clothing' } },
};

function wrap(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { username: 'parent1', is_minor: false, family_role: 'father' };
  vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status());
  vi.mocked(api.setScreenRules).mockResolvedValue({ rules: {} });
  vi.mocked(api.getMemberDevices).mockResolvedValue({ username: 'kid1', devices: [], tamper: [], offline_code: {} });
  vi.mocked(api.getGuardianConcerns).mockResolvedValue({ concerns: [FRAME] });
  vi.mocked(api.getGuardianGuidance).mockResolvedValue({ prompts: [] } as never);
});

describe('video call helpers', () => {
  it('labels a frame with the call and whether it was sent', () => {
    expect(videoCallLine(FRAME)).toMatch(/^Video call · Chrome \(facetime\.apple\.com\) · /);
    expect(videoCallAlertText(FRAME)).toBe('Unsure, not sent');
    expect(videoCallAlertText({ ...FRAME, alert: { state: 'sent', confirmed_frames: 2, needed: 2 } })).toMatch(/parents notified/);
    expect(videoCallLine({ ...FRAME, feature: 'media' })).toBeNull();
  });

  it('keeps the rest of the rules and drops a key set back to the default', () => {
    const rules = { daily_limit_min: 60, paused: true, video_calls: { block: false, apps: ['com.example.chat'] } };
    expect(withVideoCalls(rules, { block: true, apps: undefined })).toEqual({ daily_limit_min: 60, video_calls: { block: true } });
  });

  it('says why a phone cannot check calls, and nothing when it can', () => {
    expect(captureProblem('ready', 'ok')).toBeNull();
    expect(captureProblem(undefined, undefined)).toBeNull();
    expect(captureProblem('guard_off')).toMatch(/guard is off/);
    expect(captureProblem('no_screenshot_capability')).toMatch(/not been allowed to take screenshots/);
    expect(captureProblem('ready', 'blank_screen')).toMatch(/hid its screen/);
    expect(parseList('a.com, b.com\n\na.com')).toEqual(['a.com', 'b.com']);
  });
});

describe('ConcernsCard with a video call frame', () => {
  it('shows the call, the unsent state, both checks, review, export and the block button', async () => {
    wrap(<ConcernsCard username="kid1" name="Kid One" />);
    expect(await screen.findByTestId('concern-video-call')).toHaveTextContent('Video call · Chrome (facetime.apple.com)');
    expect(screen.getByTestId('concern-alert-state')).toHaveTextContent('Unsure, not sent');
    expect(screen.getByTestId('concern-second-check')).toHaveTextContent('did not agree. ordinary clothing');
    expect(screen.getByTestId('concern-review')).toBeInTheDocument();
    expect(screen.getByTestId('concern-image-export')).toBeInTheDocument();

    fireEvent.click(await screen.findByTestId('concern-block-video-calls'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledTimes(1));
    const [user, saved] = vi.mocked(api.setScreenRules).mock.calls[0];
    expect(user).toBe('kid1');
    expect(saved.video_calls).toEqual({ block: true });
    expect(saved.daily_limit_min).toBe(120);
    expect(saved.paused).toBeUndefined();
  });

  it('says video calls are blocked instead of offering the button again', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ block: true }));
    wrap(<ConcernsCard username="kid1" name="Kid One" />);
    expect(await screen.findByTestId('concern-video-blocked')).toHaveTextContent('Video calls are blocked for Kid One.');
    expect(screen.queryByTestId('concern-block-video-calls')).not.toBeInTheDocument();
  });

  it('never offers the block to someone who is not a parent', async () => {
    auth.user = { username: 'admin1', is_minor: false, is_admin: true };
    wrap(<ConcernsCard username="kid1" name="Kid One" />);
    expect(await screen.findByTestId('concern-video-call')).toBeInTheDocument();
    expect(screen.queryByTestId('concern-block-video-calls')).not.toBeInTheDocument();
  });
});

describe('VideoCallsCard', () => {
  it('saves the checks, the interval, the corroboration count and the block switch', async () => {
    wrap(<VideoCallsCard username="kid1" name="Kid One" />);
    expect(await screen.findByText(/two separate checks agree on at least 2 screenshots/)).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('video-call-interval'), { target: { value: '60' } });
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.setScreenRules).mock.calls[0][1].video_calls).toEqual({ interval_s: 60 });

    fireEvent.change(screen.getByTestId('video-call-corroborate'), { target: { value: '3' } });
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.setScreenRules).mock.calls[1][1].video_calls).toEqual({ corroborate_frames: 3 });

    fireEvent.click(screen.getByTestId('toggle-video-call-block'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledTimes(3));
    expect(vi.mocked(api.setScreenRules).mock.calls[2][1].video_calls).toEqual({ block: true });

    fireEvent.click(screen.getByTestId('toggle-video-call-scan'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledTimes(4));
    expect(vi.mocked(api.setScreenRules).mock.calls[3][1].video_calls).toEqual({ scan: false });
  });

  it('edits the lists and goes back to the standard ones', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status({ custom_sites: true, sites: ['call.example.org'] }));
    wrap(<VideoCallsCard username="kid1" name="Kid One" />);
    const sites = await screen.findByTestId('video-call-sites');
    expect(sites).toHaveValue('call.example.org');
    fireEvent.change(sites, { target: { value: 'call.example.org\nmeet.example.com' } });
    fireEvent.click(screen.getByTestId('video-call-lists-save'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.setScreenRules).mock.calls[0][1].video_calls).toEqual({ sites: ['call.example.org', 'meet.example.com'] });

    fireEvent.click(screen.getByTestId('video-call-lists-reset'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.setScreenRules).mock.calls[1][1].video_calls).toEqual({});
  });

  it('tells the parent when a phone cannot take call screenshots', async () => {
    vi.mocked(api.getMemberDevices).mockResolvedValue({
      username: 'kid1',
      devices: [{ device_key: 'd1', label: 'Kid phone', kind: 'phone', capabilities: { guard: { videoCallCapture: 'no_screenshot_capability' } } }],
      tamper: [],
      offline_code: {},
    } as never);
    wrap(<VideoCallsCard username="kid1" name="Kid One" />);
    expect(await screen.findByTestId('video-calls-unsupported')).toHaveTextContent(
      'Kid phone: The screen guard has not been allowed to take screenshots',
    );
  });
});
