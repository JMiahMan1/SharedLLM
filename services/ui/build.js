import { build } from 'vite';

async function runBuild() {
  try {
    await build();
    console.log('Build successful!');
  } catch (err) {
    console.error('--- BUILD FAILED ---');
    console.dir(err, { depth: null });
    process.exit(1);
  }
}

runBuild();
