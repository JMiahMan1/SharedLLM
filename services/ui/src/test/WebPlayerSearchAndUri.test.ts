import { describe, it, expect, vi, beforeEach } from 'vitest';
import { api, apiClient } from '../services/api';
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
      })),
    },
  };
});

describe('Web Player Search and URI creation logic', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('api.searchMusicAssistant()', () => {
    it('should generate correct URL with query, default limit, and default library_only=true', async () => {
      const mockResponse = {
        status: 'SUCCESS',
        results: [
          { name: 'Track 1', uri: 'spotify://track/1', type: 'track' }
        ]
      };
      vi.mocked(apiClient.get).mockResolvedValue({ data: mockResponse } as AxiosResponse);

      const res = await api.searchMusicAssistant('Miles Davis');
      expect(apiClient.get).toHaveBeenCalledWith('/api/media/music-assistant/search?query=Miles+Davis&limit=30&library_only=true');
      expect(res).toEqual(mockResponse);
    });

    it('should include optional mediaType, limit, and libraryOnly parameters when provided', async () => {
      const mockResponse = { status: 'SUCCESS', results: [] };
      vi.mocked(apiClient.get).mockResolvedValue({ data: mockResponse } as AxiosResponse);

      await api.searchMusicAssistant('Coltrane', 'track', 10, false);
      expect(apiClient.get).toHaveBeenCalledWith('/api/media/music-assistant/search?query=Coltrane&limit=10&library_only=false&media_type=track');
    });
  });

  describe('URI Formatter/Transformation Logic', () => {
    it('should correctly strip prefixes and add audiobookshelf:// prefix for ABS books', () => {
      const bookId = 'abs-book-123';
      const idClean = bookId.replace('abs-', '').replace('ma-', '');
      const mediaUri = `audiobookshelf://${idClean}`;
      
      expect(idClean).toBe('book-123');
      expect(mediaUri).toBe('audiobookshelf://book-123');
    });

    it('should leave generic query URIs intact for Direct Media URIs/Music Assistant', () => {
      const trackUri = 'spotify://track/abc123xyz';
      const cleanUri = trackUri.replace('abs-', '').replace('ma-', '');
      
      expect(cleanUri).toBe('spotify://track/abc123xyz');
    });
  });
});
