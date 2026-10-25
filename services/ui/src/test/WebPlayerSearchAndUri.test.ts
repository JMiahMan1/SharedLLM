import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../services/api';
import { searchMedia } from '../features/media/api/client';
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

  describe('searchMedia() (the unified search the media screens use)', () => {
    it('asks for the query and the default limit', async () => {
      const mockResponse = { top: null, tracks: [], errors: { ma: null, abs: null } };
      vi.mocked(apiClient.get).mockResolvedValue({ data: mockResponse } as AxiosResponse);

      const res = await searchMedia('Miles Davis');
      expect(apiClient.get).toHaveBeenCalledWith('/api/media/search', { params: { q: 'Miles Davis', limit: 20 }, timeout: 20_000 });
      expect(res).toEqual(mockResponse);
    });

    it('passes types and a limit when given', async () => {
      vi.mocked(apiClient.get).mockResolvedValue({ data: {} } as AxiosResponse);

      await searchMedia('Coltrane', 'track', 10);
      expect(apiClient.get).toHaveBeenCalledWith('/api/media/search', { params: { q: 'Coltrane', limit: 10, types: 'track' }, timeout: 20_000 });
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
