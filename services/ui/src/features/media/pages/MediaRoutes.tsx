import { Route, Routes } from 'react-router-dom';
import { MediaScreen } from './MediaScreen';
import ListenNowPage from './ListenNowPage';
import SearchPage from './SearchPage';
import LibraryPage from './LibraryPage';
import CollectionPage from './CollectionPage';
import BookPage from './BookPage';
import PodcastPage from './PodcastPage';

/** The media section's routes (§4.1), all inside the shared `MediaScreen` frame. */
export default function MediaRoutes() {
  return (
    <Routes>
      <Route element={<MediaScreen />}>
        <Route index element={<ListenNowPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="library/:tab" element={<LibraryPage />} />
        <Route path="album/:provider/:id" element={<CollectionPage key="album" kind="album" />} />
        <Route path="artist/:provider/:id" element={<CollectionPage key="artist" kind="artist" />} />
        <Route path="playlist/:provider/:id" element={<CollectionPage key="playlist" kind="playlist" />} />
        <Route path="book/:id" element={<BookPage />} />
        <Route path="podcast/:id" element={<PodcastPage key="abs" />} />
        <Route path="podcast/:provider/:id" element={<PodcastPage key="ma" />} />
      </Route>
    </Routes>
  );
}
