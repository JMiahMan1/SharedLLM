import { build } from 'vite';
import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';
import JSZip from 'jszip';

async function packageOtaBundle(distDir) {
  try {
    let gitSha = process.env.GIT_SHA;
    if (!gitSha) {
      try {
        gitSha = execSync('git rev-parse --short HEAD').toString().trim();
      } catch {
        gitSha = 'unknown';
      }
    }

    const versionMeta = {
      version: '1.2.0',
      git_sha: gitSha,
      build_timestamp: new Date().toISOString(),
      release_notes: 'Jarvis OS Over-The-Air Update',
    };

    fs.writeFileSync(path.join(distDir, 'version.json'), JSON.stringify(versionMeta, null, 2));

    const zip = new JSZip();
    function addFolder(dir, zipNode) {
      const items = fs.readdirSync(dir);
      for (const item of items) {
        if (item === 'bundle.zip') continue;
        const itemPath = path.join(dir, item);
        const stat = fs.statSync(itemPath);
        if (stat.isDirectory()) {
          const sub = zipNode.folder(item);
          addFolder(itemPath, sub);
        } else {
          zipNode.file(item, fs.readFileSync(itemPath));
        }
      }
    }

    addFolder(distDir, zip);
    const content = await zip.generateAsync({
      type: 'nodebuffer',
      compression: 'DEFLATE',
      compressionOptions: { level: 6 }
    });

    const zipPath = path.join(distDir, 'bundle.zip');
    fs.writeFileSync(zipPath, content);
    console.log(`[OTA] Packaged bundle.zip (${(content.length / 1024 / 1024).toFixed(2)} MB) for version ${gitSha}`);
  } catch (e) {
    console.warn('[OTA] Warning: Failed to package OTA bundle.zip:', e);
  }
}

async function runBuild() {
  try {
    await build();
    console.log('Build successful!');
    await packageOtaBundle(path.resolve('dist'));
  } catch (err) {
    console.error('--- BUILD FAILED ---');
    console.log('Error Name:', err.name);
    console.log('Error Message:', err.message);
    if (err.errors) {
      console.log('Internal Errors:', JSON.stringify(err.errors, null, 2));
    }
    // Log all properties including non-enumerable ones
    for (const key of Object.getOwnPropertyNames(err)) {
      console.log(`Property [${key}]:`, err[key]);
    }
    process.exit(1);
  }
}

runBuild();
