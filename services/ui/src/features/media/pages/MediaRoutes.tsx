import { Route, Routes } from 'react-router-dom';
import { MediaScreen } from './MediaScreen';
import ListenNowPage from './ListenNowPage';
import SearchPage from './SearchPage';
import LibraryPage from './LibraryPage';

/** The media section's routes (§4.1), all inside the shared `MediaScreen` frame. */
export default function MediaRoutes() {
  return (
    <Routes>
      <Route element={<MediaScreen />}>
        <Route index element={<ListenNowPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="library/:tab" element={<LibraryPage />} />
      </Route>
    </Routes>
  );
}
