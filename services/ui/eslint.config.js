import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'android']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    // Media follows the active theme and the artwork accent (§6.5): no fixed
    // hues in its classes. Use var(--site-*) / var(--media-accent) tokens.
    files: ['src/features/media/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: 'Literal[value=/\\b(cyan|purple|pink|amber|fuchsia|violet)-\\d{2,3}/]',
          message: 'Use theme tokens (var(--site-*), var(--media-accent)) instead of fixed colour classes in features/media.',
        },
        {
          selector: 'TemplateElement[value.raw=/\\b(cyan|purple|pink|amber|fuchsia|violet)-\\d{2,3}/]',
          message: 'Use theme tokens (var(--site-*), var(--media-accent)) instead of fixed colour classes in features/media.',
        },
      ],
    },
  },
])
