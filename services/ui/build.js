import { build } from 'vite';

async function runBuild() {
  try {
    await build();
    console.log('Build successful!');
  } catch (err) {
    console.error('--- BUILD FAILED ---');
    if (err.errors) {
      console.log('INTERNAL ERRORS:', JSON.stringify(err.errors, null, 2));
    }
    console.dir(err, { depth: null });
    process.exit(1);
  }
}

runBuild();
