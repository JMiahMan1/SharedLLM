import { build } from 'vite';

async function runBuild() {
  try {
    await build();
    console.log('Build successful!');
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
