import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        obsidian: "#070B12",
        panel: "#0E1522",
        border: "#1E293B",
        emeraldSignal: "#10B981",
        amberWarn: "#F59E0B",
        crimsonStop: "#EF4444",
      },
    },
  },
  plugins: [],
};
export default config;
