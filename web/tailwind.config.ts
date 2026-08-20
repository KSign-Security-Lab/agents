import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#111827", soft: "#374151", muted: "#6b7280" },
        line: "#e5e7eb",
        accent: { DEFAULT: "#2563eb", soft: "#eff6ff" },
        cite: { DEFAULT: "#b45309", soft: "#fef3c7" },
      },
      fontFamily: {
        sans: ["Pretendard", "Apple SD Gothic Neo", "Malgun Gothic",
               "Noto Sans KR", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;
