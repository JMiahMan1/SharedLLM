import { registerPlugin } from '@capacitor/core';

/* Photos and videos on the phone's gallery: the runtime permission and the
   background safety scanner. Only a protected child's phone, with photo
   scanning kept on by a parent, reads anything; a downscaled thumbnail is
   sent to the family's server-side scanner and the image itself is not kept. */

export interface PhotoScanAccess {
  images: boolean;
  videos: boolean;
}

/** What one scanning pass did (`scanNow`, APK with PhotoScanWorker). */
export interface PhotoScanResult {
  ok: boolean;
  scanned: number;
  uploaded: number;
}

export interface PhotoScanPluginInterface {
  hasAccess(): Promise<PhotoScanAccess>;
  /** Ask Android for photo/video access; resolves with the answer. */
  requestAccess(): Promise<PhotoScanAccess>;
  /** Run the same pass the background worker runs, now. */
  scanNow(): Promise<PhotoScanResult>;
}

const webFallback: PhotoScanPluginInterface = {
  hasAccess: async () => ({ images: false, videos: false }),
  requestAccess: async () => ({ images: false, videos: false }),
  scanNow: async () => ({ ok: true, scanned: 0, uploaded: 0 }),
};

const PhotoScan = registerPlugin<PhotoScanPluginInterface>('PhotoScan', {
  web: () => webFallback,
});

export default PhotoScan;
