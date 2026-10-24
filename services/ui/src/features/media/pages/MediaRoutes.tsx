import { Route, Routes } from 'react-router-dom';
import { MediaScreen } from './MediaScreen';
import ListenNowPage from './ListenNowPage';

/** The media section's routes (§4.1), all inside the shared `MediaScreen` frame. */
export default function MediaRoutes() {
  return (
    <Routes>
      <Route element={<MediaScreen />}>
        <Route index element={<ListenNowPage />} />
      </Route>
    </Routes>
  );
}
