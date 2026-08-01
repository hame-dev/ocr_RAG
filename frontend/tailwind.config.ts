import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        // Latin and Arabic metrics actually match in IBM Plex Sans Arabic,
        // which matters for bilingual tables.
        sans: ["var(--font-ui)", "system-ui", "sans-serif"],
        // Naskh is far more readable than UI faces for long transcribed text.
        doc: ["var(--font-doc)", "serif"],
      },
    },
  },
  plugins: [],
};

export default config;
