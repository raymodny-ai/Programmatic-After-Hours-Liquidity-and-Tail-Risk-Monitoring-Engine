import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './src/app/**/*.{ts,tsx}',
    './src/components/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: '#0a0e1a',
          panel: '#0f1626',
          card: '#141c30',
        },
        accent: {
          cyan: '#22d3ee',
          amber: '#f59e0b',
          rose: '#f43f5e',
          emerald: '#10b981',
          violet: '#8b5cf6',
        },
        signal: {
          normal: '#10b981',
          watch: '#f59e0b',
          elevated: '#fb923c',
          high: '#f43f5e',
          critical: '#be123c',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.3s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
};

export default config;