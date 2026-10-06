/**
 * P2-T33: validate the UI's media fixtures against the gateway's generated
 * JSON Schemas.
 *
 * `__generated__/schemas.json` is dumped from `services/gateway/media_models.py`
 * by `services/gateway/tests/test_media_schema_contract.py` (regenerate with
 * `UPDATE_MEDIA_SCHEMAS=1 pytest tests/test_media_schema_contract.py -q`).
 * If a gateway field is added, renamed or retyped, either this test fails
 * against the fixtures or the pytest drift guard fails — both mean the two
 * sides have diverged.
 */
import { describe, expect, it } from 'vitest'
import Ajv2020 from 'ajv/dist/2020'

import schemas from './__generated__/schemas.json'
import type {
  AbsProgressRequest,
  AbsProgressResponse,
  MediaFavoritesResponse,
  MediaHomeResponse,
  MediaItem,
  MediaItemChild,
  MediaItemDetail,
  MediaLibrary,
  MediaLibraryResponse,
  MediaSearchResponse,
  MediaSchemaName,
} from './types'

const track: MediaItem = {
  uri: 'library://track/1',
  name: 'So What',
  media_type: 'track',
  artist: 'Miles Davis',
  album: 'Kind of Blue',
  image: 'http://ma.local:8095/imageproxy/1',
  duration: 545.0,
  version: '',
  favorite: true,
}

const book: MediaItem = {
  uri: 'abs://book-1',
  name: 'The Hobbit',
  media_type: 'audiobook',
  artist: 'J.R.R. Tolkien',
  album: '',
  image: '/api/items/book-1/cover',
  duration: 54000,
  version: '',
  favorite: null,
}

const emptyErrors = { ma: null, abs: null }

const fixtures: Record<MediaSchemaName, unknown> = {
  MediaErrorInfo: { ma: null, abs: 'Audiobookshelf is unreachable' },
  MediaItem: track,
  MediaLibrary: { id: 'lib-1', name: 'Audiobooks', media_type: 'book' } as MediaLibrary,
  MediaHomeResponse: {
    recent: [track, book],
    continue: [book],
    playlists: [track],
    favorites: [track],
    radio: [],
    errors: emptyErrors,
  } as MediaHomeResponse,
  MediaSearchResponse: {
    top: track,
    tracks: [track],
    artists: [],
    albums: [],
    playlists: [],
    audiobooks: [book],
    podcasts: [],
    authors: [],
    errors: emptyErrors,
  } as MediaSearchResponse,
  MediaItemChild: {
    uri: 'abs://book-1#chapter-0',
    name: 'An Unexpected Party',
    media_type: 'chapter',
    image: '',
    duration: 1200.5,
    index: 0,
  } as MediaItemChild,
  MediaItemDetail: {
    uri: 'library://playlist/6',
    name: 'Focus',
    media_type: 'playlist',
    artist: '',
    album: '',
    image: 'http://ma.local:8095/imageproxy/6',
    duration: null,
    description: '',
    children: [
      {
        uri: 'library://track/1',
        name: 'So What',
        media_type: 'track',
        image: '',
        duration: 545.0,
        index: 1,
      },
    ],
    errors: emptyErrors,
  } as MediaItemDetail,
  MediaLibraryResponse: {
    tab: 'tracks',
    items: [track],
    libraries: [],
    offset: 0,
    limit: 50,
    errors: emptyErrors,
  } as MediaLibraryResponse,
  MediaFavoritesResponse: {
    items: [track],
    errors: emptyErrors,
  } as MediaFavoritesResponse,
  AbsProgressRequest: {
    item_id: 'book-1',
    episode_id: 'ep-1',
    current_time: 12.5,
    duration: 100.0,
    is_finished: false,
  } as AbsProgressRequest,
  AbsProgressResponse: {
    status: 'SUCCESS',
    item_id: 'book-1',
    episode_id: null,
    message: 'Progress saved',
  } as AbsProgressResponse,
}

const schemaNames = Object.keys(schemas) as MediaSchemaName[]

describe('media schema contract', () => {
  it('has a fixture for every generated schema', () => {
    expect(Object.keys(fixtures).sort()).toEqual(schemaNames.slice().sort())
  })

  describe.each(schemaNames)('%s', (name) => {
    it('validates its fixture', () => {
      const ajv = new Ajv2020({ allErrors: true, strict: false })
      const validate = ajv.compile(
        (schemas as Record<string, object>)[name],
      )
      const ok = validate(fixtures[name])
      expect(
        ok,
        `${name} fixture failed: ${JSON.stringify(validate.errors, null, 2)}`,
      ).toBe(true)
    })
  })
})
