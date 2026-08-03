import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        deep: "var(--bg-deep)",
        surface: "var(--bg-surface)",
        elevated: "var(--bg-elevated)",
        border: "var(--border)",
        primary: "var(--text-primary)",
        secondary: "var(--text-secondary)",
        muted: "var(--text-muted)",
        agent: {
          1: "var(--agent-1)",
          2: "var(--agent-2)",
          3: "var(--agent-3)",
          4: "var(--agent-4)",
        },
        sev: {
          critical: "var(--sev-critical)",
          high: "var(--sev-high)",
          medium: "var(--sev-medium)",
          low: "var(--sev-low)",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        // Spec scale: 11px labels → 12px code → 13px body → 14px titles → 20px stats
        label: ["11px", { lineHeight: "16px", letterSpacing: "0.04em" }],
        code: ["12px", { lineHeight: "18px" }],
        body: ["13px", { lineHeight: "20px" }],
        title: ["14px", { lineHeight: "20px" }],
        stat: ["20px", { lineHeight: "26px" }],
      },
    },
  },
  plugins: [],
};

export default config;
