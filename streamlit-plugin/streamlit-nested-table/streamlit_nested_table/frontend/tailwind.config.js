/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}", "./public/index.html"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#ffffff",
          muted: "#f8fafc",
          border: "#e2e8f0",
        },
        ink: {
          DEFAULT: "#0f172a",
          muted: "#64748b",
          faint: "#94a3b8",
        },
        accent: {
          DEFAULT: "#2563eb",
          soft: "#dbeafe",
        },
      },
      boxShadow: {
        table: "0 1px 3px 0 rgb(15 23 42 / 0.06), 0 1px 2px -1px rgb(15 23 42 / 0.06)",
      },
    },
  },
  plugins: [],
}
