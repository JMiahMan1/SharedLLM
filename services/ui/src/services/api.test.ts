import { describe, it, expect, vi, beforeEach } from 'vitest';
import { api, apiClient } from './api';
import { AxiosResponse } from 'axios';

vi.mock('axios', async () => {
  const actual = await vi.importActual('axios');
  return {
    ...actual,
    default: {
      create: vi.fn(() => ({
        interceptors: {
          request: { use: vi.fn(), eject: vi.fn() },
          response: { use: vi.fn(), eject: vi.fn() },
        },
        post: vi.fn(),
        get: vi.fn(),
        patch: vi.fn(),
        delete: vi.fn(),
      })),
    },
  };
});

describe('api service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('login should call /api/auth/login', async () => {
    const mockData = { api_key: 'test-token', username: 'testuser', is_admin: true };
    vi.mocked(apiClient.post).mockResolvedValue({ data: mockData } as AxiosResponse);

    const result = await api.login('testuser', 'password');
    expect(apiClient.post).toHaveBeenCalledWith('/api/auth/login', { username: 'testuser', password: 'password' });
    expect(result).toEqual(mockData);
  });

  it('getMe should call /api/users/me', async () => {
    const mockProfile = { id: '1', username: 'testuser', display_name: 'Test User', is_admin: true };
    vi.mocked(apiClient.get).mockResolvedValue({ data: mockProfile } as AxiosResponse);

    const result = await api.getMe();
    expect(apiClient.get).toHaveBeenCalledWith('/api/users/me');
    expect(result.full_name).toEqual('Test User');
    expect(result.role).toEqual('admin');
  });

  it('getWorkspaces should normalize object responses from the backend', async () => {
    const mockWorkspaces = [{ id: 'ws1', display_name: 'SharedLLM', local_path: 'SharedLLM', sync_mode: 'local_git_authoritative', scope: 'user', capabilities: [], auto_pull_enabled: false }];
    vi.mocked(apiClient.get).mockResolvedValue({ data: { status: 'SUCCESS', workspaces: mockWorkspaces } } as AxiosResponse);

    const result = await api.getWorkspaces();
    expect(apiClient.get).toHaveBeenCalledWith('/api/workspaces');
    expect(result).toEqual(mockWorkspaces);
  });

  it('getWorkspaces should preserve legacy array responses', async () => {
    const mockWorkspaces = [{ id: 'ws1', display_name: 'SharedLLM', local_path: 'SharedLLM', sync_mode: 'local_git_authoritative', scope: 'user', capabilities: [], auto_pull_enabled: false }];
    vi.mocked(apiClient.get).mockResolvedValue({ data: mockWorkspaces } as AxiosResponse);

    const result = await api.getWorkspaces();
    expect(result).toEqual(mockWorkspaces);
  });

  it('globalSearch should call /api/search', async () => {
    const mockResults = { answer: 'test answer', files: [] };
    vi.mocked(apiClient.get).mockResolvedValue({ data: mockResults } as AxiosResponse);

    const result = await api.globalSearch('test query');
    expect(apiClient.get).toHaveBeenCalledWith('/api/search?q=test%20query');
    expect(result).toEqual(mockResults);
  });

  it('createTimer should call the communication timer endpoint', async () => {
    const mockResult = { status: 'SUCCESS', message: 'Set timer', service: 'timer_add' };
    vi.mocked(apiClient.post).mockResolvedValue({ data: mockResult } as AxiosResponse);

    const result = await api.createTimer({ title: 'Kitchen Timer', duration_str: '10m' });
    expect(apiClient.post).toHaveBeenCalledWith('/api/communication/timers', { title: 'Kitchen Timer', duration_str: '10m' });
    expect(result).toEqual(mockResult);
  });

  it('getTalkMessages should call the talk message endpoint', async () => {
    const mockResult = { status: 'SUCCESS', message: 'Loaded 1 message(s).', service: 'talk_messages' };
    vi.mocked(apiClient.get).mockResolvedValue({ data: mockResult } as AxiosResponse);

    const result = await api.getTalkMessages('room-alpha', 25);
    expect(apiClient.get).toHaveBeenCalledWith('/api/communication/talk/messages?token=room-alpha&limit=25');
    expect(result).toEqual(mockResult);
  });

  it('sendTalkVoice should call the talk voice endpoint', async () => {
    const mockResult = { status: 'SUCCESS', message: 'Voice message sent.', service: 'talk_send_voice' };
    vi.mocked(apiClient.post).mockResolvedValue({ data: mockResult } as AxiosResponse);

    const result = await api.sendTalkVoice({
      token: 'room-alpha',
      audio_base64: 'data:audio/webm;base64,AAAA',
      mime_type: 'audio/webm',
      file_name: 'voice.webm',
      caption: 'Update',
    });
    expect(apiClient.post).toHaveBeenCalledWith('/api/communication/talk/voice', {
      token: 'room-alpha',
      audio_base64: 'data:audio/webm;base64,AAAA',
      mime_type: 'audio/webm',
      file_name: 'voice.webm',
      caption: 'Update',
    });
    expect(result).toEqual(mockResult);
  });
});
